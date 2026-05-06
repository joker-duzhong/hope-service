"""Add explicit dedupe key for JustRight notifications.

Revision ID: 0010_jr_notification_dedupe
Revises: 0009_jr_state_timestamps
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op


revision = "0010_jr_notification_dedupe"
down_revision = "0009_jr_state_timestamps"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name, schema="public"))


def upgrade() -> None:
    bind = op.get_bind()
    if not _column_exists(bind, "just_right_notifications", "dedupe_key"):
        op.add_column(
            "just_right_notifications",
            sa.Column("dedupe_key", sa.String(length=200), nullable=True, comment="通知去重键"),
        )
    index_name = op.f("ix_just_right_notifications_dedupe_key")
    if not _index_exists(bind, "just_right_notifications", index_name):
        op.create_index(index_name, "just_right_notifications", ["dedupe_key"], unique=False)


def downgrade() -> None:
    pass
