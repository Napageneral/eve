"""Add unique index to conversations to prevent duplicates

Revision ID: 20260108_convo_unique
Revises: b955a1159fa5
Create Date: 2026-01-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '20260108_convo_unique'
down_revision = '6cb9de9acd8f'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add unique index to prevent duplicate conversations from ETL re-runs
    op.create_index(
        'idx_conversations_chat_start_end',
        'conversations',
        ['chat_id', 'start_time', 'end_time'],
        unique=True,
        if_not_exists=True
    )


def downgrade() -> None:
    op.drop_index('idx_conversations_chat_start_end', table_name='conversations')
