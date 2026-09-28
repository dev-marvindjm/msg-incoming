from datetime import datetime, timezone
from typing import Any, Dict, Generic, List, Optional, TypeVar
from uuid import UUID
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy

T = TypeVar("T")


# ---------------------------------------------------------
# Generic Response Envelopes
# ---------------------------------------------------------
class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class PaginatedResponse(BaseModel, Generic[T]):
    items: List[T]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------
# User Schemas
# ---------------------------------------------------------
class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=6, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    is_active: bool
    created_at: datetime


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ---------------------------------------------------------
# SenderRule Schemas
# ---------------------------------------------------------
class SenderRuleCreate(BaseModel):
    platform: PlatformType
    entity_id: str = Field(..., min_length=1, description="Unique ID of channel, group or user")
    entity_name: Optional[str] = Field(None, max_length=255, description="Username or human-readable title")
    policy: SenderPolicy = Field(default=SenderPolicy.DEFAULT)
    is_active: bool = Field(default=True)
    forward_to_brokers: bool = Field(default=True)


class SenderRuleUpdate(BaseModel):
    entity_name: Optional[str] = Field(None, max_length=255)
    policy: Optional[SenderPolicy] = None
    is_active: Optional[bool] = None
    forward_to_brokers: Optional[bool] = None


class SenderRuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    platform: PlatformType
    entity_id: str
    entity_name: Optional[str]
    policy: SenderPolicy
    is_active: bool
    forward_to_brokers: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------
# GlobalFilterConfig Schemas
# ---------------------------------------------------------
class GlobalFilterConfigUpdate(BaseModel):
    platform: PlatformType = Field(default=PlatformType.TELEGRAM)
    allow_any_sender: Optional[bool] = None
    strict_whitelist: Optional[bool] = None


class GlobalFilterConfigResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    platform: PlatformType
    allow_any_sender: bool
    strict_whitelist: bool
    updated_at: datetime


# ---------------------------------------------------------
# IngestedMessage & Webhook Schemas
# ---------------------------------------------------------
class WebhookMessageCreate(BaseModel):
    platform: PlatformType = Field(default=PlatformType.THREADS)
    external_message_id: str = Field(..., min_length=1)
    sender_id: str = Field(..., min_length=1)
    sender_username: Optional[str] = Field(None, max_length=255)
    raw_text: str = Field(..., min_length=1)
    metadata_json: Optional[Dict[str, Any]] = None


class IngestedMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: UUID
    platform: PlatformType
    external_message_id: str
    sender_id: str
    sender_username: Optional[str]
    raw_text: str
    metadata_json: Optional[Dict[str, Any]]
    delivery_status: DeliveryStatus
    block_reason: Optional[str]
    created_at: datetime


class MessageIngestResult(BaseModel):
    message: IngestedMessageResponse
    forwarded: bool
    broker_response: Optional[Dict[str, Any]] = None
