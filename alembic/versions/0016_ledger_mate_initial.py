"""Create Ledger Mate tables.

Revision ID: 0016_ledger_mate_initial
Revises: 0015_aurakey_gallery_edit
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_ledger_mate_initial"
down_revision = "0015_aurakey_gallery_edit"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    audit = [sa.Column("id", uuid, primary_key=True), sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"))]
    op.create_table("ledger_mate_books", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("name", sa.String(50), nullable=False), sa.Column("currency", sa.String(3), nullable=False), sa.Column("timezone", sa.String(50), nullable=False), sa.UniqueConstraint("user_id", name="uq_ledger_mate_book_user"))
    op.create_table("ledger_mate_categories", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("record_type", sa.String(10), nullable=False), sa.Column("name", sa.String(30), nullable=False), sa.Column("icon", sa.String(50)), sa.Column("sort_order", sa.Integer(), nullable=False), sa.Column("is_enabled", sa.Boolean(), nullable=False), sa.Column("is_system", sa.Boolean(), nullable=False), sa.UniqueConstraint("user_id", "record_type", "name", name="uq_ledger_mate_category"))
    op.create_table("ledger_mate_payment_methods", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("name", sa.String(30), nullable=False), sa.Column("is_default", sa.Boolean(), nullable=False), sa.Column("is_enabled", sa.Boolean(), nullable=False), sa.UniqueConstraint("user_id", "name", name="uq_ledger_mate_payment_method"))
    op.create_table("ledger_mate_records", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("book_id", uuid, nullable=False), sa.Column("category_id", uuid, nullable=False), sa.Column("payment_method_id", uuid), sa.Column("record_type", sa.String(10), nullable=False), sa.Column("amount_cent", sa.Integer(), nullable=False), sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False), sa.Column("note", sa.Text()), sa.Column("idempotency_key", sa.String(100)), sa.Column("source", sa.String(20), nullable=False), sa.Column("import_batch_id", uuid))
    op.create_table("ledger_mate_operation_logs", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("record_id", uuid, nullable=False), sa.Column("action", sa.String(20), nullable=False), sa.Column("before_data", sa.JSON()), sa.Column("after_data", sa.JSON()))
    op.create_table("ledger_mate_import_batches", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("file_name", sa.String(255), nullable=False), sa.Column("rows", sa.JSON(), nullable=False), sa.Column("result", sa.JSON()))
    for table in ("ledger_mate_books", "ledger_mate_categories", "ledger_mate_payment_methods", "ledger_mate_records", "ledger_mate_operation_logs", "ledger_mate_import_batches"):
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def downgrade() -> None:
    for table in ("ledger_mate_import_batches", "ledger_mate_operation_logs", "ledger_mate_records", "ledger_mate_payment_methods", "ledger_mate_categories", "ledger_mate_books"):
        op.drop_table(table)
