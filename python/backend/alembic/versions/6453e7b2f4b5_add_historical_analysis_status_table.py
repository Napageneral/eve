"""add_historical_analysis_status_table

Revision ID: 6453e7b2f4b5
Revises: 6554b713161e
Create Date: 2025-08-19 20:11:27.765280

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6453e7b2f4b5'
down_revision: Union[str, None] = '6554b713161e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create table to track one-time historic analysis status per user
    op.create_table(
        'historic_analysis_status',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),  # 'not_started'|'running'|'completed'|'failed'
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('total_conversations', sa.Integer(), nullable=True),
        sa.Column('analyzed_conversations', sa.Integer(), nullable=True),
        sa.Column('failed_conversations', sa.Integer(), nullable=True),
        sa.Column('run_id', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.UniqueConstraint('user_id', name='uq_historic_analysis_user_id'),
        sa.UniqueConstraint('run_id', name='uq_historic_analysis_run_id'),
    )


def downgrade() -> None:
    op.drop_table('historic_analysis_status')
