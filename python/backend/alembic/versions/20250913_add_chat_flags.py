"""Add chat flags: is_starred, is_important, last_read_at

Revision ID: 20250913_add_chat_flags
Revises: 20250910_merge_heads
Create Date: 2025-09-13 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250913_add_chat_flags'
down_revision: Union[str, None] = '20250910_merge_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Raw SQL to remain portable
    conn = op.get_bind()
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN is_starred BOOLEAN DEFAULT FALSE"))
    except Exception:
        pass
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN is_important BOOLEAN DEFAULT FALSE"))
    except Exception:
        pass
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN last_read_at TIMESTAMP"))
    except Exception:
        pass


def downgrade() -> None:
    # Best-effort drops; may not be supported by all SQLite/engines
    conn = op.get_bind()
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats DROP COLUMN is_starred"))
    except Exception:
        pass
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats DROP COLUMN is_important"))
    except Exception:
        pass
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats DROP COLUMN last_read_at"))
    except Exception:
        pass


