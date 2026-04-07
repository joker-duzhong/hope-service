"""迁移主键与外键：Integer 自增 → UUID (PostgreSQL)

Revision ID: 0001_int_pk_to_uuid
Revises: —
Create Date: 2026-04-07

迁移策略（严格保证引用完整性）：
  ① 启用 pgcrypto，为全部表添加 new_id UUID 列并填充
  ② 为全部 FK 列添加对应 new_xxx UUID 列并通过 JOIN 回填
  ③ 将 core_user_roles 的 UUID 映射保存到临时表
  ④ 删除旧的 core_user_roles
  ⑤ 对所有普通表执行 PK 切换（删 id、rename new_id → id、重建 PK）
  ⑥ 对所有 FK 列执行切换（删旧整数列、rename new_xxx → xxx、重建 FK）
  ⑦ 用正确 UUID 重建 core_user_roles，并从临时表恢复数据
  ⑧ 恢复外键触发器
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


# ── 修订标识 ────────────────────────────────────────────────
revision = "0001_int_pk_to_uuid"
down_revision = None
branch_labels = None
depends_on = None


# ══════════════════════════════════════════════════════════════
# 工具函数（模块级，可在 upgrade 中复用）
# ══════════════════════════════════════════════════════════════

def _add_uuid_pk(table: str) -> None:
    """为表添加 new_id UUID 列，用 gen_random_uuid() 填充后设 NOT NULL。"""
    op.add_column(table, sa.Column("new_id", UUID(as_uuid=True), nullable=True))
    op.execute(sa.text(f"UPDATE {table} SET new_id = gen_random_uuid()"))
    op.alter_column(table, "new_id", nullable=False)


def _add_uuid_fk_col(
    table: str,
    old_col: str,
    new_col: str,
    ref_table: str,
    nullable: bool = False,
) -> None:
    """
    为整数 FK 列添加对应的 UUID 版本，通过 JOIN 父表 new_id 回填。
    nullable=False  → 要求每行都能找到父行（INNER 语义）
    nullable=True   → 允许父行不存在（SET NULL 场景）
    """
    op.add_column(table, sa.Column(new_col, UUID(as_uuid=True), nullable=True))
    op.execute(
        sa.text(
            f"""
            UPDATE {table} t
            SET    {new_col} = p.new_id
            FROM   {ref_table} p
            WHERE  t.{old_col} = p.id
            """
        )
    )
    if not nullable:
        op.alter_column(table, new_col, nullable=False)


def _finalize_pk(table: str) -> None:
    """
    将表的主键从旧 INTEGER id 切换为已填充的 UUID new_id：
      1. 删除旧 PK 约束（CASCADE 自动级联删除依赖的 FK 约束）
      2. 删除旧 id 列
      3. new_id → id
      4. 重建 PK 约束 + 索引
    注意：CASCADE 删除的 FK 约束会在 _finalize_fk_col 中重新创建。
    """
    conn = op.get_bind()
    conn.execute(sa.text(f"ALTER TABLE {table} DROP CONSTRAINT {table}_pkey CASCADE"))
    op.drop_column(table, "id")
    op.alter_column(table, "new_id", new_column_name="id")
    op.create_primary_key(f"{table}_pkey", table, ["id"])
    op.create_index(f"ix_{table}_id", table, ["id"])


def _finalize_fk_col(
    table: str,
    new_col: str,
    final_col: str,
    ref_table: str,
    *,
    ondelete: str = "NO ACTION",
    nullable: bool = False,
    unique: bool = False,
    index: bool = True,
) -> None:
    """
    将旧整数 FK 列替换为已回填的 UUID 列：
      1. 删除旧整数列（CASCADE 清理残留的索引/约束）
      2. new_col → final_col
      3. 重建 FK 约束（可选 UNIQUE / INDEX）
    """
    conn = op.get_bind()
    conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {final_col} CASCADE"))
    op.alter_column(table, new_col, new_column_name=final_col, nullable=nullable)
    op.create_foreign_key(
        f"fk_{table}_{final_col}",
        table, ref_table,
        [final_col], ["id"],
        ondelete=ondelete,
    )
    if unique:
        op.create_unique_constraint(f"uq_{table}_{final_col}", table, [final_col])
    if index:
        op.create_index(f"ix_{table}_{final_col}", table, [final_col])


def _finalize_logical_fk_col(
    table: str,
    new_col: str,
    final_col: str,
    *,
    nullable: bool = False,
    unique: bool = False,
    index: bool = True,
) -> None:
    """
    将旧整数逻辑 FK 列替换为已回填的 UUID 列（不创建物理 FK 约束）。
    用于跨应用边界的逻辑外键（如各 app 表中的 user_id → core_users.id）。
    """
    conn = op.get_bind()
    conn.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {final_col} CASCADE"))
    op.alter_column(table, new_col, new_column_name=final_col, nullable=nullable)
    if unique:
        op.create_unique_constraint(f"uq_{table}_{final_col}", table, [final_col])
    if index:
        op.create_index(f"ix_{table}_{final_col}", table, [final_col])


# ══════════════════════════════════════════════════════════════
# UPGRADE
# ══════════════════════════════════════════════════════════════

def upgrade() -> None:
    conn = op.get_bind()

    # ── Step 0：确保 gen_random_uuid() 可用 ──────────────────
    conn.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    # ── Step 1：禁用 FK 触发器（整个 session 范围）────────────
    conn.execute(sa.text("SET session_replication_role = replica"))

    # ── Step 2：为每张表生成 new_id UUID 列 ──────────────────
    all_tables = [
        # core
        "core_users",
        "core_roles",
        # just_right
        "just_right_couples",
        "just_right_todo_items",
        "just_right_memos",
        "just_right_user_manuals",
        "just_right_roulette_options",
        "just_right_wishlist",
        "just_right_anniversaries",
        "just_right_couple_states",
        # nest_talk
        "nest_talk_regions",
        "nest_talk_communities",
        "nest_talk_houses",
        "nest_talk_user_preferences",
        "nest_talk_conversation_sessions",
        "nest_talk_conversation_messages",
        "nest_talk_region_price_logs",
        "nest_talk_daily_reports",
        # trade_copilot
        "trade_stock_info",
        "trade_strategies",
        "trade_positions",
        "trade_watchlist",
        "trade_daily_market_logs",
        "trade_journals",
        "trade_user_settings",
        "trade_transactions",
    ]
    for tbl in all_tables:
        _add_uuid_pk(tbl)

    # ── Step 3：为每个 FK 列生成对应的 UUID 版本并回填 ─────────

    # ---- just_right: 物理 FK (couple_id → just_right_couples) ----
    _add_uuid_fk_col("just_right_todo_items",      "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_memos",            "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_user_manuals",     "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_roulette_options", "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_wishlist",         "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_anniversaries",    "couple_id",  "new_couple_id",  "just_right_couples")
    _add_uuid_fk_col("just_right_couple_states",    "couple_id",  "new_couple_id",  "just_right_couples")

    # ---- just_right: 逻辑 FK (→ core_users, 无物理约束) ----
    _add_uuid_fk_col("just_right_couples",       "user1_id",     "new_user1_id",     "core_users")
    _add_uuid_fk_col("just_right_couples",       "user2_id",     "new_user2_id",     "core_users", nullable=True)
    _add_uuid_fk_col("just_right_todo_items",    "creator_uid",  "new_creator_uid",  "core_users")
    _add_uuid_fk_col("just_right_todo_items",    "completed_by", "new_completed_by", "core_users", nullable=True)
    _add_uuid_fk_col("just_right_memos",         "creator_uid",  "new_creator_uid",  "core_users")
    _add_uuid_fk_col("just_right_user_manuals",  "uid",          "new_uid",          "core_users")
    _add_uuid_fk_col("just_right_wishlist",      "creator_uid",  "new_creator_uid",  "core_users")
    _add_uuid_fk_col("just_right_wishlist",      "claimer_uid",  "new_claimer_uid",  "core_users", nullable=True)
    _add_uuid_fk_col("just_right_couple_states", "user1_id",     "new_user1_id",     "core_users")
    _add_uuid_fk_col("just_right_couple_states", "user2_id",     "new_user2_id",     "core_users", nullable=True)
    _add_uuid_fk_col("just_right_couple_states", "fridge_note_by","new_fridge_note_by","core_users", nullable=True)

    # ---- nest_talk: 物理 FK ----
    _add_uuid_fk_col("nest_talk_communities",           "region_id",   "new_region_id",    "nest_talk_regions",               nullable=True)
    _add_uuid_fk_col("nest_talk_houses",                "region_id",   "new_region_id",    "nest_talk_regions",               nullable=True)
    _add_uuid_fk_col("nest_talk_houses",                "community_id","new_community_id", "nest_talk_communities",           nullable=True)
    _add_uuid_fk_col("nest_talk_conversation_messages", "session_id",  "new_session_id",   "nest_talk_conversation_sessions")
    _add_uuid_fk_col("nest_talk_region_price_logs",     "region_id",   "new_region_id",    "nest_talk_regions")

    # ---- nest_talk: 逻辑 FK (→ core_users) ----
    _add_uuid_fk_col("nest_talk_user_preferences",       "user_id", "new_user_id", "core_users")
    _add_uuid_fk_col("nest_talk_conversation_sessions",  "user_id", "new_user_id", "core_users")

    # ---- trade_copilot: 物理 FK ----
    _add_uuid_fk_col("trade_positions",    "strategy_id", "new_strategy_id", "trade_strategies", nullable=True)
    _add_uuid_fk_col("trade_transactions", "position_id", "new_position_id", "trade_positions")

    # ---- trade_copilot: 逻辑 FK (→ core_users) ----
    _add_uuid_fk_col("trade_strategies",    "user_id", "new_user_id", "core_users")
    _add_uuid_fk_col("trade_positions",     "user_id", "new_user_id", "core_users")
    _add_uuid_fk_col("trade_watchlist",     "user_id", "new_user_id", "core_users")
    _add_uuid_fk_col("trade_journals",      "user_id", "new_user_id", "core_users")
    _add_uuid_fk_col("trade_user_settings", "user_id", "new_user_id", "core_users")

    # ── Step 4：保存 core_user_roles 关联数据到临时表 ──────────
    # 先将 UUID 映射填入关联表本身
    op.add_column("core_user_roles", sa.Column("new_user_id", UUID(as_uuid=True), nullable=True))
    op.add_column("core_user_roles", sa.Column("new_role_id", UUID(as_uuid=True), nullable=True))
    conn.execute(sa.text(
        """
        UPDATE core_user_roles ur
        SET    new_user_id = u.new_id
        FROM   core_users u
        WHERE  ur.user_id = u.id
        """
    ))
    conn.execute(sa.text(
        """
        UPDATE core_user_roles ur
        SET    new_role_id = r.new_id
        FROM   core_roles r
        WHERE  ur.role_id = r.id
        """
    ))
    # 将 UUID 对保存到临时表（临时表随 session 结束自动销毁）
    conn.execute(sa.text(
        """
        CREATE TEMP TABLE tmp_user_roles AS
        SELECT new_user_id, new_role_id
        FROM   core_user_roles
        WHERE  new_user_id IS NOT NULL
          AND  new_role_id IS NOT NULL
        """
    ))

    # ── Step 5：删除旧 core_user_roles（整数 PK 版本）─────────
    op.drop_table("core_user_roles")

    # ── Step 6：切换所有普通表的 PK ──────────────────────────
    for tbl in all_tables:
        _finalize_pk(tbl)

    # ── Step 7：切换所有 FK 列 ────────────────────────────────

    # ---- just_right: 物理 FK ----
    _finalize_fk_col("just_right_todo_items",      "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_memos",            "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_user_manuals",     "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_roulette_options", "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_wishlist",         "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_anniversaries",    "new_couple_id", "couple_id",    "just_right_couples")
    _finalize_fk_col("just_right_couple_states",    "new_couple_id", "couple_id",    "just_right_couples",
                     unique=True)

    # ---- just_right: 逻辑 FK (无物理约束) ----
    _finalize_logical_fk_col("just_right_couples",       "new_user1_id",     "user1_id",     index=True)
    _finalize_logical_fk_col("just_right_couples",       "new_user2_id",     "user2_id",     nullable=True, index=False)
    _finalize_logical_fk_col("just_right_todo_items",    "new_creator_uid",  "creator_uid",  index=False)
    _finalize_logical_fk_col("just_right_todo_items",    "new_completed_by", "completed_by", nullable=True, index=False)
    _finalize_logical_fk_col("just_right_memos",         "new_creator_uid",  "creator_uid",  index=False)
    _finalize_logical_fk_col("just_right_user_manuals",  "new_uid",          "uid",          unique=True, index=True)
    _finalize_logical_fk_col("just_right_wishlist",      "new_creator_uid",  "creator_uid",  index=False)
    _finalize_logical_fk_col("just_right_wishlist",      "new_claimer_uid",  "claimer_uid",  nullable=True, index=False)
    _finalize_logical_fk_col("just_right_couple_states", "new_user1_id",     "user1_id",     index=False)
    _finalize_logical_fk_col("just_right_couple_states", "new_user2_id",     "user2_id",     nullable=True, index=False)
    _finalize_logical_fk_col("just_right_couple_states", "new_fridge_note_by","fridge_note_by",nullable=True, index=False)

    # ---- nest_talk: 物理 FK ----
    _finalize_fk_col("nest_talk_communities",           "new_region_id",    "region_id",    "nest_talk_regions",
                     ondelete="SET NULL", nullable=True)
    _finalize_fk_col("nest_talk_houses",                "new_region_id",    "region_id",    "nest_talk_regions",
                     ondelete="SET NULL", nullable=True)
    _finalize_fk_col("nest_talk_houses",                "new_community_id", "community_id", "nest_talk_communities",
                     ondelete="SET NULL", nullable=True)
    _finalize_fk_col("nest_talk_conversation_messages", "new_session_id",   "session_id",   "nest_talk_conversation_sessions",
                     ondelete="CASCADE")
    _finalize_fk_col("nest_talk_region_price_logs",     "new_region_id",    "region_id",    "nest_talk_regions",
                     ondelete="CASCADE")

    # ---- nest_talk: 逻辑 FK ----
    _finalize_logical_fk_col("nest_talk_user_preferences",      "new_user_id", "user_id", unique=True, index=True)
    _finalize_logical_fk_col("nest_talk_conversation_sessions", "new_user_id", "user_id", index=True)

    # ---- trade_copilot: 物理 FK ----
    _finalize_fk_col("trade_positions",    "new_strategy_id", "strategy_id", "trade_strategies",
                     ondelete="SET NULL", nullable=True)
    _finalize_fk_col("trade_transactions", "new_position_id", "position_id", "trade_positions")

    # ---- trade_copilot: 逻辑 FK ----
    _finalize_logical_fk_col("trade_strategies",    "new_user_id", "user_id", index=True)
    _finalize_logical_fk_col("trade_positions",     "new_user_id", "user_id", index=True)
    _finalize_logical_fk_col("trade_watchlist",     "new_user_id", "user_id", index=True)
    _finalize_logical_fk_col("trade_journals",      "new_user_id", "user_id", index=True)
    _finalize_logical_fk_col("trade_user_settings", "new_user_id", "user_id", unique=True, index=True)

    # ── Step 8：重建 core_user_roles（UUID PK）并恢复数据 ─────
    op.create_table(
        "core_user_roles",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("core_users.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "role_id",
            UUID(as_uuid=True),
            sa.ForeignKey("core_roles.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
    )
    conn.execute(sa.text(
        """
        INSERT INTO core_user_roles (user_id, role_id)
        SELECT new_user_id, new_role_id
        FROM   tmp_user_roles
        """
    ))

    # ── Step 9：恢复外键触发器 ───────────────────────────────
    conn.execute(sa.text("SET session_replication_role = DEFAULT"))


# ══════════════════════════════════════════════════════════════
# DOWNGRADE：不可逆
# ══════════════════════════════════════════════════════════════

def downgrade() -> None:
    raise NotImplementedError(
        "UUID → INTEGER 迁移不可逆。\n"
        "如需回滚，请从执行迁移前的数据库备份中恢复。"
    )
