"""add_eve_prompt_id_make_fk_nullable

Revision ID: b955a1159fa5
Revises: 20251026_add_document_contexts_table
Create Date: 2025-10-26 21:54:27.223821

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b955a1159fa5'
down_revision: Union[str, None] = '20251026_add_document_contexts_table'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add eve_prompt_id column to track Eve prompts directly.
    Make prompt_template_id nullable for transition to Eve-only tracking.
    
    SQLite requires table recreation to change NOT NULL constraints.
    """
    # SQLite table recreation pattern
    with op.batch_alter_table('conversation_analyses', schema=None) as batch_op:
        # Add new column
        batch_op.add_column(sa.Column('eve_prompt_id', sa.String(), nullable=True))
        
        # Make prompt_template_id nullable (requires table recreation in SQLite)
        batch_op.alter_column('prompt_template_id',
                              existing_type=sa.INTEGER(),
                              nullable=True)
    
    # Add index on eve_prompt_id for query performance
    op.create_index('ix_conversation_analyses_eve_prompt_id', 'conversation_analyses', ['eve_prompt_id'])


def downgrade() -> None:
    """Revert eve_prompt_id column addition"""
    op.drop_index('ix_conversation_analyses_eve_prompt_id', table_name='conversation_analyses')
    op.drop_column('conversation_analyses', 'eve_prompt_id')
