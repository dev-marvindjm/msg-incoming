import asyncio
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from sqlmodel import select

from app.core.config import settings
from app.core.database import async_session_maker
from app.domain.enums import PlatformType
from app.domain.models import User
from app.domain.schemas import WebhookMessageCreate
from app.services.emitter import broker_emitter
from app.services.filter_engine import filter_engine

logger = logging.getLogger("msg_ingestor.telethon")


class TelethonService:
    """
    Manages the MTProto connection via Telethon, listens to incoming NewMessage events
    across configured channels and groups, extracts metadata safely, and routes messages
    through the multi-tenant filter engine and broker emitter.
    """

    def __init__(self):
        self.client: Optional[TelegramClient] = None
        self._running: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _build_client(self) -> Optional[TelegramClient]:
        """Creates TelegramClient instance based on configuration."""
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            logger.info("Telethon not configured: TELEGRAM_API_ID or TELEGRAM_API_HASH missing.")
            return None

        # 1. StringSession takes priority for cloud/Docker deployments
        if settings.TELEGRAM_SESSION_STRING:
            logger.info("Using StringSession for Telethon MTProto client.")
            session = StringSession(settings.TELEGRAM_SESSION_STRING)
        else:
            # 2. File-based persistent session in designated directory
            sessions_dir = Path(settings.TELEGRAM_SESSIONS_DIR)
            sessions_dir.mkdir(parents=True, exist_ok=True)
            session_path = sessions_dir / settings.TELEGRAM_SESSION_NAME
            logger.info(f"Using persistent file session at {session_path}")
            session = str(session_path)

        client = TelegramClient(
            session=session,
            api_id=settings.TELEGRAM_API_ID,
            api_hash=settings.TELEGRAM_API_HASH,
        )
        return client

    async def start(self) -> None:
        """Starts the Telethon client and attaches event handlers."""
        if self._running:
            logger.warning("Telethon service is already running.")
            return

        self.client = self._build_client()
        if not self.client:
            logger.warning("Telethon client could not be initialized due to missing credentials.")
            return

        try:
            logger.info("Connecting Telethon MTProto client...")
            if settings.TELEGRAM_BOT_TOKEN:
                await self.client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
            else:
                await self.client.start()

            me = await self.client.get_me()
            logger.info(
                f"Telethon connected successfully as: {getattr(me, 'first_name', '')} "
                f"(@{getattr(me, 'username', 'no_username')}, ID: {getattr(me, 'id', 'unknown')})"
            )

            # Register NewMessage event handler
            self.client.add_event_handler(self._handle_new_message, events.NewMessage())
            self._running = True

        except Exception as exc:
            logger.error(f"Failed to start Telethon client: {exc}", exc_info=True)
            self._running = False

    async def stop(self) -> None:
        """Gracefully disconnects the Telethon MTProto client."""
        if self.client and self._running:
            logger.info("Disconnecting Telethon MTProto client...")
            try:
                await self.client.disconnect()
            except Exception as e:
                logger.warning(f"Error during Telethon disconnect: {e}")
            finally:
                self._running = False
                logger.info("Telethon client disconnected.")

    async def _handle_new_message(self, event: events.NewMessage.Event) -> None:
        """
        Event handler triggered when a new message arrives in any monitored chat or channel.
        Extracts metadata safely and dispatches into the multi-tenant filter engine.
        """
        try:
            # 1. Safe extraction of chat, sender, IDs, and usernames
            chat = await event.get_chat()
            sender = await event.get_sender()

            # Identify sender ID:
            # In Telegram channels, event.sender_id is often None; event.chat_id is the channel ID
            raw_sender_id = event.chat_id or (event.sender_id if event.sender_id is not None else 0)
            sender_id = str(raw_sender_id)

            sender_username = (
                getattr(sender, "username", None)
                or getattr(chat, "username", None)
            )

            raw_text = event.raw_text or event.message.message or ""
            if not raw_text.strip():
                return  # Skip purely empty media messages without captions

            external_message_id = str(event.message.id)

            metadata: Dict[str, Any] = {
                "chat_id": event.chat_id,
                "is_channel": getattr(event, "is_channel", False),
                "is_group": getattr(event, "is_group", False),
                "is_private": getattr(event, "is_private", False),
                "chat_title": getattr(chat, "title", None),
                "date": event.date.isoformat() if event.date else None,
                "views": getattr(event.message, "views", None),
                "forwards": getattr(event.message, "forwards", None),
            }

            logger.info(
                f"[Telethon] Ingested message msg_id={external_message_id} "
                f"from sender={sender_id} (@{sender_username}) in chat='{metadata.get('chat_title')}'"
            )

            # 2. Multi-tenant dispatch across active operators
            await self._dispatch_to_active_users(
                sender_id=sender_id,
                sender_username=sender_username,
                raw_text=raw_text,
                external_message_id=external_message_id,
                metadata_json=metadata,
            )

        except Exception as exc:
            logger.error(f"Error handling Telethon NewMessage event: {exc}", exc_info=True)

    async def _dispatch_to_active_users(
        self,
        sender_id: str,
        sender_username: Optional[str],
        raw_text: str,
        external_message_id: str,
        metadata_json: Dict[str, Any],
    ) -> None:
        """
        Routes the received Telegram message through each active user's filter rules
        and persists an independent, idempotent audit record per user.
        """
        async with async_session_maker() as session:
            try:
                # Fetch all active system operators
                users_res = await session.exec(select(User).where(User.is_active == True))  # noqa: E712
                active_users = users_res.all()

                for user in active_users:
                    payload = WebhookMessageCreate(
                        platform=PlatformType.TELEGRAM,
                        external_message_id=external_message_id,
                        sender_id=sender_id,
                        sender_username=sender_username,
                        raw_text=raw_text,
                        metadata_json=metadata_json,
                    )

                    res = await filter_engine.ingest_and_evaluate(
                        user_id=user.id,
                        payload=payload,
                        db=session,
                    )

                    if res.is_ok():
                        ingested_msg, should_forward, is_duplicate = res.unwrap()
                        if should_forward and not is_duplicate:
                            # Asynchronously dispatch to broker trading engine
                            asyncio.create_task(
                                broker_emitter.emit_to_trading_engine(
                                    user_id=user.id,
                                    message=ingested_msg,
                                )
                            )
            except Exception as e:
                logger.error(f"Error dispatching Telegram message to users: {e}", exc_info=True)

    def is_connected(self) -> bool:
        """Returns True if Telethon client is actively connected."""
        return bool(self.client and self.client.is_connected())


telethon_service = TelethonService()
