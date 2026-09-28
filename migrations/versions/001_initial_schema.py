"""001_initial_schema

Revision ID: 001_initial_schema
Revises: 
Create Date: 2026-09-27 20:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '001_initial_schema'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users table
    op.create_table(
        'users',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('hashed_password', sa.String(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_id'), 'users', ['id'], unique=False)
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # 2. sender_rules table
    op.create_table(
        'sender_rules',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.String(length=255), nullable=False),
        sa.Column('entity_name', sa.String(length=255), nullable=True),
        sa.Column('policy', sa.String(length=50), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('forward_to_brokers', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'platform', 'entity_id', name='uq_sender_rule_per_user_platform_entity'),
    )
    op.create_index(op.f('ix_sender_rules_user_id'), 'sender_rules', ['user_id'], unique=False)
    op.create_index(op.f('ix_sender_rules_platform'), 'sender_rules', ['platform'], unique=False)
    op.create_index(op.f('ix_sender_rules_entity_id'), 'sender_rules', ['entity_id'], unique=False)

    # 3. global_filter_configs table
    op.create_table(
        'global_filter_configs',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('allow_any_sender', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('strict_whitelist', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'platform', name='uq_global_filter_config_per_user_platform'),
    )
    op.create_index(op.f('ix_global_filter_configs_user_id'), 'global_filter_configs', ['user_id'], unique=False)

    # 4. ingested_messages table
    op.create_table(
        'ingested_messages',
        sa.Column('id', sa.Integer(), nullable=False, autoincrement=True),
        sa.Column('user_id', sa.UUID(), nullable=False),
        sa.Column('platform', sa.String(length=50), nullable=False),
        sa.Column('external_message_id', sa.String(length=255), nullable=False),
        sa.Column('sender_id', sa.String(length=255), nullable=False),
        sa.Column('sender_username', sa.String(length=255), nullable=True),
        sa.Column('raw_text', sa.Text(), nullable=False),
        sa.Column('metadata_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('delivery_status', sa.String(length=50), nullable=False),
        sa.Column('block_reason', sa.String(length=255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'platform', 'external_message_id', name='uq_message_per_user_platform_msg_id'),
    )
    op.create_index(op.f('ix_ingested_messages_user_id'), 'ingested_messages', ['user_id'], unique=False)
    op.create_index(op.f('ix_ingested_messages_platform'), 'ingested_messages', ['platform'], unique=False)
    op.create_index(op.f('ix_ingested_messages_external_message_id'), 'ingested_messages', ['external_message_id'], unique=False)
    op.create_index(op.f('ix_ingested_messages_sender_id'), 'ingested_messages', ['sender_id'], unique=False)
    op.create_index(op.f('ix_ingested_messages_created_at'), 'ingested_messages', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_table('ingested_messages')
    op.drop_table('global_filter_configs')
    op.drop_table('sender_rules')
    op.drop_table('users')
