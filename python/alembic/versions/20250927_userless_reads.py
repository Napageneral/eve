"""Create userless reads table and migrate data

Revision ID: 20250927_userless_reads
Revises: 20250926_add_document_reads
Create Date: 2025-09-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250927_userless_reads'
down_revision: Union[str, None] = '20250926_add_document_reads'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if 'chatbot_document_reads_simple' not in tables:
        op.create_table(
            'chatbot_document_reads_simple',
            sa.Column('document_id', sa.String(), primary_key=True, nullable=False),
            sa.Column('last_read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('display_read_at', sa.DateTime(timezone=True), nullable=True),
            sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
            sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        )

    # Migrate existing per-user rows by collapsing to latest timestamps per document_id
    if 'chatbot_document_reads' in tables:
        try:
            bind.execute(sa.text(
                """
                INSERT INTO chatbot_document_reads_simple (document_id, last_read_at, display_read_at, created_at, updated_at)
                SELECT document_id,
                       MAX(last_read_at) as last_read_at,
                       MAX(display_read_at) as display_read_at,
                       CURRENT_TIMESTAMP,
                       CURRENT_TIMESTAMP
                FROM chatbot_document_reads
                GROUP BY document_id
                ON CONFLICT (document_id) DO UPDATE SET
                    last_read_at = COALESCE(EXCLUDED.last_read_at, chatbot_document_reads_simple.last_read_at),
                    display_read_at = COALESCE(EXCLUDED.display_read_at, chatbot_document_reads_simple.display_read_at),
                    updated_at = CURRENT_TIMESTAMP
                """
            ))
        except Exception:
            pass


def downgrade() -> None:
    try:
        op.drop_table('chatbot_document_reads_simple')
    except Exception:
        pass


