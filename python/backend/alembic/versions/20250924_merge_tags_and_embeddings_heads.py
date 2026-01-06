"""Merge heads: embeddings blob change + add tags columns

Revision ID: 20250924_merge_heads
Revises: 20250922_embeddings_blob_only, 20250924_add_tags
Create Date: 2025-09-24 00:05:00.000000

"""
from typing import Sequence, Union

# Alembic identifiers
revision: str = '20250924_merge_heads'
down_revision: Union[str, tuple[str, ...], None] = (
    '20250922_embeddings_blob_only',
    '20250924_add_tags',
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Pure merge; nothing to do
    pass


def downgrade() -> None:
    # Pure merge; nothing to do on downgrade
    pass


