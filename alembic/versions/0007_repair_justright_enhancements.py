"""Repair missing JustRight enhancement schema objects.

Revision ID: 0007_jr_repair
Revises: 0006_add_aurakey_tables
Create Date: 2026-05-06
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision = "0007_jr_repair"
down_revision = "0006_add_aurakey_tables"
branch_labels = None
depends_on = None


def _table_exists(bind, table_name: str) -> bool:
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names(schema="public")


def _column_exists(bind, table_name: str, column_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name, schema="public"))


def _index_exists(bind, table_name: str, index_name: str) -> bool:
    inspector = sa.inspect(bind)
    return any(index["name"] == index_name for index in inspector.get_indexes(table_name, schema="public"))


def upgrade() -> None:
    bind = op.get_bind()

    if _table_exists(bind, "just_right_memos"):
        if not _column_exists(bind, "just_right_memos", "is_pinned"):
            op.add_column(
                "just_right_memos",
                sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false", comment="是否置顶"),
            )
        if not _column_exists(bind, "just_right_memos", "pinned_at"):
            op.add_column(
                "just_right_memos",
                sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True, comment="置顶时间"),
            )

    if _table_exists(bind, "just_right_wishlist"):
        if not _column_exists(bind, "just_right_wishlist", "fulfilled_note"):
            op.add_column(
                "just_right_wishlist",
                sa.Column("fulfilled_note", sa.String(500), nullable=True, comment="实现备注"),
            )
        if not _column_exists(bind, "just_right_wishlist", "fulfilled_resource_ids"):
            op.add_column(
                "just_right_wishlist",
                sa.Column("fulfilled_resource_ids", postgresql.JSON, nullable=True, comment="实现照片资源ID列表"),
            )
        if not _column_exists(bind, "just_right_wishlist", "fulfilled_at"):
            op.add_column(
                "just_right_wishlist",
                sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True, comment="实现时间"),
            )

    if not _table_exists(bind, "just_right_mood_logs"):
        op.create_table(
            "just_right_mood_logs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("couple_id", postgresql.UUID(as_uuid=True), nullable=False, comment="情侣ID"),
            sa.Column("uid", postgresql.UUID(as_uuid=True), nullable=False, comment="用户ID"),
            sa.Column("mood", sa.String(50), nullable=False, comment="心情状态"),
            sa.Column("note", sa.String(500), nullable=True, comment="心情备注"),
            sa.Column("tags", postgresql.JSON, nullable=True, comment="标签列表"),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["couple_id"], ["just_right_couples.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if _table_exists(bind, "just_right_mood_logs"):
        if not _index_exists(bind, "just_right_mood_logs", op.f("ix_just_right_mood_logs_couple_id")):
            op.create_index(op.f("ix_just_right_mood_logs_couple_id"), "just_right_mood_logs", ["couple_id"], unique=False)
        if not _index_exists(bind, "just_right_mood_logs", op.f("ix_just_right_mood_logs_uid")):
            op.create_index(op.f("ix_just_right_mood_logs_uid"), "just_right_mood_logs", ["uid"], unique=False)
        if not _index_exists(bind, "just_right_mood_logs", op.f("ix_just_right_mood_logs_id")):
            op.create_index(op.f("ix_just_right_mood_logs_id"), "just_right_mood_logs", ["id"], unique=False)

    if not _table_exists(bind, "just_right_notifications"):
        op.create_table(
            "just_right_notifications",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("couple_id", postgresql.UUID(as_uuid=True), nullable=False, comment="情侣ID"),
            sa.Column("recipient_uid", postgresql.UUID(as_uuid=True), nullable=False, comment="接收者用户ID"),
            sa.Column("type", sa.String(50), nullable=False, comment="通知类型"),
            sa.Column("title", sa.String(200), nullable=False, comment="通知标题"),
            sa.Column("content", sa.Text(), nullable=False, comment="通知内容"),
            sa.Column("data", postgresql.JSON, nullable=True, comment="附加数据"),
            sa.Column("is_read", sa.Boolean(), nullable=False, server_default="false", comment="是否已读"),
            sa.Column("is_sent", sa.Boolean(), nullable=False, server_default="false", comment="是否已发送"),
            sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True, comment="发送时间"),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["couple_id"], ["just_right_couples.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if _table_exists(bind, "just_right_notifications"):
        if not _index_exists(bind, "just_right_notifications", op.f("ix_just_right_notifications_couple_id")):
            op.create_index(op.f("ix_just_right_notifications_couple_id"), "just_right_notifications", ["couple_id"], unique=False)
        if not _index_exists(bind, "just_right_notifications", op.f("ix_just_right_notifications_recipient_uid")):
            op.create_index(op.f("ix_just_right_notifications_recipient_uid"), "just_right_notifications", ["recipient_uid"], unique=False)
        if not _index_exists(bind, "just_right_notifications", op.f("ix_just_right_notifications_id")):
            op.create_index(op.f("ix_just_right_notifications_id"), "just_right_notifications", ["id"], unique=False)


def downgrade() -> None:
    pass
