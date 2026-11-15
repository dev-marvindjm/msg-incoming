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

    def __init__(self, owner_email: Optional[str] = None):
        self.client: Optional[TelegramClient] = None
        self._running: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self.owner_email: Optional[str] = owner_email
        self._unscoped_warning_logged: bool = False

    def _build_client(self, force_file_session: bool = False) -> Optional[TelegramClient]:
        """Creates TelegramClient instance based on configuration."""
        if not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
            logger.info("Telethon not configured: TELEGRAM_API_ID or TELEGRAM_API_HASH missing.")
            return None

        sessions_dir = Path(settings.TELEGRAM_SESSIONS_DIR)
        sessions_dir.mkdir(parents=True, exist_ok=True)
        session_path = sessions_dir / settings.TELEGRAM_SESSION_NAME

        # If force_file_session or no StringSession is configured, use file-based session
        if force_file_session or not settings.TELEGRAM_SESSION_STRING:
            logger.info(f"Using persistent file session at {session_path}")
            session = str(session_path)
        else:
            # Check if file session exists on disk
            file_on_disk = session_path.with_suffix(".session")
            if file_on_disk.exists() and file_on_disk.stat().st_size > 0:
                logger.info(f"Found existing persistent file session at {file_on_disk}, using it.")
                session = str(session_path)
            else:
                logger.info("Using StringSession for Telethon MTProto client.")
                session = StringSession(settings.TELEGRAM_SESSION_STRING)

        client = TelegramClient(
            session=session,
            api_id=settings.TELEGRAM_API_ID,
            api_hash=settings.TELEGRAM_API_HASH,
        )
        return client

    async def get_or_create_client(self) -> Optional[TelegramClient]:
        """Returns the active Telethon client, initializing and connecting it if necessary."""
        if not self.client:
            self.client = self._build_client()
        if self.client:
            try:
                if not self.client.is_connected():
                    await self.client.connect()
            except Exception as e:
                logger.warning(f"Initial Telethon client connection failed ({e}), rebuilding with clean file session...")
                self.client = self._build_client(force_file_session=True)
                if self.client and not self.client.is_connected():
                    await self.client.connect()
        return self.client

    async def ensure_started(self) -> None:
        """Starts NewMessage listener if user is authorized and listener is not yet running."""
        if not self.client:
            return
        if not self._running and await self.client.is_user_authorized():
            self.client.add_event_handler(self._handle_new_message, events.NewMessage())
            self._running = True
            logger.info("Telethon NewMessage event listener registered and running.")

    async def start(self) -> None:
        """Starts the Telethon client and attaches event handlers."""
        if self._running:
            logger.warning("Telethon service is already running.")
            return

        if not self.client:
            self.client = self._build_client()
        if not self.client:
            logger.warning("Telethon client could not be initialized due to missing credentials.")
            return

        try:
            logger.info("Connecting Telethon MTProto client...")
            if not self.client.is_connected():
                await self.client.connect()
            if not await self.client.is_user_authorized():
                if settings.TELEGRAM_BOT_TOKEN:
                    logger.info("Authorizing Telethon client with Telegram Bot Token...")
                    await self.client.start(bot_token=settings.TELEGRAM_BOT_TOKEN)
                else:
                    logger.warning(
                        "Telethon MTProto client connected to Telegram servers, but session is not authorized yet. "
                        "To activate live incoming Telegram events, provide TELEGRAM_BOT_TOKEN or a valid TELEGRAM_SESSION_STRING / .session file in /app/sessions."
                    )
                    return

            me = await self.client.get_me()
            logger.info(
                f"Telethon connected and authorized as: {getattr(me, 'first_name', '')} "
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

            # Identify sender ID and chat ID:
            # In Telegram channels, event.sender_id is often None; event.chat_id is the channel ID
            raw_sender_id = event.chat_id or (event.sender_id if event.sender_id is not None else 0)
            sender_id = str(raw_sender_id)
            chat_id = str(event.chat_id) if event.chat_id is not None else sender_id

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
                f"from sender={sender_id} (@{sender_username}) in chat='{metadata.get('chat_title')}' (chat_id={chat_id})"
            )

            # 2. Multi-tenant / owner-scoped dispatch across active operators
            await self._dispatch_to_active_users(
                sender_id=sender_id,
                sender_username=sender_username,
                raw_text=raw_text,
                external_message_id=external_message_id,
                metadata_json=metadata,
                chat_id=chat_id,
            )

        except Exception as exc:
            logger.error(f"Error handling Telethon NewMessage event: {exc}", exc_info=True)

    async def _resolve_target_users(
        self,
        session: Any,
        owner_email: Optional[str] = None,
    ) -> List[User]:
        """
        Resolves the active user(s) to receive events from this Telethon session.
        If owner_email or settings.TELEGRAM_OWNER_EMAIL is configured, scopes dispatch
        exclusively to that active user. Otherwise, falls back to all active users
        and logs a one-time warning.
        """
        effective_owner_email = (
            owner_email or self.owner_email or settings.TELEGRAM_OWNER_EMAIL or ""
        ).strip() or None

        if effective_owner_email:
            stmt = select(User).where(
                User.is_active == True,  # noqa: E712
                User.email == effective_owner_email,
            )
            users_res = await session.exec(stmt)
            matched_users = list(users_res.all())
            if not matched_users:
                logger.warning(
                    f"TELEGRAM_OWNER_EMAIL='{effective_owner_email}' did not match any active user; skipping dispatch."
                )
            return matched_users

        if not self._unscoped_warning_logged:
            logger.warning(
                "TELEGRAM_OWNER_EMAIL is not configured; dispatching Telegram messages to ALL active users. "
                "Configure TELEGRAM_OWNER_EMAIL to scope this Telethon session to a single owner."
            )
            self._unscoped_warning_logged = True

        users_res = await session.exec(select(User).where(User.is_active == True))  # noqa: E712
        return list(users_res.all())

    async def _dispatch_to_active_users(
        self,
        sender_id: str,
        sender_username: Optional[str],
        raw_text: str,
        external_message_id: str,
        metadata_json: Dict[str, Any],
        chat_id: Optional[str] = None,
        owner_email: Optional[str] = None,
    ) -> None:
        """
        Routes the received Telegram message through target user(s)' filter rules
        and persists an independent, idempotent audit record per user.
        """
        resolved_chat_id = (
            str(chat_id)
            if chat_id is not None
            else (
                str(metadata_json["chat_id"])
                if metadata_json and metadata_json.get("chat_id") is not None
                else sender_id
            )
        )
        async with async_session_maker() as session:
            try:
                active_users = await self._resolve_target_users(session, owner_email=owner_email)

                for user in active_users:
                    payload = WebhookMessageCreate(
                        platform=PlatformType.TELEGRAM,
                        chat_id=resolved_chat_id,
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
                                    owner_email=user.email,
                                )
                            )
            except Exception as e:
                logger.error(f"Error dispatching Telegram message to users: {e}", exc_info=True)

    def is_connected(self) -> bool:
        """Returns True if Telethon client is actively connected."""
        return bool(self.client and self.client.is_connected())


telethon_service = TelethonService()
