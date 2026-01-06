"""Add origin_chat_id to chatbot_documents

Revision ID: 20250910_add_origin_chat
Revises: 702d4ce46e13
Create Date: 2025-09-10 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250910_add_origin_chat'
down_revision: Union[str, None] = '702d4ce46e13'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c['name'] for c in inspector.get_columns('chatbot_documents')}
    if 'origin_chat_id' not in cols:
        # Use batch for SQLite safety when altering an existing table
        with op.batch_alter_table('chatbot_documents') as batch_op:
            batch_op.add_column(sa.Column('origin_chat_id', sa.UUID(), nullable=True))
        # Create the index outside the batch using raw SQL for maximum portability
        try:
            op.execute('CREATE INDEX IF NOT EXISTS ix_chatbot_documents_origin_chat_id ON chatbot_documents (origin_chat_id)')
        except Exception:
            pass


def downgrade() -> None:
    try:
        op.execute('DROP INDEX IF EXISTS ix_chatbot_documents_origin_chat_id')
    except Exception:
        pass
    with op.batch_alter_table('chatbot_documents') as batch_op:
        try:
            batch_op.drop_column('origin_chat_id')
        except Exception:
            pass


