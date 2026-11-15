from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from sqlmodel import Column, Field, Relationship, SQLModel, UniqueConstraint
from sqlalchemy.types import JSON
from sqlalchemy.dialects.postgresql import JSONB

from app.domain.enums import DeliveryStatus, PlatformType, SenderPolicy


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: UUID = Field(
        default_factory=uuid4,
        primary_key=True,
        index=True,
        nullable=False,
    )
    email: str = Field(unique=True, index=True, nullable=False, max_length=255)
    hashed_password: str = Field(nullable=False)
    is_active: bool = Field(default=True, nullable=False)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # Multi-tenant user-owned resources with cascade delete
    sender_rules: List["SenderRule"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    global_filter_configs: List["GlobalFilterConfig"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    ingested_messages: List["IngestedMessage"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )


class SenderRule(SQLModel, table=True):
    __tablename__ = "sender_rules"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "entity_id",
            name="uq_sender_rule_per_user_platform_entity"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )
    platform: PlatformType = Field(index=True, nullable=False)
    entity_id: str = Field(
        index=True,
        nullable=False,
        description="ID del canal, chat o usuario en la plataforma externa",
    )
    entity_name: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Username o título legible del canal",
    )
    policy: SenderPolicy = Field(
        default=SenderPolicy.DEFAULT,
        nullable=False,
    )
    is_active: bool = Field(default=True, nullable=False)
    forward_to_brokers: bool = Field(
        default=True,
        description="Si es False, se registra pero no se despacha al pipeline de órdenes",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Optional[User] = Relationship(back_populates="sender_rules")


class GlobalFilterConfig(SQLModel, table=True):
    __tablename__ = "global_filter_configs"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform",
            name="uq_global_filter_config_per_user_platform"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )
    platform: PlatformType = Field(index=True, nullable=False)
    allow_any_sender: bool = Field(
        default=False,
        nullable=False,
        description="Si es True, procesa y emite mensajes de cualquier emisor excepto los de la lista negra (BLOCK)",
    )
    strict_whitelist: bool = Field(
        default=False,
        nullable=False,
        description="Si es True, ignora todo lo que no esté explícitamente en ALLOW",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    user: Optional[User] = Relationship(back_populates="global_filter_configs")


class IngestedMessage(SQLModel, table=True):
    __tablename__ = "ingested_messages"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "platform", "chat_id", "external_message_id",
            name="uq_message_per_user_platform_chat_msg_id"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: UUID = Field(
        foreign_key="users.id",
        index=True,
        nullable=False,
        ondelete="CASCADE",
    )
    platform: PlatformType = Field(index=True, nullable=False)
    chat_id: Optional[str] = Field(
        default=None,
        index=True,
        nullable=True,
        description="ID del chat o canal de origen (ej. Telegram chat_id)",
    )
    external_message_id: str = Field(
        index=True,
        nullable=False,
        description="ID originado por Telegram (msg_id) o Threads (post_id)",
    )
    sender_id: str = Field(
        index=True,
        nullable=False,
        description="ID del emisor o canal que originó el mensaje",
    )
    sender_username: Optional[str] = Field(default=None, max_length=255)
    raw_text: str = Field(nullable=False)
    
    # Campo JSON compatible con PostgreSQL (JSONB nativo) y SQLite (JSON estructurado)
    metadata_json: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSON().with_variant(JSONB, "postgresql"), nullable=True),
    )
    
    delivery_status: DeliveryStatus = Field(
        default=DeliveryStatus.IGNORED,
        nullable=False,
    )
    block_reason: Optional[str] = Field(
        default=None,
        max_length=255,
        description="Explicación si delivery_status es BLOCKED o IGNORED",
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        index=True,
        nullable=False,
    )

    user: Optional[User] = Relationship(back_populates="ingested_messages")
