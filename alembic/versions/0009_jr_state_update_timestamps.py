"""Add precise JustRight state update timestamps.

Revision ID: 0009_jr_state_timestamps
Revises: 0008_jr_memo_cols
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op


revision = "0009_jr_state_timestamps"
down_revision = "0008_jr_memo_cols"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def upgrade() -> None:
    bind = op.get_bind()
    columns = [
        ("user1_mood_updated_at", "用户1心情更新时间"),
        ("user1_note_updated_at", "用户1留言更新时间"),
        ("user2_mood_updated_at", "用户2心情更新时间"),
        ("user2_note_updated_at", "用户2留言更新时间"),
    ]
    for column_name, comment in columns:
        if not _column_exists(bind, "just_right_couple_states", column_name):
            op.add_column(
                "just_right_couple_states",
                sa.Column(column_name, sa.DateTime(timezone=True), nullable=True, comment=comment),
            )


def downgrade() -> None:
    pass
