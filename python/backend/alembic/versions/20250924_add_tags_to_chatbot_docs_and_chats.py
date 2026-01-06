"""Add tags columns to chatbot_documents and chatbot_chats

Revision ID: 20250924_add_tags
Revises: 20250910_add_origin_chat
Create Date: 2025-09-24 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250924_add_tags'
down_revision: Union[str, None] = '20250910_add_origin_chat'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_column_if_missing(table: str, col: str, type_: sa.types.TypeEngine) -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns(table)}
    if col not in existing:
        # Use batch_alter_table for SQLite compatibility when rewriting tables
        with op.batch_alter_table(table) as batch_op:
            batch_op.add_column(sa.Column(col, type_, nullable=True))


def upgrade() -> None:
    # Store tags as TEXT containing JSON array of strings
    _add_column_if_missing('chatbot_documents', 'tags', sa.Text())
    _add_column_if_missing('chatbot_chats', 'tags', sa.Text())


def downgrade() -> None:
    # Best-effort drop; ignore if missing
    try:
        with op.batch_alter_table('chatbot_documents') as batch_op:
            batch_op.drop_column('tags')
    except Exception:
        pass
    try:
        with op.batch_alter_table('chatbot_chats') as batch_op:
            batch_op.drop_column('tags')
    except Exception:
        pass


