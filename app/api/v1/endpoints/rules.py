from datetime import datetime, timezone
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import col, desc, select

from app.core.database import SessionDep
from app.core.security import UserDep
from app.domain.enums import PlatformType, SenderPolicy
from app.domain.models import GlobalFilterConfig, SenderRule
from app.domain.schemas import (
    ApiResponse,
    GlobalFilterConfigResponse,
    GlobalFilterConfigUpdate,
    SenderRuleCreate,
    SenderRuleResponse,
    SenderRuleUpdate,
)

router = APIRouter(prefix="/rules", tags=["Filtering Rules & Configurations"])


# ---------------------------------------------------------
# Senders CRUD & Configuration
# ---------------------------------------------------------
@router.post(
    "/senders",
    response_model=ApiResponse[SenderRuleResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register or update a sender policy rule",
)
async def upsert_sender_rule(
    rule_in: SenderRuleCreate,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Registers a new sender rule or updates an existing rule for the authenticated user.
    Allows configuring ALLOW (whitelist), BLOCK (blacklist), or DEFAULT per sender/channel.
    """
    # Check if a rule for this user, platform, and entity_id already exists
    stmt = select(SenderRule).where(
        SenderRule.user_id == current_user.id,
        SenderRule.platform == rule_in.platform,
        SenderRule.entity_id == rule_in.entity_id,
    )
    result = await session.exec(stmt)
    existing_rule = result.first()

    now = datetime.now(timezone.utc)

    if existing_rule:
        # Upsert / update existing rule
        existing_rule.entity_name = rule_in.entity_name or existing_rule.entity_name
        existing_rule.policy = rule_in.policy
        existing_rule.is_active = rule_in.is_active
        existing_rule.forward_to_brokers = rule_in.forward_to_brokers
        existing_rule.updated_at = now
        session.add(existing_rule)
        await session.commit()
        await session.refresh(existing_rule)
        return ApiResponse(data=SenderRuleResponse.model_validate(existing_rule))

    # Create new rule
    new_rule = SenderRule(
        user_id=current_user.id,
        platform=rule_in.platform,
        entity_id=rule_in.entity_id,
        entity_name=rule_in.entity_name,
        policy=rule_in.policy,
        is_active=rule_in.is_active,
        forward_to_brokers=rule_in.forward_to_brokers,
        created_at=now,
        updated_at=now,
    )
    session.add(new_rule)
    await session.commit()
    await session.refresh(new_rule)

    return ApiResponse(data=SenderRuleResponse.model_validate(new_rule))


@router.get(
    "/senders",
    response_model=ApiResponse[List[SenderRuleResponse]],
    summary="List active sender rules with filters",
)
async def list_sender_rules(
    current_user: UserDep,
    session: SessionDep,
    platform: Optional[PlatformType] = Query(None, description="Filter by platform"),
    policy: Optional[SenderPolicy] = Query(None, description="Filter by policy (ALLOW, BLOCK, DEFAULT)"),
    is_active: Optional[bool] = Query(None, description="Filter by active status"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Retrieves all configured sender rules for the authenticated user,
    with optional filtering by platform, policy, and active status.
    """
    stmt = select(SenderRule).where(SenderRule.user_id == current_user.id)

    if platform is not None:
        stmt = stmt.where(SenderRule.platform == platform)
    if policy is not None:
        stmt = stmt.where(SenderRule.policy == policy)
    if is_active is not None:
        stmt = stmt.where(SenderRule.is_active == is_active)

    stmt = stmt.order_by(desc(SenderRule.updated_at)).offset(offset).limit(limit)
    result = await session.exec(stmt)
    rules = result.all()

    return ApiResponse(data=[SenderRuleResponse.model_validate(r) for r in rules])


@router.delete(
    "/senders/{sender_identifier}",
    response_model=ApiResponse[dict],
    summary="Delete a sender rule by ID or entity_id",
)
async def delete_sender_rule(
    sender_identifier: str,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Removes a sender rule. `sender_identifier` can be either the numeric primary key
    or the external `entity_id` (channel ID/username) belonging to the user.
    """
    # 1. Try finding by numeric primary key
    rule = None
    if sender_identifier.isdigit():
        pk = int(sender_identifier)
        stmt = select(SenderRule).where(SenderRule.id == pk, SenderRule.user_id == current_user.id)
        result = await session.exec(stmt)
        rule = result.first()

    # 2. If not found by PK, find by entity_id
    if not rule:
        stmt = select(SenderRule).where(
            SenderRule.entity_id == sender_identifier,
            SenderRule.user_id == current_user.id,
        )
        result = await session.exec(stmt)
        rule = result.first()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sender rule '{sender_identifier}' not found for current user.",
        )

    await session.delete(rule)
    await session.commit()

    return ApiResponse(data={"success": True, "deleted_rule_id": rule.id, "entity_id": rule.entity_id})


@router.put(
    "/senders/{rule_id}/toggle",
    response_model=ApiResponse[SenderRuleResponse],
    summary="Toggle active status of a sender rule",
)
async def toggle_sender_rule(
    rule_id: int,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Toggles the `is_active` flag of a sender rule (inspired by Rust `/api/senders/:id/toggle`).
    """
    stmt = select(SenderRule).where(SenderRule.id == rule_id, SenderRule.user_id == current_user.id)
    result = await session.exec(stmt)
    rule = result.first()

    if not rule:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Sender rule #{rule_id} not found.",
        )

    rule.is_active = not rule.is_active
    rule.updated_at = datetime.now(timezone.utc)
    session.add(rule)
    await session.commit()
    await session.refresh(rule)

    return ApiResponse(data=SenderRuleResponse.model_validate(rule))


# ---------------------------------------------------------
# Global Filter Configuration
# ---------------------------------------------------------
@router.get(
    "/config",
    response_model=ApiResponse[List[GlobalFilterConfigResponse]],
    summary="Get user's global filter configs",
)
async def get_global_filter_config(
    current_user: UserDep,
    session: SessionDep,
    platform: Optional[PlatformType] = Query(None, description="Optional platform filter"),
):
    """
    Returns the user's dynamic global configurations (`allow_any_sender`, `strict_whitelist`).
    """
    stmt = select(GlobalFilterConfig).where(GlobalFilterConfig.user_id == current_user.id)
    if platform:
        stmt = stmt.where(GlobalFilterConfig.platform == platform)

    result = await session.exec(stmt)
    configs = result.all()

    # If no config exists yet, create defaults for queried or common platforms
    if not configs:
        target_platforms = [platform] if platform else [PlatformType.TELEGRAM, PlatformType.THREADS]
        for p in target_platforms:
            cfg = GlobalFilterConfig(
                user_id=current_user.id,
                platform=p,
                allow_any_sender=False,
                strict_whitelist=False,
            )
            session.add(cfg)
        await session.commit()
        result = await session.exec(stmt)
        configs = result.all()

    return ApiResponse(data=[GlobalFilterConfigResponse.model_validate(c) for c in configs])


@router.put(
    "/config",
    response_model=ApiResponse[GlobalFilterConfigResponse],
    summary="Update user's global filter config dynamically",
)
async def update_global_filter_config(
    config_in: GlobalFilterConfigUpdate,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Dynamically updates the global filtering policy (`allow_any_sender`, `strict_whitelist`)
    without restarting the service.
    """
    stmt = select(GlobalFilterConfig).where(
        GlobalFilterConfig.user_id == current_user.id,
        GlobalFilterConfig.platform == config_in.platform,
    )
    result = await session.exec(stmt)
    config = result.first()

    now = datetime.now(timezone.utc)

    if not config:
        config = GlobalFilterConfig(
            user_id=current_user.id,
            platform=config_in.platform,
            allow_any_sender=config_in.allow_any_sender if config_in.allow_any_sender is not None else False,
            strict_whitelist=config_in.strict_whitelist if config_in.strict_whitelist is not None else False,
            updated_at=now,
        )
    else:
        if config_in.allow_any_sender is not None:
            config.allow_any_sender = config_in.allow_any_sender
        if config_in.strict_whitelist is not None:
            config.strict_whitelist = config_in.strict_whitelist
        config.updated_at = now

    session.add(config)
    await session.commit()
    await session.refresh(config)

    return ApiResponse(data=GlobalFilterConfigResponse.model_validate(config))
