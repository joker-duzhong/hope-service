"""Memo: rename image_urls → resource_ids

Revision ID: 0002_memo_resource_ids
Revises: 0001_int_pk_to_uuid
Create Date: 2026-04-07
"""
from alembic import op


# ── 修订标识 ────────────────────────────────────────────────
revision = "0002_memo_resource_ids"
down_revision = "0001_int_pk_to_uuid"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "just_right_memos",
        "image_urls",
        new_column_name="resource_ids",
        comment="关联资源ID列表",
    )


def downgrade() -> None:
    op.alter_column(
        "just_right_memos",
        "resource_ids",
        new_column_name="image_urls",
        comment="图片URL列表",
    )
