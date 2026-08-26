"""
数据库引擎与 Session 依赖
"""
import uuid
from datetime import datetime
from typing import AsyncGenerator

from sqlalchemy import Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.config import settings

# 异步数据库引擎
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
)

# 异步会话工厂
async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """ORM 模型基类"""
    pass


class CoreModel(Base):
    """带通用字段的核心业务模型基类"""
    __abstract__ = True

    id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        index=True,
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI Depends: 获取数据库会话"""
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """初始化数据库表（仅开发环境使用，生产环境用 Alembic）"""
    import apps.ai_gateway.models  # noqa: F401
    import apps.aurakey.models  # noqa: F401
    import apps.just_right.models  # noqa: F401
    import apps.ledger_mate.models  # noqa: F401
    import apps.nest_talk.models  # noqa: F401
    import apps.project_sisyphus.models  # noqa: F401
    import apps.shadow_board.models  # noqa: F401
    import apps.time_library.models  # noqa: F401
    import apps.trade_copilot.models  # noqa: F401
    import apps.typo_craft.models  # noqa: F401
    import apps.zaiwen_gaokao.models  # noqa: F401
    import core.associations  # noqa: F401
    import core.roles.models  # noqa: F401
    import core.storage.models  # noqa: F401
    import core.users.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
