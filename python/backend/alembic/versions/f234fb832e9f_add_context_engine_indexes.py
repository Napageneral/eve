"""add context engine indexes

Revision ID: f234fb832e9f
Revises: 702d4ce46e13
Create Date: 2025-09-07 20:11:20.471974

NOTE: Originally pointed to 8f7e2c1d3abc (deleted during cleanup), changed to 702d4ce46e13

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = 'f234fb832e9f'
down_revision: Union[str, None] = '702d4ce46e13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Minimal, non-destructive migration to add context-engine indexes
    # SQLite-safe: only create new indexes; do not alter column types or drop legacy objects.
    try:
        op.create_index('idx_conversations_chat_start', 'conversations', ['chat_id', 'start_time'], unique=False)
    except Exception:
        pass
    try:
        op.create_index('idx_entities_conv_title', 'entities', ['conversation_id', 'title'], unique=False)
    except Exception:
        pass
    try:
        op.create_index('idx_topics_conv_title', 'topics', ['conversation_id', 'title'], unique=False)
    except Exception:
        pass
    try:
        op.create_index('idx_emotions_conv_type', 'emotions', ['conversation_id', 'emotion_type'], unique=False)
    except Exception:
        pass


def downgrade() -> None:
    # Drop only the indexes added by this migration
    try:
        op.drop_index('idx_emotions_conv_type', table_name='emotions')
    except Exception:
        pass
    try:
        op.drop_index('idx_topics_conv_title', table_name='topics')
    except Exception:
        pass
    try:
        op.drop_index('idx_entities_conv_title', table_name='entities')
    except Exception:
        pass
    try:
        op.drop_index('idx_conversations_chat_start', table_name='conversations')
    except Exception:
        pass
