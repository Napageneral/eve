"""Add embeddings table for semantic search

Revision ID: 20250921_add_embeddings_table
Revises: 20250921_add_suggestions_history_table
Create Date: 2025-09-21 22:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250921_add_embeddings_table'
down_revision: Union[str, None] = '20250921_add_suggestions_history_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # Core table (portable SQL)
    try:
        conn.execute(sa.text(
            """
            CREATE TABLE IF NOT EXISTS embeddings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                chat_id TEXT,
                conversation_id TEXT,
                message_id TEXT,
                source_type TEXT NOT NULL,
                source_id TEXT NOT NULL,
                label TEXT,
                chunk_index INTEGER DEFAULT 0,
                model TEXT NOT NULL,
                dim INTEGER NOT NULL,
                vector_json TEXT NOT NULL,
                text_hash TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        ))
    except Exception:
        try:
            conn.execute(sa.text(
                """
                CREATE TABLE embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    chat_id TEXT,
                    conversation_id TEXT,
                    message_id TEXT,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    label TEXT,
                    chunk_index INTEGER DEFAULT 0,
                    model TEXT NOT NULL,
                    dim INTEGER NOT NULL,
                    vector_json TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """
            ))
        except Exception:
            pass

    # Useful indexes
    for ddl in [
        "CREATE INDEX IF NOT EXISTS idx_embeddings_user_chat ON embeddings (user_id, chat_id)",
        "CREATE INDEX IF NOT EXISTS idx_embeddings_source ON embeddings (source_type, source_id)",
        "CREATE INDEX IF NOT EXISTS idx_embeddings_model_dim ON embeddings (model, dim)",
        "CREATE INDEX IF NOT EXISTS idx_embeddings_hash ON embeddings (text_hash)",
    ]:
        try:
            conn.execute(sa.text(ddl))
        except Exception:
            try:
                conn.execute(sa.text(ddl.replace(" IF NOT EXISTS", "")))
            except Exception:
                pass

    # Uniqueness guard via partial emulation
    try:
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_embeddings_unique ON embeddings (user_id, source_type, source_id, chunk_index, model, dim, text_hash)"
        ))
    except Exception:
        try:
            conn.execute(sa.text(
                "CREATE UNIQUE INDEX uq_embeddings_unique ON embeddings (user_id, source_type, source_id, chunk_index, model, dim, text_hash)"
            ))
        except Exception:
            pass


def downgrade() -> None:
    conn = op.get_bind()
    for ddl in [
        "DROP INDEX IF EXISTS uq_embeddings_unique",
        "DROP INDEX IF EXISTS idx_embeddings_hash",
        "DROP INDEX IF EXISTS idx_embeddings_model_dim",
        "DROP INDEX IF EXISTS idx_embeddings_source",
        "DROP INDEX IF EXISTS idx_embeddings_user_chat",
    ]:
        try:
            conn.execute(sa.text(ddl))
        except Exception:
            try:
                conn.execute(sa.text(ddl.replace(" IF EXISTS", "")))
            except Exception:
                pass

    try:
        conn.execute(sa.text("DROP TABLE IF EXISTS embeddings"))
    except Exception:
        try:
            conn.execute(sa.text("DROP TABLE embeddings"))
        except Exception:
            pass


