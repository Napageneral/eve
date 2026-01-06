"""drop_context_definitions_and_selections

Revision ID: 6cb9de9acd8f
Revises: 298a2da70b04
Create Date: 2025-10-29 13:13:40.071314

Drop context_definitions and context_selections tables.

These tables are no longer used after migrating to the Eve system.
Eve manages context definitions and selections directly without database storage.

Historical note:
- context_definitions table was never populated (context_definitions.yaml never existed)
- context_selections table was created but unused
- PromptTemplate table is kept for historical analysis data
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6cb9de9acd8f'
down_revision: Union[str, None] = '298a2da70b04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop context_selections first (has FK to context_definitions)
    op.drop_table('context_selections')
    # Then drop context_definitions
    op.drop_table('context_definitions')


def downgrade() -> None:
    # Recreate context_definitions
    op.create_table(
        'context_definitions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('retrieval_function_ref', sa.String(), nullable=False),
        sa.Column('parameter_schema', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    
    # Recreate context_selections
    op.create_table(
        'context_selections',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('context_definition_id', sa.Integer(), nullable=False),
        sa.Column('parameter_values', sa.JSON(), nullable=True),
        sa.Column('resolved_content', sa.Text(), nullable=True),
        sa.Column('token_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['context_definition_id'], ['context_definitions.id'])
    )
