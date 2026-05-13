"""Add AuraKey task publication audit fields.

Revision ID: 0012_aurakey_task_pub_audit
Revises: 0011_aurakey_entitlements
Create Date: 2026-05-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0012_aurakey_task_pub_audit"
down_revision = "0011_aurakey_entitlements"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names(schema="public")


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name, schema="public"))


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if _table_exists(bind, table_name) and not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing("aurakey_tasks", sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True))
    _add_column_if_missing("aurakey_tasks", sa.Column("publish_status", sa.String(), nullable=False, server_default="approved"))
    _add_column_if_missing("aurakey_tasks", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    _add_column_if_missing("aurakey_tasks", sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"))
    _add_column_if_missing("aurakey_tasks", sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"))

    if _table_exists(bind, "aurakey_tasks"):
        op.execute(
            """
            UPDATE aurakey_tasks
            SET published_at = COALESCE(published_at, updated_at, created_at),
                publish_status = COALESCE(publish_status, 'approved')
            WHERE is_published = true
              AND status = 'success'
            """
        )
        if not _index_exists(bind, "aurakey_tasks", op.f("ix_aurakey_tasks_category_id")):
            op.create_index(op.f("ix_aurakey_tasks_category_id"), "aurakey_tasks", ["category_id"], unique=False)
        if not _index_exists(bind, "aurakey_tasks", op.f("ix_aurakey_tasks_publish_status")):
            op.create_index(op.f("ix_aurakey_tasks_publish_status"), "aurakey_tasks", ["publish_status"], unique=False)
        if not _index_exists(bind, "aurakey_tasks", op.f("ix_aurakey_tasks_published_at")):
            op.create_index(op.f("ix_aurakey_tasks_published_at"), "aurakey_tasks", ["published_at"], unique=False)


def downgrade() -> None:
    pass
