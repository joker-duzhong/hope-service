"""
Alembic 迁移环境配置

支持两种运行模式：
  - 离线模式 (alembic upgrade head --sql)：生成纯 SQL 脚本
  - 在线模式 (alembic upgrade head)：直接连接数据库执行迁移

数据库 URL 从项目的 core.config.settings 中动态读取，与应用保持一致。
"""
import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# 导入项目配置和所有 ORM 模型，确保 Base.metadata 包含全部表
# ---------------------------------------------------------------------------
from core.config import settings
from core.database import Base  # noqa: F401 — 触发 Base 注册

# 导入所有模型（必须全部 import，否则 autogenerate 检测不到）
import core.users.models      # noqa: F401
import core.roles.models      # noqa: F401
import core.associations       # noqa: F401
import apps.just_right.models  # noqa: F401
import apps.nest_talk.models   # noqa: F401
import apps.trade_copilot.models  # noqa: F401
import apps.typo_craft.models     # noqa: F401
import core.storage.models         # noqa: F401

# ---------------------------------------------------------------------------
# Alembic Config 对象（读取 alembic.ini）
# ---------------------------------------------------------------------------
config = context.config

# 从 alembic.ini 读取日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata —— autogenerate 通过它对比现有数据库结构
target_metadata = Base.metadata

# 动态注入数据库 URL（覆盖 alembic.ini 中的空值）
# 使用同步 URL（psycopg 驱动）供 Alembic 使用
config.set_main_option("sqlalchemy.url", settings.SYNC_DATABASE_URL)


# ---------------------------------------------------------------------------
# 离线迁移（生成 SQL 脚本，不需要真实数据库连接）
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """以离线模式运行迁移，输出 SQL 而非直接执行。"""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # 比较类型变化（如 Integer → UUID）
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# 在线迁移（异步引擎，直接连接 PostgreSQL）
# ---------------------------------------------------------------------------
def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """使用异步引擎（asyncpg）连接数据库并执行迁移。"""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        # 覆盖为异步 URL
        url=settings.DATABASE_URL,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """以在线模式运行迁移。"""
    asyncio.run(run_async_migrations())


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
