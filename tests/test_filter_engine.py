import pytest
from uuid import uuid4
from sqlmodel.ext.asyncio.session import AsyncSession
from sqlmodel import select

from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy
from app.domain.models import GlobalFilterConfig, SenderRule, User
from app.domain.schemas import WebhookMessageCreate
from app.services.filter_engine import filter_engine


@pytest.mark.asyncio
async def test_filter_engine_allow_policy(db_session: AsyncSession, user_a: User):
    """Verifies that an explicitly allowed sender has delivery_status=EMITTED and should_forward=True."""
    rule = SenderRule(
        user_id=user_a.id,
        platform=PlatformType.TELEGRAM,
        entity_id="-1001234567890",
        entity_name="vip_forex_signals",
        policy=SenderPolicy.ALLOW,
        is_active=True,
        forward_to_brokers=True,
    )
    db_session.add(rule)
    await db_session.commit()

    payload = WebhookMessageCreate(
        platform=PlatformType.TELEGRAM,
        external_message_id="msg_001",
        sender_id="-1001234567890",
        sender_username="vip_forex_signals",
        raw_text="BUY EURUSD 1.0850 SL 1.0800 TP 1.0950",
    )

    res = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert res.is_ok()
    message, should_forward, is_duplicate = res.unwrap()

    assert message.delivery_status == DeliveryStatus.EMITTED
    assert message.block_reason is None
    assert should_forward is True
    assert is_duplicate is False


@pytest.mark.asyncio
async def test_filter_engine_block_policy(db_session: AsyncSession, user_a: User):
    """Verifies that an explicitly blocked sender has delivery_status=BLOCKED and should_forward=False."""
    rule = SenderRule(
        user_id=user_a.id,
        platform=PlatformType.TELEGRAM,
        entity_id="spam_channel_99",
        entity_name="crypto_pumps",
        policy=SenderPolicy.BLOCK,
        is_active=True,
    )
    db_session.add(rule)
    await db_session.commit()

    payload = WebhookMessageCreate(
        platform=PlatformType.TELEGRAM,
        external_message_id="msg_002",
        sender_id="spam_channel_99",
        sender_username="crypto_pumps",
        raw_text="PUMP AND DUMP TOKEN NOW!",
    )

    res = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert res.is_ok()
    message, should_forward, is_duplicate = res.unwrap()

    assert message.delivery_status == DeliveryStatus.BLOCKED
    assert message.block_reason == "BLACKLISTED_SENDER"
    assert should_forward is False
    assert is_duplicate is False


@pytest.mark.asyncio
async def test_filter_engine_strict_whitelist_ignores_unlisted(db_session: AsyncSession, user_a: User):
    """When strict_whitelist=True, unlisted senders are IGNORED."""
    # Update global config for user_a
    stmt = select(GlobalFilterConfig).where(
        GlobalFilterConfig.user_id == user_a.id,
        GlobalFilterConfig.platform == PlatformType.TELEGRAM,
    )
    res = await db_session.exec(stmt)
    cfg = res.first()
    cfg.strict_whitelist = True
    db_session.add(cfg)
    await db_session.commit()

    payload = WebhookMessageCreate(
        platform=PlatformType.TELEGRAM,
        external_message_id="msg_003",
        sender_id="random_sender_123",
        raw_text="Some trading suggestion",
    )

    result = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert result.is_ok()
    message, should_forward, is_duplicate = result.unwrap()

    assert message.delivery_status == DeliveryStatus.IGNORED
    assert message.block_reason == "NOT_IN_WHITELIST"
    assert should_forward is False


@pytest.mark.asyncio
async def test_filter_engine_allow_any_sender_dynamic(db_session: AsyncSession, user_a: User):
    """When allow_any_sender=True, any unblocked sender is EMITTED."""
    # Update global config for user_a
    stmt = select(GlobalFilterConfig).where(
        GlobalFilterConfig.user_id == user_a.id,
        GlobalFilterConfig.platform == PlatformType.THREADS,
    )
    res = await db_session.exec(stmt)
    cfg = res.first()
    cfg.allow_any_sender = True
    db_session.add(cfg)
    await db_session.commit()

    payload = WebhookMessageCreate(
        platform=PlatformType.THREADS,
        external_message_id="post_999",
        sender_id="threads_trader_44",
        raw_text="AUDUSD CALL 60s at support",
    )

    result = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert result.is_ok()
    message, should_forward, is_duplicate = result.unwrap()

    assert message.delivery_status == DeliveryStatus.EMITTED
    assert message.block_reason is None
    assert should_forward is True


@pytest.mark.asyncio
async def test_filter_engine_idempotency_per_user(db_session: AsyncSession, user_a: User):
    """Duplicate message with same external_message_id is not re-processed."""
    payload = WebhookMessageCreate(
        platform=PlatformType.TELEGRAM,
        external_message_id="unique_msg_100",
        sender_id="sender_alpha",
        raw_text="Test message",
    )

    res1 = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert res1.is_ok()
    msg1, should_forward1, is_duplicate1 = res1.unwrap()
    assert is_duplicate1 is False

    # Second ingestion attempt of the same message for user_a
    res2 = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    assert res2.is_ok()
    msg2, should_forward2, is_duplicate2 = res2.unwrap()
    assert is_duplicate2 is True
    assert should_forward2 is False
    assert msg1.id == msg2.id


@pytest.mark.asyncio
async def test_multi_user_tenancy_isolation(db_session: AsyncSession, user_a: User, user_b: User):
    """
    Tests that two independent users can evaluate the exact same channel/sender
    differently according to their own rules (User A ALLOWs, User B BLOCKs).
    """
    channel_id = "-10088888888"

    # User A allows channel
    rule_a = SenderRule(
        user_id=user_a.id,
        platform=PlatformType.TELEGRAM,
        entity_id=channel_id,
        policy=SenderPolicy.ALLOW,
        is_active=True,
    )
    # User B blocks same channel
    rule_b = SenderRule(
        user_id=user_b.id,
        platform=PlatformType.TELEGRAM,
        entity_id=channel_id,
        policy=SenderPolicy.BLOCK,
        is_active=True,
    )
    db_session.add_all([rule_a, rule_b])
    await db_session.commit()

    payload = WebhookMessageCreate(
        platform=PlatformType.TELEGRAM,
        external_message_id="shared_telegram_msg_777",
        sender_id=channel_id,
        raw_text="BUY GBPUSD 1.2700",
    )

    # Ingest for User A
    res_a = await filter_engine.ingest_and_evaluate(user_a.id, payload, db_session)
    msg_a, forward_a, _ = res_a.unwrap()
    assert msg_a.delivery_status == DeliveryStatus.EMITTED
    assert forward_a is True

    # Ingest for User B (with same external_message_id, proving composite uniqueness per user)
    res_b = await filter_engine.ingest_and_evaluate(user_b.id, payload, db_session)
    msg_b, forward_b, _ = res_b.unwrap()
    assert msg_b.delivery_status == DeliveryStatus.BLOCKED
    assert forward_b is False
    assert msg_a.id != msg_b.id
