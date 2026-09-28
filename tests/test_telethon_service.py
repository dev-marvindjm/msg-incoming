import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy
from app.domain.models import IngestedMessage, SenderRule, User
from app.services.telethon_client import TelethonService


@pytest.mark.asyncio
async def test_telethon_message_event_handling_and_dispatch(db_session: AsyncSession, user_a: User):
    """
    Tests that when an incoming Telethon event arrives, the service extracts the
    sender ID, username, raw text, and persists an evaluated message for user_a.
    """
    # Configure user_a to allow this channel
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

    # Create mock event
    mock_event = MagicMock()
    mock_event.chat_id = int(channel_id)
    mock_event.sender_id = None
    mock_event.raw_text = "BUY XAUUSD 2350.50 SL 2340 TP 2370"
    mock_event.message.id = 5544
    mock_event.message.message = "BUY XAUUSD 2350.50 SL 2340 TP 2370"
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

    # Patch async_session_maker to use our test db_session
    mock_maker = MagicMock()
    mock_maker.return_value.__aenter__.return_value = db_session
    mock_maker.return_value.__aexit__.return_value = None

    with patch("app.services.telethon_client.async_session_maker", mock_maker), \
         patch("app.services.emitter.broker_emitter.emit_to_trading_engine", new_callable=AsyncMock) as mock_emit:

        await service._handle_new_message(mock_event)
        # Give asyncio loop a moment for background emit tasks
        await asyncio.sleep(0.05)

    # Verify message was ingested in database
    stmt = select(IngestedMessage).where(
        IngestedMessage.user_id == user_a.id,
        IngestedMessage.external_message_id == "5544",
    )
    result = await db_session.exec(stmt)
    ingested = result.first()

    assert ingested is not None
    assert ingested.sender_id == channel_id
    assert ingested.sender_username == "elite_signals_channel"
    assert ingested.delivery_status == DeliveryStatus.EMITTED
    assert "XAUUSD" in ingested.raw_text
