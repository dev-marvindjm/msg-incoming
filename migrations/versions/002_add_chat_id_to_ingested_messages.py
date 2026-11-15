"""002_add_chat_id_to_ingested_messages

Revision ID: 002_add_chat_id_to_ingested_messages
Revises: 001_initial_schema
Create Date: 2026-11-15 13:45:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '002_add_chat_id_to_ingested_messages'
down_revision: Union[str, None] = '001_initial_schema'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'ingested_messages',
        sa.Column('chat_id', sa.String(length=255), nullable=True),
    )

    bind = op.get_bind()
    if bind.dialect.name == 'postgresql':
        op.execute(
            sa.text(
                "UPDATE ingested_messages "
                "SET chat_id = metadata_json->>'chat_id' "
                "WHERE chat_id IS NULL AND metadata_json IS NOT NULL AND metadata_json->>'chat_id' IS NOT NULL"
            )
        )
    else:
        op.execute(
            sa.text(
                "UPDATE ingested_messages "
                "SET chat_id = CAST(json_extract(metadata_json, '$.chat_id') AS TEXT) "
                "WHERE chat_id IS NULL AND metadata_json IS NOT NULL AND json_extract(metadata_json, '$.chat_id') IS NOT NULL"
            )
        )

    op.drop_constraint('uq_message_per_user_platform_msg_id', 'ingested_messages', type_='unique')
    op.create_unique_constraint(
        'uq_message_per_user_platform_chat_msg_id',
        'ingested_messages',
        ['user_id', 'platform', 'chat_id', 'external_message_id'],
    )
    op.create_index(op.f('ix_ingested_messages_chat_id'), 'ingested_messages', ['chat_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ingested_messages_chat_id'), table_name='ingested_messages')
    op.drop_constraint('uq_message_per_user_platform_chat_msg_id', 'ingested_messages', type_='unique')
    op.create_unique_constraint(
        'uq_message_per_user_platform_msg_id',
        'ingested_messages',
        ['user_id', 'platform', 'external_message_id'],
    )
    op.drop_column('ingested_messages', 'chat_id')
