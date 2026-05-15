"""Add AuraKey reference images.

Revision ID: 0014_aurakey_reference_images
Revises: 0013_aurakey_image_resource_id
Create Date: 2026-05-15
"""

import sqlalchemy as sa
from alembic import op


revision = "0014_aurakey_reference_images"
down_revision = "0013_aurakey_image_resource_id"
branch_labels = None
depends_on = None


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names(schema="public")


def upgrade() -> None:
    bind = op.get_bind()
    if not _table_exists(bind, "aurakey_tasks"):
        return

    if not _column_exists(bind, "aurakey_tasks", "reference_image_ids"):
        op.add_column("aurakey_tasks", sa.Column("reference_image_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    pass
