import logging
import re
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
    broker trading pipeline (MetaTrader 5 Quant Server and binary options adapters).
    """

    def __init__(self, broker_url: str = settings.BROKER_API_URL):
        self.broker_url = broker_url.rstrip("/")
        self.headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": f"MSG-Incoming/{settings.APP_VERSION}",
        }
        if settings.BROKER_API_KEY:
            self.headers["X-API-Key"] = settings.BROKER_API_KEY

    def _extract_trading_hints(self, text: str) -> Dict[str, Any]:
        """
        Lightweight heuristic parser to extract basic trading hints
        (e.g., EURUSD, BUY/SELL/CALL/PUT) if available in raw text.
        """
        upper_text = text.upper()

        # Identify action
        action = "BUY"
        if "SELL" in upper_text or "PUT" in upper_text or "VENTA" in upper_text or "BAJA" in upper_text:
            action = "SELL"
        elif "BUY" in upper_text or "CALL" in upper_text or "COMPRA" in upper_text or "SUBE" in upper_text:
            action = "BUY"

        # Identify currency pair or asset (e.g. EURUSD, GBPJPY, BTCUSD, etc.)
        asset_match = re.search(r"\b([A-Z]{3}/?[A-Z]{3}|BTCUSD|ETHUSD|XAUUSD|GOLD)\b", upper_text)
        symbol = asset_match.group(1).replace("/", "") if asset_match else "UNKNOWN"

        return {
            "symbol": symbol,
            "action": action,
        }

    async def emit_to_trading_engine(
        self,
        user_id: UUID,
        message: IngestedMessage,
    ) -> Result[Dict[str, Any], str]:
        """
        Sends an approved message to the MetaTrader 5 quant server signal intake endpoint.
        """
        endpoint = f"{self.broker_url}/api/v1/signals/receive"
        hints = self._extract_trading_hints(message.raw_text)

        payload = {
            "source": f"{message.platform}:{message.sender_username or message.sender_id}",
            "symbol": hints["symbol"],
            "action": hints["action"],
            "market_type": "FOREX" if hints["symbol"] != "UNKNOWN" else "BINARY_OPTIONS",
            "raw_data": {
                "user_id": str(user_id),
                "internal_message_id": message.id,
                "external_message_id": message.external_message_id,
                "sender_id": message.sender_id,
                "sender_username": message.sender_username,
                "raw_text": message.raw_text,
                "metadata": message.metadata_json,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=settings.DISPATCH_TIMEOUT_SECONDS) as client:
                logger.info(f"Emitting message #{message.id} to broker pipeline at {endpoint}")
                response = await client.post(endpoint, json=payload, headers=self.headers)

                if response.status_code in (200, 201):
                    res_json = response.json()
                    logger.info(f"Successfully emitted message #{message.id} to broker pipeline: {res_json}")
                    return Ok(res_json)

                error_msg = f"Broker responded with status {response.status_code}: {response.text}"
                logger.warning(error_msg)
                return Err(error_msg)

        except httpx.RequestError as exc:
            err_str = f"Network error connecting to broker engine ({endpoint}): {exc}"
            logger.error(err_str)
            return Err(err_str)
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
