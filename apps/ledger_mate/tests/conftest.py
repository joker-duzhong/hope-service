"""隔离测试：内存 SQLite 与假 LLM，不加载业务配置、.env 或其他 app。"""
import importlib
import sys
import types
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest_asyncio
from sqlalchemy import Boolean, DateTime, Uuid, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class TestBase(DeclarativeBase):
    pass


class TestCoreModel(TestBase):
    __abstract__ = True
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


app_path = Path(__file__).resolve().parents[1]
apps = types.ModuleType("apps")
apps.__path__ = [str(app_path.parent)]
ledger = types.ModuleType("apps.ledger_mate")
ledger.__path__ = [str(app_path)]
core_database = types.ModuleType("core.database")
core_database.CoreModel = TestCoreModel
core_database.Base = TestBase
llm = types.ModuleType("core.llm.engine")


async def forbidden_llm(*args, **kwargs):
    raise AssertionError("tests must provide an explicit fake LLM")


llm.generate_chat = forbidden_llm
sys.modules.update({"apps": apps, "apps.ledger_mate": ledger, "core.database": core_database, "core.llm.engine": llm})
models = importlib.import_module("apps.ledger_mate.models")
services = importlib.import_module("apps.ledger_mate.services")


@pytest_asyncio.fixture
async def ledger_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(TestBase.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        user_id = uuid.uuid4()
        book = models.LedgerMateBook(user_id=user_id)
        expense = models.LedgerMateCategory(user_id=user_id, name="餐饮", record_type="expense", is_system=True)
        income = models.LedgerMateCategory(user_id=user_id, name="工资", record_type="income", is_system=True)
        payment = models.LedgerMatePaymentMethod(user_id=user_id, name="现金", is_default=True)
        session = models.LedgerMateAiSession(user_id=user_id, title="AI 记账")
        db.add_all([book, expense, income, payment, session])
        await db.commit()

        async def existing_defaults(db, user_id, *, commit=True):
            if commit:
                await db.commit()
            else:
                await db.flush()

        monkeypatch.setattr(services.LedgerMateService, "ensure_defaults", existing_defaults)
        yield SimpleNamespace(db=db, user=user_id, book=book, expense=expense, income=income, payment=payment, session=session, session_id=session.id)
    await engine.dispose()
