"""JustRight: enhancements for memo, wishlist, mood logs and notifications

Revision ID: 0004_justright_enhancements
Revises: 0003_add_turn_count
Create Date: 2026-05-05
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


# ── 修订标识 ────────────────────────────────────────────────
revision = "0004_justright_enhancements"
down_revision = "0003_add_turn_count"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================== 1. Memo 表添加置顶字段 ====================
    op.add_column(
        "just_right_memos",
        sa.Column("is_pinned", sa.Boolean(), nullable=False, server_default="false", comment="是否置顶"),
    )
    op.add_column(
        "just_right_memos",
        sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True, comment="置顶时间"),
    )

    # ==================== 2. WishlistItem 表添加实现记录字段 ====================
    op.add_column(
        "just_right_wishlist",
        sa.Column("fulfilled_note", sa.String(500), nullable=True, comment="实现备注"),
    )
    op.add_column(
        "just_right_wishlist",
        sa.Column("fulfilled_resource_ids", postgresql.JSON, nullable=True, comment="实现照片资源ID列表"),
    )
    op.add_column(
        "just_right_wishlist",
        sa.Column("fulfilled_at", sa.DateTime(timezone=True), nullable=True, comment="实现时间"),
    )

    # ==================== 3. 创建心情日记表 ====================
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
    op.create_index(op.f("ix_just_right_mood_logs_couple_id"), "just_right_mood_logs", ["couple_id"], unique=False)
    op.create_index(op.f("ix_just_right_mood_logs_uid"), "just_right_mood_logs", ["uid"], unique=False)
    op.create_index(op.f("ix_just_right_mood_logs_id"), "just_right_mood_logs", ["id"], unique=False)

    # ==================== 4. 创建通知表 ====================
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
    op.create_index(op.f("ix_just_right_notifications_couple_id"), "just_right_notifications", ["couple_id"], unique=False)
    op.create_index(op.f("ix_just_right_notifications_recipient_uid"), "just_right_notifications", ["recipient_uid"], unique=False)
    op.create_index(op.f("ix_just_right_notifications_id"), "just_right_notifications", ["id"], unique=False)


def downgrade() -> None:
    # 删除通知表
    op.drop_index(op.f("ix_just_right_notifications_id"), table_name="just_right_notifications")
    op.drop_index(op.f("ix_just_right_notifications_recipient_uid"), table_name="just_right_notifications")
    op.drop_index(op.f("ix_just_right_notifications_couple_id"), table_name="just_right_notifications")
    op.drop_table("just_right_notifications")

    # 删除心情日记表
    op.drop_index(op.f("ix_just_right_mood_logs_id"), table_name="just_right_mood_logs")
    op.drop_index(op.f("ix_just_right_mood_logs_uid"), table_name="just_right_mood_logs")
    op.drop_index(op.f("ix_just_right_mood_logs_couple_id"), table_name="just_right_mood_logs")
    op.drop_table("just_right_mood_logs")

    # 删除心愿单实现记录字段
    op.drop_column("just_right_wishlist", "fulfilled_at")
    op.drop_column("just_right_wishlist", "fulfilled_resource_ids")
    op.drop_column("just_right_wishlist", "fulfilled_note")

    # 删除备忘录置顶字段
    op.drop_column("just_right_memos", "pinned_at")
    op.drop_column("just_right_memos", "is_pinned")
