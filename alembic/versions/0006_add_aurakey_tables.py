"""Add aurakey tables

Revision ID: 0006_add_aurakey_tables
Revises: 0005_merge_heads
Create Date: 2026-05-05
"""
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op


# revision identifiers, used by Alembic.
revision = "0006_add_aurakey_tables"
down_revision = "0005_merge_heads"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ==================== Aurakey Gallery ====================
    op.create_table(
        "aurakey_gallery",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("category_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("author_nickname", sa.String(), nullable=True),
        sa.Column("author_avatar", sa.String(), nullable=True),
        sa.Column("thumb_url", sa.String(), nullable=True),
        sa.Column("image_url", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=True),
        sa.Column("aspect_ratio", sa.String(), nullable=True),
        sa.Column("like_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("view_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_gallery_user_id"), "aurakey_gallery", ["user_id"], unique=False)
    op.create_index(op.f("ix_aurakey_gallery_category_id"), "aurakey_gallery", ["category_id"], unique=False)

    # ==================== Aurakey Gallery Likes ====================
    op.create_table(
        "aurakey_gallery_likes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("gallery_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_gallery_likes_user_id"), "aurakey_gallery_likes", ["user_id"], unique=False)
    op.create_index(op.f("ix_aurakey_gallery_likes_gallery_id"), "aurakey_gallery_likes", ["gallery_id"], unique=False)

    # ==================== Aurakey Tasks ====================
    op.create_table(
        "aurakey_tasks",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("remote_task_id", sa.String(), nullable=True),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_url", sa.String(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("model_name", sa.String(), nullable=False),
        sa.Column("aspect_ratio", sa.String(), nullable=False),
        sa.Column("frozen_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failed_reason", sa.String(), nullable=True),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_tasks_user_id"), "aurakey_tasks", ["user_id"], unique=False)

    # ==================== Aurakey Gallery Categories ====================
    op.create_table(
        "aurakey_gallery_categories",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==================== Aurakey Model Options ====================
    op.create_table(
        "aurakey_model_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("model_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("cost", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("is_vip_only", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", sa.String(), nullable=False, server_default="on"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_model_options_model_id"), "aurakey_model_options", ["model_id"], unique=True)

    # ==================== Aurakey Aspect Ratio Options ====================
    op.create_table(
        "aurakey_aspect_ratio_options",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("ratio", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="on"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_aspect_ratio_options_ratio"), "aurakey_aspect_ratio_options", ["ratio"], unique=True)

    # ==================== Aurakey User Assets ====================
    op.create_table(
        "aurakey_user_assets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_vip", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("vip_type", sa.String(), nullable=True),
        sa.Column("vip_expire_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invite_code", sa.String(), nullable=False),
        sa.Column("invited_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_reward_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invited_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_user_assets_user_id"), "aurakey_user_assets", ["user_id"], unique=True)
    op.create_index(op.f("ix_aurakey_user_assets_invite_code"), "aurakey_user_assets", ["invite_code"], unique=True)

    # ==================== Aurakey Asset Logs ====================
    op.create_table(
        "aurakey_asset_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_asset_logs_user_id"), "aurakey_asset_logs", ["user_id"], unique=False)

    # ==================== Aurakey Products ====================
    op.create_table(
        "aurakey_products",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("price", sa.Integer(), nullable=False),
        sa.Column("original_price", sa.Integer(), nullable=True),
        sa.Column("point_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("tag", sa.String(), nullable=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    # ==================== Aurakey Orders ====================
    op.create_table(
        "aurakey_orders",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("order_no", sa.String(), nullable=False),
        sa.Column("product_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="waiting"),
        sa.Column("pay_method", sa.String(), nullable=False, server_default="wechat_mini"),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_aurakey_orders_user_id"), "aurakey_orders", ["user_id"], unique=False)
    op.create_index(op.f("ix_aurakey_orders_order_no"), "aurakey_orders", ["order_no"], unique=True)


def downgrade() -> None:
    # Drop tables in reverse order
    op.drop_index(op.f("ix_aurakey_orders_order_no"), table_name="aurakey_orders")
    op.drop_index(op.f("ix_aurakey_orders_user_id"), table_name="aurakey_orders")
    op.drop_table("aurakey_orders")

    op.drop_table("aurakey_products")

    op.drop_index(op.f("ix_aurakey_asset_logs_user_id"), table_name="aurakey_asset_logs")
    op.drop_table("aurakey_asset_logs")

    op.drop_index(op.f("ix_aurakey_user_assets_invite_code"), table_name="aurakey_user_assets")
    op.drop_index(op.f("ix_aurakey_user_assets_user_id"), table_name="aurakey_user_assets")
    op.drop_table("aurakey_user_assets")

    op.drop_index(op.f("ix_aurakey_aspect_ratio_options_ratio"), table_name="aurakey_aspect_ratio_options")
    op.drop_table("aurakey_aspect_ratio_options")

    op.drop_index(op.f("ix_aurakey_model_options_model_id"), table_name="aurakey_model_options")
    op.drop_table("aurakey_model_options")

    op.drop_table("aurakey_gallery_categories")

    op.drop_index(op.f("ix_aurakey_tasks_user_id"), table_name="aurakey_tasks")
    op.drop_table("aurakey_tasks")

    op.drop_index(op.f("ix_aurakey_gallery_likes_gallery_id"), table_name="aurakey_gallery_likes")
    op.drop_index(op.f("ix_aurakey_gallery_likes_user_id"), table_name="aurakey_gallery_likes")
    op.drop_table("aurakey_gallery_likes")

    op.drop_index(op.f("ix_aurakey_gallery_category_id"), table_name="aurakey_gallery")
    op.drop_index(op.f("ix_aurakey_gallery_user_id"), table_name="aurakey_gallery")
    op.drop_table("aurakey_gallery")

