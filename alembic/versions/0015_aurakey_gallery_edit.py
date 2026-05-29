"""Add AuraKey task gallery edit fields.

Revision ID: 0015_aurakey_gallery_edit
Revises: 0014_aurakey_reference_images
Create Date: 2026-05-29
"""

import sqlalchemy as sa
from alembic import op


revision = "0015_aurakey_gallery_edit"
down_revision = "0014_aurakey_reference_images"
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

    if not _column_exists(bind, "aurakey_tasks", "show_title"):
        op.add_column("aurakey_tasks", sa.Column("show_title", sa.String(), nullable=True))
    if not _column_exists(bind, "aurakey_tasks", "template_prompt"):
        op.add_column("aurakey_tasks", sa.Column("template_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    pass
