from typing import Optional
from fastapi import APIRouter, HTTPException, Query, status
from sqlmodel import desc, func, select

from app.core.database import SessionDep
from app.core.security import UserDep
from app.domain.enums import DeliveryStatus, PlatformType
from app.domain.models import IngestedMessage
from app.domain.schemas import (
    ApiResponse,
    IngestedMessageResponse,
    MessageIngestResult,
    PaginatedResponse,
    WebhookMessageCreate,
)
from app.services.emitter import broker_emitter
from app.services.filter_engine import filter_engine

router = APIRouter(prefix="/messages", tags=["Message Ingestion & Audit"])


@router.post(
    "/webhook",
    response_model=ApiResponse[MessageIngestResult],
    status_code=status.HTTP_201_CREATED,
    summary="Ingest external message via HTTP webhook",
)
async def ingest_webhook_message(
    payload: WebhookMessageCreate,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Ingests messages from Threads, external webhooks, or manual entry.
    Applies the authenticated user's sender filtering policies (ALLOW / BLOCK / DEFAULT),
    ensures idempotent persistence per user, and forwards to brokers if authorized.
    """
    ingest_res = await filter_engine.ingest_and_evaluate(
        user_id=current_user.id,
        payload=payload,
        db=session,
    )

    if ingest_res.is_err():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=ingest_res.unwrap_err(),
        )

    ingested_msg, should_forward, is_duplicate = ingest_res.unwrap()
    broker_res_data = None

    if should_forward and not is_duplicate:
        emit_res = await broker_emitter.emit_to_trading_engine(
            user_id=current_user.id,
            message=ingested_msg,
            owner_email=current_user.email,
        )
        if emit_res.is_ok():
            broker_res_data = emit_res.unwrap()
        else:
            broker_res_data = {"error": emit_res.unwrap_err()}

    result = MessageIngestResult(
        message=IngestedMessageResponse.model_validate(ingested_msg),
        forwarded=should_forward and not is_duplicate,
        broker_response=broker_res_data,
    )
    return ApiResponse(data=result)


@router.get(
    "/history",
    response_model=ApiResponse[PaginatedResponse[IngestedMessageResponse]],
    summary="Audit log of ingested messages",
)
async def get_messages_history(
    current_user: UserDep,
    session: SessionDep,
    platform: Optional[PlatformType] = Query(None, description="Filter by platform"),
    delivery_status: Optional[DeliveryStatus] = Query(None, description="Filter by status (EMITTED, BLOCKED, etc.)"),
    sender_id: Optional[str] = Query(None, description="Filter by sender ID"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """
    Retrieves the paginated audit trail of all messages received for this user,
    their extraction parameters, evaluated policies, and delivery statuses.
    """
    # 1. Total count query
    count_stmt = select(func.count()).select_from(IngestedMessage).where(
        IngestedMessage.user_id == current_user.id
    )
    if platform:
        count_stmt = count_stmt.where(IngestedMessage.platform == platform)
    if delivery_status:
        count_stmt = count_stmt.where(IngestedMessage.delivery_status == delivery_status)
    if sender_id:
        count_stmt = count_stmt.where(IngestedMessage.sender_id == sender_id)

    total_res = await session.exec(count_stmt)
    total = total_res.one()

    # 2. Paginated rows query
    stmt = select(IngestedMessage).where(IngestedMessage.user_id == current_user.id)
    if platform:
        stmt = stmt.where(IngestedMessage.platform == platform)
    if delivery_status:
        stmt = stmt.where(IngestedMessage.delivery_status == delivery_status)
    if sender_id:
        stmt = stmt.where(IngestedMessage.sender_id == sender_id)

    stmt = stmt.order_by(desc(IngestedMessage.created_at)).offset(offset).limit(limit)
    rows_res = await session.exec(stmt)
    messages = rows_res.all()

    items = [IngestedMessageResponse.model_validate(m) for m in messages]
    return ApiResponse(
        data=PaginatedResponse(
            items=items,
            total=total,
            limit=limit,
            offset=offset,
        )
    )


@router.get(
    "/{message_id}",
    response_model=ApiResponse[IngestedMessageResponse],
    summary="Get single message detail by ID",
)
async def get_message_detail(
    message_id: int,
    current_user: UserDep,
    session: SessionDep,
):
    """
    Retrieves complete metadata and execution status for a specific message.
    """
    stmt = select(IngestedMessage).where(
        IngestedMessage.id == message_id,
        IngestedMessage.user_id == current_user.id,
    )
    result = await session.exec(stmt)
    message = result.first()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Message #{message_id} not found.",
        )

    return ApiResponse(data=IngestedMessageResponse.model_validate(message))
