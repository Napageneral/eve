"""Add chatbot_document_displays table for document UI displays

Revision ID: 20250926_add_document_displays
Revises: 20250924_merge_heads
Create Date: 2025-09-26 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250926_add_document_displays'
down_revision: Union[str, None] = '20250924_merge_heads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _column_exists(table: str, col: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {c['name'] for c in inspector.get_columns(table)}
    return col in existing


def _index_exists(index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        indexes = inspector.get_indexes('chatbot_document_displays')
        return any(ix.get('name') == index_name for ix in indexes)
    except Exception:
        return False


def upgrade() -> None:
    table = 'chatbot_document_displays'

    if not _table_exists(table):
        op.create_table(
            table,
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('document_id', sa.UUID(), nullable=False),
            sa.Column('document_created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('generated_code', sa.Text(), nullable=False),
            sa.Column('model_used', sa.String(), nullable=True),
            sa.Column('cost', sa.String(), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        )

    # Add any missing columns (idempotent safety for heterogeneous dev DBs)
    for name, col in (
        ('id', sa.Integer()),
        ('document_id', sa.UUID()),
        ('document_created_at', sa.DateTime(timezone=True)),
        ('generated_code', sa.Text()),
        ('model_used', sa.String()),
        ('cost', sa.String()),
        ('created_at', sa.DateTime(timezone=True)),
        ('updated_at', sa.DateTime(timezone=True)),
    ):
        if _table_exists(table) and not _column_exists(table, name):
            with op.batch_alter_table(table) as batch_op:
                # Keep new columns nullable to avoid failures on existing rows
                batch_op.add_column(sa.Column(name, col.type, nullable=True))

    # Create indexes if missing
    if _table_exists(table) and not _index_exists('ix_cdd_document_id'):
        try:
            op.create_index('ix_cdd_document_id', table, ['document_id'], unique=False)
        except Exception:
            pass

    if _table_exists(table) and not _index_exists('ix_cdd_docid_created_at'):
        try:
            op.create_index('ix_cdd_docid_created_at', table, ['document_id', 'document_created_at'], unique=False)
        except Exception:
            pass


def downgrade() -> None:
    table = 'chatbot_document_displays'
    try:
        op.drop_index('ix_cdd_docid_created_at', table_name=table)
    except Exception:
        pass
    try:
        op.drop_index('ix_cdd_document_id', table_name=table)
    except Exception:
        pass
    try:
        op.drop_table(table)
    except Exception:
        pass


