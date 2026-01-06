"""Add suggestions_history table for Smart Cues telemetry

Revision ID: 20250921_add_suggestions_history_table
Revises: 20250915_add_thread_contexts_table
Create Date: 2025-09-21 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250921_add_suggestions_history_table'
down_revision: Union[str, None] = '20250915_add_thread_contexts_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Create suggestions_history table (engine-portable, best-effort idempotent)
    try:
        conn.execute(sa.text(
            """
            CREATE TABLE IF NOT EXISTS suggestions_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                suggestion_id TEXT NOT NULL,
                title TEXT,
                subtitle TEXT,
                rationale TEXT,
                source_refs TEXT,
                payload_refs TEXT,
                context_selection_id INTEGER,
                suggested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                dismissed_at TIMESTAMP NULL,
                accepted_at TIMESTAMP NULL
            )
            """
        ))
    except Exception:
        # Fallback without IF NOT EXISTS
        try:
            conn.execute(sa.text(
                """
                CREATE TABLE suggestions_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    suggestion_id TEXT NOT NULL,
                    title TEXT,
                    subtitle TEXT,
                    rationale TEXT,
                    source_refs TEXT,
                    payload_refs TEXT,
                    context_selection_id INTEGER,
                    suggested_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    dismissed_at TIMESTAMP NULL,
                    accepted_at TIMESTAMP NULL
                )
                """
            ))
        except Exception:
            pass

    # Helpful indexes
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_sugg_hist_chat_time ON suggestions_history (chat_id, suggested_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_sugg_hist_chat_sugg ON suggestions_history (chat_id, suggestion_id, suggested_at DESC)",
    ]:
        try:
            conn.execute(sa.text(ddl))
        except Exception:
            # Retry without IF NOT EXISTS for engines lacking support
            try:
                conn.execute(sa.text(ddl.replace(" IF NOT EXISTS", "")))
            except Exception:
                pass


def downgrade() -> None:
    conn = op.get_bind()
    # Best-effort drops
    for ddl in [
        "DROP INDEX IF EXISTS idx_sugg_hist_chat_sugg",
        "DROP INDEX IF EXISTS idx_sugg_hist_chat_time",
    ]:
        try:
            conn.execute(sa.text(ddl))
        except Exception:
            try:
                conn.execute(sa.text(ddl.replace(" IF EXISTS", "")))
            except Exception:
                pass

    try:
        conn.execute(sa.text("DROP TABLE IF EXISTS suggestions_history"))
    except Exception:
        try:
            conn.execute(sa.text("DROP TABLE suggestions_history"))
        except Exception:
            pass


