"""Merge migration heads

Revision ID: 0005_merge_heads
Revises: 0004_justright_enhancements, 59fa4de6b8ee
Create Date: 2026-05-05
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0005_merge_heads'
down_revision = ('0004_justright_enhancements', '59fa4de6b8ee')
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
