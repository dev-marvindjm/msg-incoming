from datetime import datetime, timezone
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from app.core.config import settings
from app.domain.enums import DeliveryStatus, PlatformType
from app.domain.models import IngestedMessage
from app.services.emitter import BrokerEmitter


@pytest.mark.asyncio
async def test_emitter_successful_post_matches_contract(monkeypatch):
    monkeypatch.setattr(settings, "INTERNAL_API_KEY", "test-internal-key-2026")
    monkeypatch.setattr(settings, "BROKER_API_KEY", "test-broker-api-key")

    emitter = BrokerEmitter(broker_url="http://mock-broker:8000")
    assert not hasattr(emitter, "_extract_trading_hints")

    user_id = uuid4()
    created_at = datetime(2026, 11, 15, 13, 5, 0, 412000, tzinfo=timezone.utc)
    msg_date = "2026-11-15T13:05:00+00:00"

    msg = IngestedMessage(
        id=42,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        chat_id="-1001234567890",
        external_message_id="4521",
        sender_id="-1001234567890",
        sender_username="channel_handle",
        raw_text="EURUSD CALL 14:05 M1",
        metadata_json={
            "chat_id": -1001234567890,
            "chat_title": "VIP Signals",
            "date": msg_date,
        },
        delivery_status=DeliveryStatus.EMITTED,
        created_at=created_at,
    )

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {
            "success": True,
            "data": {"status": "CREATED", "operations": [{"id": 1}]},
        }
        mock_post.return_value = mock_resp

        res = await emitter.emit_to_trading_engine(
            user_id=user_id,
            message=msg,
            owner_email="operator_a@quant.com",
        )
        assert res.is_ok()
        data = res.unwrap()
        assert data["success"] is True
        assert data["data"]["status"] == "CREATED"

        mock_post.assert_awaited_once()
        call_args = mock_post.call_args
        assert call_args.args[0] == "http://mock-broker:8000/api/v1/operations/ingest"

        sent_headers = call_args.kwargs["headers"]
        assert sent_headers["X-Internal-Key"] == "test-internal-key-2026"
        assert sent_headers["X-API-Key"] == "test-broker-api-key"

        sent_json = call_args.kwargs["json"]
        assert sent_json == {
            "owner_email": "operator_a@quant.com",
            "platform": "TELEGRAM",
            "chat_id": "-1001234567890",
            "external_message_id": "4521",
            "sender_id": "-1001234567890",
            "sender_username": "channel_handle",
            "chat_title": "VIP Signals",
            "raw_text": "EURUSD CALL 14:05 M1",
            "message_date": msg_date,
            "received_at": created_at.isoformat(),
            "internal_message_id": 42,
        }


@pytest.mark.asyncio
async def test_emitter_retries_on_5xx_and_succeeds():
    emitter = BrokerEmitter(
        broker_url="http://mock-broker:8000",
        internal_api_key="internal-key",
    )
    user_id = uuid4()
    msg = IngestedMessage(
        id=44,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        chat_id="-100555",
        external_message_id="ext_44",
        sender_id="-100555",
        raw_text="BUY EURUSD",
        delivery_status=DeliveryStatus.EMITTED,
    )

    resp_502 = MagicMock()
    resp_502.status_code = 502
    resp_502.text = "Bad Gateway"

    resp_200 = MagicMock()
    resp_200.status_code = 200
    resp_200.json.return_value = {"success": True, "data": {"status": "CREATED", "operations": []}}

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=[resp_502, resp_200]) as mock_post, \
         patch("app.services.emitter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await emitter.emit_to_trading_engine(user_id, msg, owner_email="user@quant.com")
        assert res.is_ok()
        assert mock_post.await_count == 2
        mock_sleep.assert_awaited_once_with(0.5)


@pytest.mark.asyncio
async def test_emitter_does_not_retry_on_4xx():
    emitter = BrokerEmitter(broker_url="http://mock-broker:8000")
    user_id = uuid4()
    msg = IngestedMessage(
        id=45,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        chat_id="-100555",
        external_message_id="ext_45",
        sender_id="-100555",
        raw_text="BUY EURUSD",
        delivery_status=DeliveryStatus.EMITTED,
    )

    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.text = "Unauthorized"

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=resp_401) as mock_post, \
         patch("app.services.emitter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        res = await emitter.emit_to_trading_engine(user_id, msg, owner_email="user@quant.com")
        assert res.is_err()
        assert "401" in res.unwrap_err()
        assert mock_post.await_count == 1
        mock_sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_emitter_handles_network_error():
    emitter = BrokerEmitter(broker_url="http://unreachable-broker:9999")
    user_id = uuid4()
    msg = IngestedMessage(
        id=43,
        user_id=user_id,
        platform=PlatformType.TELEGRAM,
        chat_id="-100999",
        external_message_id="ext_43",
        sender_id="-100999",
        raw_text="BUY EURUSD",
        delivery_status=DeliveryStatus.EMITTED,
    )

    with patch(
        "httpx.AsyncClient.post",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("Connection refused"),
    ) as mock_post, patch("app.services.emitter.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        # When network fails, emitter retries up to 3 attempts and returns Err instead of raising
        res = await emitter.emit_to_trading_engine(user_id, msg, owner_email="user@quant.com")
        assert res.is_err()
        assert "error connecting to broker" in res.unwrap_err().lower()
        assert mock_post.await_count == 3
        assert mock_sleep.await_count == 2
