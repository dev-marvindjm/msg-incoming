import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.enums import DeliveryStatus, PlatformType
from app.domain.models import IngestedMessage
from app.services.emitter import BrokerEmitter


@pytest.mark.asyncio
async def test_emitter_extracts_trading_hints():
    emitter = BrokerEmitter()

    hints1 = emitter._extract_trading_hints("BUY EURUSD 1.0850 SL 1.0800 TP 1.0950")
    assert hints1["action"] == "BUY"
    assert hints1["symbol"] == "EURUSD"

    hints2 = emitter._extract_trading_hints("VENTA / PUT EN GBPJPY 60s")
    assert hints2["action"] == "SELL"
    assert hints2["symbol"] == "GBPJPY"

    hints3 = emitter._extract_trading_hints("Mensaje sin par ni señal")
    assert hints3["symbol"] == "UNKNOWN"


@pytest.mark.asyncio
async def test_emitter_successful_post():
    emitter = BrokerEmitter(broker_url="http://mock-broker:8000")
    user_id = uuid4()
    msg = IngestedMessage(
        id=42,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        external_message_id="ext_42",
        sender_id="-100999",
        sender_username="trader_pro",
        raw_text="BUY EURUSD 1.0850",
        delivery_status=DeliveryStatus.EMITTED,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"success": True, "order_id": 999}
        mock_post.return_value = mock_resp

        res = await emitter.emit_to_trading_engine(user_id, msg)
        assert res.is_ok()
        data = res.unwrap()
        assert data["order_id"] == 999


@pytest.mark.asyncio
async def test_emitter_handles_network_error():
    emitter = BrokerEmitter(broker_url="http://unreachable-broker:9999")
    user_id = uuid4()
    msg = IngestedMessage(
        id=43,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        external_message_id="ext_43",
        sender_id="-100999",
        raw_text="BUY EURUSD",
        delivery_status=DeliveryStatus.EMITTED,
    )

    # When network fails, emitter returns Err instead of throwing unhandled exception
    res = await emitter.emit_to_trading_engine(user_id, msg)
    assert res.is_err()
    assert "error connecting to broker" in res.unwrap_err().lower()
