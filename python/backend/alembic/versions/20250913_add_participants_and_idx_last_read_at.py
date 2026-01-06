"""Add participants_json to chatbot_chats and index on last_read_at

Revision ID: 20250913_add_participants_and_idx_last_read_at
Revises: 20250913_add_chat_flags
Create Date: 2025-09-13 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250913_add_participants_and_idx_last_read_at'
down_revision: Union[str, None] = '20250913_add_chat_flags'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    # Add participants_json column (prefer JSON/JSONB; fallback to TEXT)
    try:
        # Try JSONB (Postgres)
        conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN participants_json JSONB"))
    except Exception:
        try:
            # Try generic JSON
            conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN participants_json JSON"))
        except Exception:
            try:
                # Fallback for SQLite
                conn.execute(sa.text("ALTER TABLE chatbot_chats ADD COLUMN participants_json TEXT"))
            except Exception:
                pass

    # Create index on last_read_at if possible
    try:
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_chatbot_chats_last_read_at ON chatbot_chats (last_read_at)"))
    except Exception:
        # SQLite older versions may not support IF NOT EXISTS for indexes
        try:
            conn.execute(sa.text("CREATE INDEX idx_chatbot_chats_last_read_at ON chatbot_chats (last_read_at)"))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    # Best-effort drop index/column (guarded for portability)
    try:
        conn.execute(sa.text("DROP INDEX IF EXISTS idx_chatbot_chats_last_read_at"))
    except Exception:
        try:
            conn.execute(sa.text("DROP INDEX idx_chatbot_chats_last_read_at"))
        except Exception:
            pass
    # Dropping columns is not supported universally (e.g., SQLite). Best-effort.
    try:
        conn.execute(sa.text("ALTER TABLE chatbot_chats DROP COLUMN participants_json"))
    except Exception:
        pass


