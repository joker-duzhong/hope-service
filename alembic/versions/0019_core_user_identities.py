"""Add platform user identities and token versioning.

Revision ID: 0019_core_user_identities
Revises: 0018_teacher_logbook
Create Date: 2026-08-27
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_core_user_identities"
down_revision = "0018_teacher_logbook"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "core_users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )
    op.create_table(
        "core_user_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("core_users.id"), nullable=False),
        sa.Column("provider", sa.String(50), nullable=False, server_default="wechat"),
        sa.Column("provider_app_id", sa.String(64), nullable=False),
        sa.Column("subject", sa.String(128), nullable=False),
        sa.Column("unionid", sa.String(64), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("meta_data", postgresql.JSONB(), nullable=True),
        sa.UniqueConstraint("provider", "provider_app_id", "subject", name="uq_user_identity_provider_subject"),
    )
    op.create_index("ix_core_user_identities_user_id", "core_user_identities", ["user_id"])
    op.create_index("ix_core_user_identities_unionid", "core_user_identities", ["unionid"])


def downgrade() -> None:
    op.drop_index("ix_core_user_identities_unionid", table_name="core_user_identities")
    op.drop_index("ix_core_user_identities_user_id", table_name="core_user_identities")
    op.drop_table("core_user_identities")
    op.drop_column("core_users", "token_version")
