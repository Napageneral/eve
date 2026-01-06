"""Add document_contexts table to track contexts per document

Revision ID: 20251026_add_document_contexts_table
Revises: 20250927_userless_reads
Create Date: 2025-10-26

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20251026_add_document_contexts_table'
down_revision: Union[str, None] = '20250927_userless_reads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    
    # Create document_contexts table (mirrors thread_contexts)
    try:
        conn.execute(sa.text(
            """
            CREATE TABLE IF NOT EXISTS document_contexts (
                id TEXT PRIMARY KEY,
                document_id TEXT NOT NULL,
                context_type TEXT NOT NULL,
                context_id TEXT,
                context_name TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        ))
    except Exception:
        pass
    
    # Indexes for performance
    try:
        conn.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_document_contexts_doc ON document_contexts (document_id, added_at DESC)"
        ))
    except Exception:
        pass
    
    try:
        conn.execute(sa.text(
            "CREATE INDEX IF NOT EXISTS idx_document_contexts_kind ON document_contexts (context_type, context_id)"
        ))
    except Exception:
        pass
    
    # De-dupe constraint
    try:
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_ctx_unique ON document_contexts (document_id, context_type, context_id)"
        ))
    except Exception:
        pass


def downgrade() -> None:
    conn = op.get_bind()
    try:
        conn.execute(sa.text("DROP TABLE IF EXISTS document_contexts"))
    except Exception:
        pass

