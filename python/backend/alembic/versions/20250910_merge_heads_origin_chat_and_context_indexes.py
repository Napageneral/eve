"""Merge heads for origin_chat and context indexes

Revision ID: 20250910_merge_heads
Revises: 20250910_add_origin_chat, f234fb832e9f
Create Date: 2025-09-10 18:31:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20250910_merge_heads'
down_revision: Union[str, tuple[str, str]] = ('20250910_add_origin_chat', 'f234fb832e9f')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # no-op merge
    pass


def downgrade() -> None:
    # no-op merge
    pass


