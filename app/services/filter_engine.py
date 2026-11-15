import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from uuid import UUID

from sqlmodel import or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.result import Err, Ok, Result
from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy
from app.domain.models import GlobalFilterConfig, IngestedMessage, SenderRule
from app.domain.schemas import WebhookMessageCreate

logger = logging.getLogger("msg_ingestor.filter_engine")


@dataclass
class FilterDecision:
    delivery_status: DeliveryStatus
    block_reason: Optional[str]
    should_forward: bool
    matched_rule: Optional[SenderRule] = None


class FilterEngine:
    """
    Pure decision and evaluation engine.
    Applies multi-tenant sender policies (ALLOW, BLOCK, DEFAULT) and
    global platform filters (allow_any_sender, strict_whitelist) with
    strict idempotency per user.
    """

    @staticmethod
    async def evaluate_sender(
        user_id: UUID,
        platform: PlatformType,
        sender_id: str,
        sender_username: Optional[str],
        db: AsyncSession,
    ) -> FilterDecision:
        """
        Evaluates the sender policy against active SenderRule and GlobalFilterConfig.
        """
        clean_username = sender_username.lstrip("@").lower() if sender_username else None

        # 1. Search for a specific SenderRule for this user and platform
        conditions = [SenderRule.entity_id == sender_id]
        if clean_username:
            # Also allow matching by @username without the leading '@'
            conditions.append(SenderRule.entity_name == clean_username)
            conditions.append(SenderRule.entity_name == f"@{clean_username}")

        rule_stmt = (
            select(SenderRule)
            .where(
                SenderRule.user_id == user_id,
                SenderRule.platform == platform,
                SenderRule.is_active == True,  # noqa: E712
                or_(*conditions),
            )
        )
        rule_result = await db.exec(rule_stmt)
        matched_rule = rule_result.first()

        # 2. Query user's GlobalFilterConfig for this platform
        config_stmt = select(GlobalFilterConfig).where(
            GlobalFilterConfig.user_id == user_id,
            GlobalFilterConfig.platform == platform,
        )
        config_result = await db.exec(config_stmt)
        global_config = config_result.first()

        allow_any_sender = global_config.allow_any_sender if global_config else False
        strict_whitelist = global_config.strict_whitelist if global_config else False

        # 3. Decision Matrix:
        # A. Explicit Rule exists and is active
        if matched_rule:
            if matched_rule.policy == SenderPolicy.BLOCK:
                logger.info(
                    f"User {user_id}: Sender {sender_id} (@{sender_username}) BLOCKED by explicit rule #{matched_rule.id}"
                )
                return FilterDecision(
                    delivery_status=DeliveryStatus.BLOCKED,
                    block_reason="BLACKLISTED_SENDER",
                    should_forward=False,
                    matched_rule=matched_rule,
                )

            if matched_rule.policy == SenderPolicy.ALLOW:
                logger.info(
                    f"User {user_id}: Sender {sender_id} (@{sender_username}) ALLOWED by explicit rule #{matched_rule.id}"
                )
                return FilterDecision(
                    delivery_status=DeliveryStatus.EMITTED,
                    block_reason=None,
                    should_forward=matched_rule.forward_to_brokers,
                    matched_rule=matched_rule,
                )

            # If policy is DEFAULT, fall through to GlobalFilterConfig evaluation

        # B. Fallback to GlobalFilterConfig
        if allow_any_sender:
            logger.info(
                f"User {user_id}: Sender {sender_id} (@{sender_username}) EMITTED (allow_any_sender active, unblocked)"
            )
            return FilterDecision(
                delivery_status=DeliveryStatus.EMITTED,
                block_reason=None,
                should_forward=True,
                matched_rule=matched_rule,
            )

        if strict_whitelist:
            logger.info(
                f"User {user_id}: Sender {sender_id} (@{sender_username}) IGNORED (strict_whitelist active, not in ALLOW)"
            )
            return FilterDecision(
                delivery_status=DeliveryStatus.IGNORED,
                block_reason="NOT_IN_WHITELIST",
                should_forward=False,
                matched_rule=matched_rule,
            )

        # C. Default conservative behavior when neither allow_any nor whitelist match
        logger.info(
            f"User {user_id}: Sender {sender_id} (@{sender_username}) IGNORED (unapproved default policy)"
        )
        return FilterDecision(
            delivery_status=DeliveryStatus.IGNORED,
            block_reason="DEFAULT_POLICY_UNAPPROVED",
            should_forward=False,
            matched_rule=matched_rule,
        )

    async def ingest_and_evaluate(
        self,
        user_id: UUID,
        payload: WebhookMessageCreate,
        db: AsyncSession,
    ) -> Result[Tuple[IngestedMessage, bool, bool], str]:
        """
        Idempotently persists the message and evaluates sender policies.
        Returns Result with (IngestedMessage, should_forward, is_duplicate).
        """
        try:
            raw_chat_id = (
                payload.chat_id
                or (payload.metadata_json or {}).get("chat_id")
                or payload.sender_id
            )
            resolved_chat_id = str(raw_chat_id) if raw_chat_id is not None else None

            # 1. Idempotency Check: (user_id, platform, chat_id, external_message_id)
            existing_stmt = select(IngestedMessage).where(
                IngestedMessage.user_id == user_id,
                IngestedMessage.platform == payload.platform,
                IngestedMessage.chat_id == resolved_chat_id,
                IngestedMessage.external_message_id == str(payload.external_message_id),
            )
            existing_res = await db.exec(existing_stmt)
            existing_message = existing_res.first()

            if existing_message:
                logger.info(
                    f"Duplicate message ignored for user {user_id}: "
                    f"platform={payload.platform}, chat_id={resolved_chat_id}, msg_id={payload.external_message_id}"
                )
                return Ok((existing_message, False, True))

            # 2. Evaluate Policy
            decision = await self.evaluate_sender(
                user_id=user_id,
                platform=payload.platform,
                sender_id=payload.sender_id,
                sender_username=payload.sender_username,
                db=db,
            )

            # 3. Persist Ingested Message
            ingested = IngestedMessage(
                user_id=user_id,
                platform=payload.platform,
                chat_id=resolved_chat_id,
                external_message_id=str(payload.external_message_id),
                sender_id=str(payload.sender_id),
                sender_username=payload.sender_username,
                raw_text=payload.raw_text,
                metadata_json=payload.metadata_json,
                delivery_status=decision.delivery_status,
                block_reason=decision.block_reason,
            )
            db.add(ingested)
            await db.commit()
            await db.refresh(ingested)

            return Ok((ingested, decision.should_forward, False))

        except Exception as exc:
            logger.error(f"Error ingesting message for user {user_id}: {exc}", exc_info=True)
            await db.rollback()
            return Err(f"Failed to ingest message: {str(exc)}")


filter_engine = FilterEngine()
