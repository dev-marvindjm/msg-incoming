import asyncio
import logging
from typing import Any, Dict, Optional
from uuid import UUID
import httpx

from app.core.config import settings
from app.core.result import Err, Ok, Result
from app.domain.models import IngestedMessage

logger = logging.getLogger("msg_ingestor.emitter")


class BrokerEmitter:
    """
    Dispatches filtered and approved incoming messages to the downstream
    quant trading engine (`/api/v1/operations/ingest`).
    """

    MAX_ATTEMPTS = 3
    RETRY_BACKOFFS = (0.5, 1.0)

    def __init__(
        self,
        broker_url: Optional[str] = None,
        internal_api_key: Optional[str] = None,
        broker_api_key: Optional[str] = None,
    ):
        resolved_url = broker_url if broker_url is not None else settings.BROKER_API_URL
        self.broker_url = resolved_url.rstrip("/")
        self.internal_api_key = internal_api_key
        self.broker_api_key = broker_api_key
        self.headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"MSG-Incoming/{settings.APP_VERSION}",
        }
        if self.broker_api_key or settings.BROKER_API_KEY:
            self.headers["X-API-Key"] = self.broker_api_key or settings.BROKER_API_KEY or ""
        if self.internal_api_key or settings.INTERNAL_API_KEY:
            self.headers["X-Internal-Key"] = self.internal_api_key or settings.INTERNAL_API_KEY or ""

    def _build_headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"MSG-Incoming/{settings.APP_VERSION}",
        }
        api_key = self.broker_api_key if self.broker_api_key is not None else settings.BROKER_API_KEY
        if api_key:
            headers["X-API-Key"] = api_key
        internal_key = self.internal_api_key if self.internal_api_key is not None else settings.INTERNAL_API_KEY
        if internal_key:
            headers["X-Internal-Key"] = internal_key
        return headers

    async def emit_to_trading_engine(
        self,
        user_id: UUID,
        message: IngestedMessage,
        owner_email: Optional[str] = None,
        user_email: Optional[str] = None,
    ) -> Result[Dict[str, Any], str]:
        """
        Sends an approved raw message to the quant trading server operations ingest endpoint.
        Retries up to 3 attempts (backoff 0.5s, 1.0s) on httpx.RequestError or HTTP 5xx.
        """
        endpoint = f"{self.broker_url}/api/v1/operations/ingest"
        metadata = message.metadata_json or {}
        resolved_owner_email = owner_email or user_email or metadata.get("owner_email")

        platform_str = str(
            message.platform.value if hasattr(message.platform, "value") else message.platform
        )
        chat_id_str = str(
            message.chat_id or metadata.get("chat_id") or message.sender_id
        )
        received_at_str = message.created_at.isoformat() if message.created_at else None
        message_date_str = metadata.get("date") or received_at_str

        payload: Dict[str, Any] = {
            "owner_email": resolved_owner_email,
            "platform": platform_str,
            "chat_id": chat_id_str,
            "external_message_id": str(message.external_message_id),
            "sender_id": str(message.sender_id),
            "sender_username": message.sender_username,
            "chat_title": metadata.get("chat_title"),
            "raw_text": message.raw_text,
            "message_date": message_date_str,
            "received_at": received_at_str,
            "internal_message_id": message.id,
        }

        headers = self._build_headers()
        last_error = "Unknown error during broker emission"

        try:
            async with httpx.AsyncClient(timeout=settings.DISPATCH_TIMEOUT_SECONDS) as client:
                for attempt in range(1, self.MAX_ATTEMPTS + 1):
                    try:
                        logger.info(
                            f"Emitting message #{message.id} (user={user_id}, owner={resolved_owner_email}) "
                            f"to broker pipeline at {endpoint} (attempt {attempt}/{self.MAX_ATTEMPTS})"
                        )
                        response = await client.post(endpoint, json=payload, headers=headers)

                        if response.status_code in (200, 201):
                            res_json = response.json()
                            logger.info(
                                f"Successfully emitted message #{message.id} to broker pipeline: {res_json}"
                            )
                            return Ok(res_json)

                        last_error = (
                            f"Broker responded with status {response.status_code}: {response.text}"
                        )
                        if response.status_code >= 500 and attempt < self.MAX_ATTEMPTS:
                            backoff = self.RETRY_BACKOFFS[min(attempt - 1, len(self.RETRY_BACKOFFS) - 1)]
                            logger.warning(
                                f"{last_error}. Retrying in {backoff}s (attempt {attempt}/{self.MAX_ATTEMPTS})..."
                            )
                            await asyncio.sleep(backoff)
                            continue

                        logger.warning(last_error)
                        return Err(last_error)

                    except httpx.RequestError as exc:
                        last_error = f"Network error connecting to broker engine ({endpoint}): {exc}"
                        if attempt < self.MAX_ATTEMPTS:
                            backoff = self.RETRY_BACKOFFS[min(attempt - 1, len(self.RETRY_BACKOFFS) - 1)]
                            logger.warning(
                                f"{last_error}. Retrying in {backoff}s (attempt {attempt}/{self.MAX_ATTEMPTS})..."
                            )
                            await asyncio.sleep(backoff)
                            continue
                        logger.error(last_error)
                        return Err(last_error)

                return Err(last_error)

        except Exception as exc:
            err_str = f"Unexpected error during broker emission: {exc}"
            logger.error(err_str, exc_info=True)
            return Err(err_str)

    async def forward_to_rust_hub(
        self,
        sender_name: str,
        external_id: str,
        network: str = "telegram",
    ) -> Result[Dict[str, Any], str]:
        """
        Syncs or notifies the Rust server (`/api/senders`) if RUST_APP_URL is active.
        """
        if not settings.RUST_APP_URL:
            return Ok({"skipped": True, "reason": "RUST_APP_URL not configured"})

        endpoint = f"{settings.RUST_APP_URL.rstrip('/')}/api/senders"
        payload = {
            "network": network.lower(),
            "name": sender_name,
            "external_id": external_id,
            "sender_type": "channel",
        }

        try:
            async with httpx.AsyncClient(timeout=settings.DISPATCH_TIMEOUT_SECONDS) as client:
                res = await client.post(endpoint, json=payload)
                if res.status_code in (200, 201):
                    return Ok(res.json())
                return Err(f"Rust server error {res.status_code}: {res.text}")
        except Exception as e:
            return Err(f"Could not forward to Rust hub: {e}")


broker_emitter = BrokerEmitter()
