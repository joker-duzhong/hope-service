"""Create Ledger Mate AI chat tables.

Revision ID: 0017_ledger_mate_ai_chat
Revises: 0016_ledger_mate_initial
Create Date: 2026-07-16
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_ledger_mate_ai_chat"
down_revision = "0016_ledger_mate_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    audit = [sa.Column("id", uuid, primary_key=True), sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"))]
    op.create_table("ledger_mate_ai_sessions", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("title", sa.String(100), nullable=False))
    op.create_table("ledger_mate_ai_messages", *audit, sa.Column("session_id", uuid, nullable=False), sa.Column("user_id", uuid, nullable=False), sa.Column("role", sa.String(10), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("payload", sa.JSON()))
    op.create_table("ledger_mate_ai_record_references", *audit, sa.Column("user_id", uuid, nullable=False), sa.Column("session_id", uuid, nullable=False), sa.Column("message_id", uuid, nullable=False), sa.Column("record_id", uuid, nullable=False), sa.UniqueConstraint("message_id", "record_id", name="uq_ledger_mate_ai_message_record"))
    op.create_index("ix_lm_ai_sessions_user", "ledger_mate_ai_sessions", ["user_id"])
    op.create_index("ix_lm_ai_messages_session_user", "ledger_mate_ai_messages", ["session_id", "user_id"])
    op.create_index("ix_lm_ai_refs_lookup", "ledger_mate_ai_record_references", ["user_id", "session_id", "message_id", "record_id"])


def downgrade() -> None:
    for table in ("ledger_mate_ai_record_references", "ledger_mate_ai_messages", "ledger_mate_ai_sessions"):
        op.drop_table(table)
