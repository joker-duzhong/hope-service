"""Add AuraKey entitlement fields and point grants.

Revision ID: 0011_aurakey_entitlements
Revises: 0010_jr_notification_dedupe
Create Date: 2026-05-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql
from uuid import uuid4


revision = "0011_aurakey_entitlements"
down_revision = "0010_jr_notification_dedupe"
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


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    bind = op.get_bind()
    if not _column_exists(bind, table_name, column.name):
        op.add_column(table_name, column)


def upgrade() -> None:
    bind = op.get_bind()

    _add_column_if_missing("aurakey_tasks", sa.Column("point_deductions", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")))

    product_columns = [
        sa.Column("vip_type", sa.String(), nullable=True),
        sa.Column("vip_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_days", sa.Integer(), nullable=True),
    ]
    for column in product_columns:
        _add_column_if_missing("aurakey_products", column)

    order_columns = [
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entitlement_start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("entitlement_expire_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("third_trade_no", sa.String(), nullable=True),
        sa.Column("product_name", sa.String(), nullable=True),
        sa.Column("product_type", sa.String(), nullable=True),
        sa.Column("vip_type", sa.String(), nullable=True),
        sa.Column("vip_level", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("point_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_amount", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("valid_days", sa.Integer(), nullable=True),
        sa.Column("granted_points", sa.Integer(), nullable=False, server_default="0"),
    ]
    for column in order_columns:
        _add_column_if_missing("aurakey_orders", column)

    op.execute(
        """
        UPDATE aurakey_orders AS o
        SET
            product_name = COALESCE(o.product_name, p.name),
            product_type = COALESCE(o.product_type, p.type),
            vip_type = COALESCE(o.vip_type, p.vip_type, p.tag, CASE WHEN p.type = 'vip' THEN p.name ELSE NULL END),
            vip_level = COALESCE(NULLIF(o.vip_level, 0), p.vip_level, 0),
            point_amount = COALESCE(NULLIF(o.point_amount, 0), p.point_amount, 0),
            bonus_amount = COALESCE(NULLIF(o.bonus_amount, 0), p.bonus_amount, 0),
            valid_days = COALESCE(o.valid_days, p.valid_days, CASE WHEN p.type = 'vip' THEN 30 ELSE NULL END),
            granted_points = COALESCE(NULLIF(o.granted_points, 0), p.point_amount + p.bonus_amount, 0),
            paid_at = CASE WHEN o.status = 'success' THEN COALESCE(o.paid_at, o.updated_at, o.created_at) ELSE o.paid_at END,
            entitlement_start_at = CASE
                WHEN o.status = 'success' THEN COALESCE(o.entitlement_start_at, o.updated_at, o.created_at)
                ELSE o.entitlement_start_at
            END,
            entitlement_expire_at = CASE
                WHEN o.status = 'success' AND COALESCE(o.entitlement_expire_at, NULL) IS NULL AND COALESCE(o.valid_days, p.valid_days, CASE WHEN p.type = 'vip' THEN 30 ELSE NULL END) IS NOT NULL
                    THEN COALESCE(o.updated_at, o.created_at) + (COALESCE(o.valid_days, p.valid_days, CASE WHEN p.type = 'vip' THEN 30 ELSE NULL END) || ' days')::interval
                ELSE o.entitlement_expire_at
            END
        FROM aurakey_products AS p
        WHERE o.product_id = p.id
        """
    )

    if not _table_exists(bind, "aurakey_point_grants"):
        op.create_table(
            "aurakey_point_grants",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("source_type", sa.String(), nullable=False),
            sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column("amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("remaining_amount", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("description", sa.String(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists(bind, "aurakey_point_grants", op.f("ix_aurakey_point_grants_user_id")):
        op.create_index(op.f("ix_aurakey_point_grants_user_id"), "aurakey_point_grants", ["user_id"], unique=False)
    if not _index_exists(bind, "aurakey_point_grants", op.f("ix_aurakey_point_grants_source_id")):
        op.create_index(op.f("ix_aurakey_point_grants_source_id"), "aurakey_point_grants", ["source_id"], unique=False)

    existing_grant_users = {
        row[0]
        for row in bind.execute(sa.text("SELECT DISTINCT user_id FROM aurakey_point_grants")).all()
    }
    legacy_assets = bind.execute(
        sa.text("SELECT id, user_id, balance FROM aurakey_user_assets WHERE balance > 0")
    ).mappings().all()
    legacy_rows = [
        {
            "id": uuid4(),
            "user_id": row["user_id"],
            "source_type": "legacy_balance",
            "source_id": row["id"],
            "amount": row["balance"],
            "remaining_amount": row["balance"],
            "expires_at": None,
            "description": "历史余额迁移",
            "is_deleted": False,
        }
        for row in legacy_assets
        if row["user_id"] not in existing_grant_users
    ]
    if legacy_rows:
        point_grants_table = sa.table(
            "aurakey_point_grants",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("user_id", postgresql.UUID(as_uuid=True)),
            sa.column("source_type", sa.String()),
            sa.column("source_id", postgresql.UUID(as_uuid=True)),
            sa.column("amount", sa.Integer()),
            sa.column("remaining_amount", sa.Integer()),
            sa.column("expires_at", sa.DateTime(timezone=True)),
            sa.column("description", sa.String()),
            sa.column("is_deleted", sa.Boolean()),
        )
        op.bulk_insert(point_grants_table, legacy_rows)

    if not _table_exists(bind, "aurakey_system_configs"):
        op.create_table(
            "aurakey_system_configs",
            sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("value", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
            sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
    if not _index_exists(bind, "aurakey_system_configs", op.f("ix_aurakey_system_configs_key")):
        op.create_index(op.f("ix_aurakey_system_configs_key"), "aurakey_system_configs", ["key"], unique=True)

    config_exists = bind.execute(
        sa.text("SELECT 1 FROM aurakey_system_configs WHERE key = 'aurakey_system_config'")
    ).first()
    if not config_exists:
        system_config_table = sa.table(
            "aurakey_system_configs",
            sa.column("id", postgresql.UUID(as_uuid=True)),
            sa.column("key", sa.String()),
            sa.column("value", sa.JSON()),
            sa.column("is_deleted", sa.Boolean()),
        )
        op.bulk_insert(
            system_config_table,
            [
                {
                    "id": uuid4(),
                    "key": "aurakey_system_config",
                    "value": {
                        "register_reward_points": 10,
                        "daily_sign_in_reward_points": 10,
                        "invite_reward_points": 50,
                        "default_vip_valid_days": 30,
                        "default_point_pack_valid_days": None,
                        "daily_free_points_reset_hour": 12,
                        "custom": {},
                    },
                    "is_deleted": False,
                }
            ],
        )


def downgrade() -> None:
    pass
