"""Add chatbot_document_reads table for per-user doc/display read state

Revision ID: 20250926_add_document_reads
Revises: 20250926_add_document_displays
Create Date: 2025-09-26 00:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250926_add_document_reads'
down_revision: Union[str, None] = '20250926_add_document_displays'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _index_exists(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    try:
        indexes = inspector.get_indexes(table_name)
        return any(ix.get('name') == index_name for ix in indexes)
    except Exception:
        return False


def upgrade() -> None:
    table = 'chatbot_document_reads'
    if not _table_exists(table):
        op.create_table(
            table,
            sa.Column('id', sa.Integer(), primary_key=True, nullable=False),
            sa.Column('user_id', sa.UUID(), nullable=False),
            sa.Column('document_id', sa.UUID(), nullable=False),
            sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('display_read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
            sa.UniqueConstraint('user_id', 'document_id', name='uq_cdr_user_doc'),
        )
    # Indexes
    if not _index_exists(table, 'ix_cdr_user_id'):
        op.create_index('ix_cdr_user_id', table, ['user_id'], unique=False)
    if not _index_exists(table, 'ix_cdr_document_id'):
        op.create_index('ix_cdr_document_id', table, ['document_id'], unique=False)


def downgrade() -> None:
    table = 'chatbot_document_reads'
    try:
        op.drop_index('ix_cdr_document_id', table_name=table)
    except Exception:
        pass
    try:
        op.drop_index('ix_cdr_user_id', table_name=table)
    except Exception:
        pass
    try:
        op.drop_table(table)
    except Exception:
        pass


