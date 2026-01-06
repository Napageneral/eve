"""add idempotency_key column to reports

Revision ID: 20250824_add_idempotency_key_to_reports
Revises: 6453e7b2f4b5
Create Date: 2025-08-24 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250824_add_idempotency_key_to_reports'
down_revision: Union[str, None] = '6453e7b2f4b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add nullable idempotency_key and a non-unique index for fast lookups
    with op.batch_alter_table('reports') as batch_op:
        batch_op.add_column(sa.Column('idempotency_key', sa.String(), nullable=True))
        batch_op.create_index('ix_reports_idempotency_key', ['idempotency_key'], unique=False)


def downgrade() -> None:
    with op.batch_alter_table('reports') as batch_op:
        try:
            batch_op.drop_index('ix_reports_idempotency_key')
        except Exception:
            pass
        batch_op.drop_column('idempotency_key')


