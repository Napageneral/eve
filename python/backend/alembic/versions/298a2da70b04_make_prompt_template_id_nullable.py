"""make_prompt_template_id_nullable

Revision ID: 298a2da70b04
Revises: b955a1159fa5
Create Date: 2025-10-27 10:05:51.188442

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '298a2da70b04'
down_revision: Union[str, None] = 'b955a1159fa5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Make prompt_template_id nullable in conversation_analyses table.
    
    This allows tracking Eve prompts via eve_prompt_id without needing
    database prompt template entries.
    
    SQLite requires table recreation to change NOT NULL constraints.
    """
    # Use batch mode to recreate table with nullable prompt_template_id
    with op.batch_alter_table('conversation_analyses', schema=None) as batch_op:
        batch_op.alter_column('prompt_template_id',
                              existing_type=sa.INTEGER(),
                              type_=sa.INTEGER(),
                              nullable=True,
                              existing_nullable=False)


def downgrade() -> None:
    """
    Revert prompt_template_id back to NOT NULL.
    
    Note: This will fail if any rows have NULL prompt_template_id.
    You must populate those rows with valid IDs before downgrading.
    """
    with op.batch_alter_table('conversation_analyses', schema=None) as batch_op:
        batch_op.alter_column('prompt_template_id',
                              existing_type=sa.INTEGER(),
                              type_=sa.INTEGER(),
                              nullable=False,
                              existing_nullable=True)
