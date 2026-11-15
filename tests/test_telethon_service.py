import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.core.config import settings
from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy
from app.domain.models import IngestedMessage, SenderRule, User
from app.services.telethon_client import TelethonService


def _build_mock_event(channel_id: str, msg_id: int = 5544, text: str = "BUY XAUUSD 2350.50 SL 2340 TP 2370"):
    mock_event = MagicMock()
    mock_event.chat_id = int(channel_id)
    mock_event.sender_id = None
    mock_event.raw_text = text
    mock_event.message.id = msg_id
    mock_event.message.message = text
    mock_event.message.views = 120
    mock_event.message.forwards = 5
    mock_event.date = datetime.now(timezone.utc)
    mock_event.is_channel = True
    mock_event.is_group = False
    mock_event.is_private = False

    mock_chat = MagicMock()
    mock_chat.username = "elite_signals_channel"
    mock_chat.title = "Elite Signals VIP"

    mock_sender = MagicMock()
    mock_sender.username = "elite_signals_channel"

    mock_event.get_chat = AsyncMock(return_value=mock_chat)
    mock_event.get_sender = AsyncMock(return_value=mock_sender)
    return mock_event


@pytest.mark.asyncio
async def test_telethon_message_event_handling_and_dispatch(
    db_session: AsyncSession,
    user_a: User,
    monkeypatch,
):
    """
    Tests that when an incoming Telethon event arrives, the service extracts the
    chat_id, sender ID, username, raw text, and persists an evaluated message for user_a.
    """
    monkeypatch.setattr(settings, "TELEGRAM_OWNER_EMAIL", None)

    channel_id = "-1001987654321"
    rule = SenderRule(
        user_id=user_a.id,
        platform=PlatformType.TELEGRAM,
        entity_id=channel_id,
        entity_name="elite_signals_channel",
        policy=SenderPolicy.ALLOW,
        is_active=True,
        forward_to_brokers=True,
    )
    db_session.add(rule)
    await db_session.commit()

    service = TelethonService()
    mock_event = _build_mock_event(channel_id, msg_id=5544)

    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = db_session
    mock_maker.return_value.__aexit__.return_value = None

    with patch("app.services.telethon_client.async_session_maker", mock_maker), \
         patch("app.services.emitter.broker_emitter.emit_to_trading_engine", new_callable=AsyncMock) as mock_emit:

        await service._handle_new_message(mock_event)
        await asyncio.sleep(0.05)

        mock_emit.assert_awaited_once()
        emit_kwargs = mock_emit.call_args.kwargs
        assert emit_kwargs["user_id"] == user_a.id
        assert emit_kwargs["owner_email"] == user_a.email

    stmt = select(IngestedMessage).where(
        IngestedMessage.user_id == user_a.id,
        IngestedMessage.external_message_id == "5544",
    )
    result = await db_session.exec(stmt)
    ingested = result.first()

    assert ingested is not None
    assert ingested.chat_id == channel_id
    assert ingested.sender_id == channel_id
    assert ingested.sender_username == "elite_signals_channel"
    assert ingested.delivery_status == DeliveryStatus.EMITTED
    assert "XAUUSD" in ingested.raw_text


@pytest.mark.asyncio
async def test_telethon_owner_scoped_dispatch_only_hits_owner(
    db_session: AsyncSession,
    user_a: User,
    user_b: User,
    monkeypatch,
):
    """
    When TELEGRAM_OWNER_EMAIL is configured, incoming Telethon events must be
    dispatched exclusively to the matching active owner user and not to other users.
    """
    monkeypatch.setattr(settings, "TELEGRAM_OWNER_EMAIL", user_a.email)

    channel_id = "-1001987654321"
    for user in (user_a, user_b):
        db_session.add(
            SenderRule(
                user_id=user.id,
                platform=PlatformType.TELEGRAM,
                entity_id=channel_id,
                entity_name="elite_signals_channel",
                policy=SenderPolicy.ALLOW,
                is_active=True,
                forward_to_brokers=True,
            )
        )
    await db_session.commit()

    service = TelethonService()
    mock_event = _build_mock_event(channel_id, msg_id=7788, text="EURUSD CALL 5M")

    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = db_session
    mock_maker.return_value.__aexit__.return_value = None

    with patch("app.services.telethon_client.async_session_maker", mock_maker), \
         patch("app.services.emitter.broker_emitter.emit_to_trading_engine", new_callable=AsyncMock) as mock_emit:

        await service._handle_new_message(mock_event)
        await asyncio.sleep(0.05)

        mock_emit.assert_awaited_once()
        assert mock_emit.call_args.kwargs["user_id"] == user_a.id
        assert mock_emit.call_args.kwargs["owner_email"] == user_a.email

    res_a = await db_session.exec(
        select(IngestedMessage).where(IngestedMessage.user_id == user_a.id)
    )
    assert len(res_a.all()) == 1

    res_b = await db_session.exec(
        select(IngestedMessage).where(IngestedMessage.user_id == user_b.id)
    )
    assert len(res_b.all()) == 0
