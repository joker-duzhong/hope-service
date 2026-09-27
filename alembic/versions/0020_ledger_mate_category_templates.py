"""Add Ledger Mate global category templates.

Revision ID: 0020_ledger_mate_cat_templates
Revises: 0019_core_user_identities
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0020_ledger_mate_cat_templates"
down_revision = "0019_core_user_identities"
branch_labels = None
depends_on = None


UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "ledger_mate_category_templates",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("record_type", sa.String(10), nullable=False),
        sa.Column("name", sa.String(30), nullable=False),
        sa.Column("icon", sa.String(500), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("record_type", "name", name="uq_ledger_mate_category_template"),
    )
    op.create_index(
        "ix_ledger_mate_category_templates_record_type",
        "ledger_mate_category_templates",
        ["record_type"],
    )
    op.alter_column(
        "ledger_mate_categories",
        "icon",
        existing_type=sa.String(50),
        type_=sa.String(500),
        existing_nullable=True,
    )

    templates = sa.table(
        "ledger_mate_category_templates",
        sa.column("id", UUID),
        sa.column("record_type", sa.String(10)),
        sa.column("name", sa.String(30)),
        sa.column("sort_order", sa.Integer()),
        sa.column("is_enabled", sa.Boolean()),
    )
    defaults = {
        "expense": ["餐饮", "交通", "购物", "居住", "医疗", "娱乐", "其他"],
        "income": ["工资", "奖金", "兼职", "理财", "其他"],
    }
    import uuid
    op.bulk_insert(
        templates,
        [
            {
                "id": uuid.uuid5(uuid.NAMESPACE_URL, f"hope-ledger-mate:{record_type}:{name}"),
                "record_type": record_type,
                "name": name,
                "sort_order": index,
                "is_enabled": True,
            }
            for record_type, names in defaults.items()
            for index, name in enumerate(names)
        ],
    )


def downgrade() -> None:
    op.alter_column(
        "ledger_mate_categories",
        "icon",
        existing_type=sa.String(500),
        type_=sa.String(50),
        existing_nullable=True,
    )
    op.drop_index(
        "ix_ledger_mate_category_templates_record_type",
        table_name="ledger_mate_category_templates",
    )
    op.drop_table("ledger_mate_category_templates")
