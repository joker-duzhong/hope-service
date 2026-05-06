"""Add missing memo JSON columns.

Revision ID: 0008_jr_memo_cols
Revises: 0007_jr_repair
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0008_jr_memo_cols"
down_revision = "0007_jr_repair"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def upgrade() -> None:
    bind = op.get_bind()

    if not _column_exists(bind, "just_right_memos", "likes"):
        op.add_column(
            "just_right_memos",
            sa.Column("likes", postgresql.JSON, nullable=True, comment="点赞用户ID列表"),
        )

    if not _column_exists(bind, "just_right_memos", "comments"):
        op.add_column(
            "just_right_memos",
            sa.Column("comments", postgresql.JSON, nullable=True, comment="评论列表"),
        )


def downgrade() -> None:
    pass
