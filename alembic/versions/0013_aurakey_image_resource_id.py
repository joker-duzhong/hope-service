"""Add AuraKey image resource id.

Revision ID: 0013_aurakey_image_resource_id
Revises: 0012_aurakey_task_pub_audit
Create Date: 2026-05-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0013_aurakey_image_resource_id"
down_revision = "0012_aurakey_task_pub_audit"
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


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "aurakey_tasks"):
        return

    if not _column_exists(bind, "aurakey_tasks", "image_resource_id"):
        op.add_column("aurakey_tasks", sa.Column("image_resource_id", postgresql.UUID(as_uuid=True), nullable=True))
    if not _index_exists(bind, "aurakey_tasks", op.f("ix_aurakey_tasks_image_resource_id")):
        op.create_index(op.f("ix_aurakey_tasks_image_resource_id"), "aurakey_tasks", ["image_resource_id"], unique=False)


def downgrade() -> None:
    pass
