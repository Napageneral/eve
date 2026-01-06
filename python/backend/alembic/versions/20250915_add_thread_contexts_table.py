"""Add thread_contexts table to track first-class contexts per thread

Revision ID: 20250915_add_thread_contexts_table
Revises: 20250913_add_participants_and_idx_last_read_at
Create Date: 2025-09-15 21:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250915_add_thread_contexts_table'
down_revision: Union[str, None] = '20250913_add_participants_and_idx_last_read_at'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create thread_contexts table with portable types
    try:
        conn.execute(sa.text(
            """
            CREATE TABLE IF NOT EXISTS thread_contexts (
                id TEXT PRIMARY KEY,
                chat_id TEXT NOT NULL,
                context_type TEXT NOT NULL,
                context_id TEXT,
                context_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                added_by_message_id TEXT
            )
            """
        ))
    except Exception:
        # Best-effort; ignore if already exists
        pass

    # Helpful indexes for lookups
    try:
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_thread_contexts_chat ON thread_contexts (chat_id, added_at DESC)"))
    except Exception:
        try:
            conn.execute(sa.text("CREATE INDEX idx_thread_contexts_chat ON thread_contexts (chat_id, added_at DESC)"))
        except Exception:
            pass

    try:
        conn.execute(sa.text("CREATE INDEX IF NOT EXISTS idx_thread_contexts_kind ON thread_contexts (context_type, context_id)"))
    except Exception:
        try:
            conn.execute(sa.text("CREATE INDEX idx_thread_contexts_kind ON thread_contexts (context_type, context_id)"))
        except Exception:
            pass

    # De-dupe guard (last-write-wins via added_at ordering when querying)
    try:
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_thread_ctx_unique ON thread_contexts (chat_id, context_type, context_id)"
        ))
    except Exception:
        # Some engines (SQLite old) don't support IF NOT EXISTS for unique index
        try:
            conn.execute(sa.text(
                "CREATE UNIQUE INDEX uq_thread_ctx_unique ON thread_contexts (chat_id, context_type, context_id)"
            ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    # Best-effort drop (safe on engines that support it)
    try:
        conn.execute(sa.text("DROP TABLE IF EXISTS thread_contexts"))
    except Exception:
        try:
            conn.execute(sa.text("DROP TABLE thread_contexts"))
        except Exception:
            pass



