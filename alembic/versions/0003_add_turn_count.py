"""NestTalk: add turn_count to conversation_sessions

Revision ID: 0003_add_turn_count
Revises: 0002_memo_resource_ids
Create Date: 2026-04-09
"""
import sqlalchemy as sa
from alembic import op


# ── 修订标识 ────────────────────────────────────────────────
revision = "0003_add_turn_count"
down_revision = "0002_memo_resource_ids"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "nest_talk_conversation_sessions",
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0", comment="对话轮数"),
    )


def downgrade() -> None:
    op.drop_column("nest_talk_conversation_sessions", "turn_count")
