"""账伴业务逻辑，不包含 HTTP 请求处理。"""
import uuid
import json
from collections import defaultdict
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiRecordReference, LedgerMateAiSession, LedgerMateBook, LedgerMateCategory, LedgerMateOperationLog, LedgerMatePaymentMethod, LedgerMateRecord
from apps.ledger_mate.prompts import build_accounting_parser_prompt
from apps.ledger_mate.schemas import AiMessageCreate, AiParseResult, AiSessionCreate, CategoryCreate, PaymentMethodCreate, RecordCreate, RecordUpdate
from core.llm.engine import generate_chat

DEFAULT_CATEGORIES = {"expense": ["餐饮", "交通", "购物", "居住", "医疗", "娱乐", "其他"], "income": ["工资", "奖金", "兼职", "理财", "其他"]}
DEFAULT_METHODS = ["微信支付", "支付宝", "银行卡", "现金"]


class LedgerMateService:
    @staticmethod
    async def create_ai_session(db: AsyncSession, user_id: uuid.UUID, data: AiSessionCreate):
        session = LedgerMateAiSession(user_id=user_id, title=data.title or "AI 记账")
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def list_ai_sessions(db: AsyncSession, user_id: uuid.UUID):
        return (await db.scalars(select(LedgerMateAiSession).where(LedgerMateAiSession.user_id == user_id, LedgerMateAiSession.is_deleted == False).order_by(LedgerMateAiSession.updated_at.desc()))).all()

    @staticmethod
    async def get_ai_session(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID):
        session = await db.scalar(select(LedgerMateAiSession).where(LedgerMateAiSession.id == session_id, LedgerMateAiSession.user_id == user_id, LedgerMateAiSession.is_deleted == False))
        if not session:
            raise HTTPException(404, "AI 会话不存在")
        return session

    @staticmethod
    async def _message_records(db: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID):
        record_ids = (await db.scalars(select(LedgerMateAiRecordReference.record_id).where(LedgerMateAiRecordReference.message_id == message_id, LedgerMateAiRecordReference.user_id == user_id, LedgerMateAiRecordReference.is_deleted == False))).all()
        if not record_ids:
            return []
        return (await db.scalars(select(LedgerMateRecord).where(LedgerMateRecord.id.in_(record_ids), LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False))).all()

    @staticmethod
    async def get_ai_messages(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, limit: int = 100):
        await LedgerMateService.get_ai_session(db, user_id, session_id)
        messages = (await db.scalars(select(LedgerMateAiMessage).where(LedgerMateAiMessage.session_id == session_id, LedgerMateAiMessage.user_id == user_id, LedgerMateAiMessage.is_deleted == False).order_by(LedgerMateAiMessage.created_at.desc()).limit(min(limit, 100)))).all()
        return list(reversed(messages))

    @staticmethod
    async def _save_message(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, role: str, content: str, payload: Optional[dict] = None):
        message = LedgerMateAiMessage(user_id=user_id, session_id=session_id, role=role, content=content, payload=payload)
        db.add(message)
        await db.flush()
        return message

    @staticmethod
    async def chat_with_ai(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, data: AiMessageCreate):
        session = await LedgerMateService.get_ai_session(db, user_id, session_id)
        await LedgerMateService.ensure_defaults(db, user_id)
        history = await LedgerMateService.get_ai_messages(db, user_id, session_id, limit=5)
        categories = await LedgerMateService.categories(db, user_id)
        methods = await LedgerMateService.methods(db, user_id)
        now = datetime.now(ZoneInfo("Asia/Shanghai"))
        context = {
            "current_time": now.isoformat(), "timezone": "Asia/Shanghai",
            "categories": [{"id": str(item.id), "record_type": item.record_type, "name": item.name} for item in categories],
            "payment_methods": [{"id": str(item.id), "name": item.name, "is_default": item.is_default} for item in methods],
            "history": [{"role": item.role, "content": item.content, "payload": item.payload} for item in history],
            "user_input": data.content,
        }
        raw = await generate_chat([{"role": "system", "content": build_accounting_parser_prompt(context)}], response_format={"type": "json_object"})
        try:
            parsed = AiParseResult.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            raise HTTPException(502, "AI 返回的记账结构无效，请重试") from exc
        user_message = await LedgerMateService._save_message(db, user_id, session_id, "user", data.content)
        created_records = []
        category_map = {item.id: item for item in categories}
        method_ids = {item.id for item in methods}
        if parsed.status == "ready":
            if not parsed.records:
                raise HTTPException(502, "AI 未返回账单数据")
            for index, draft in enumerate(parsed.records):
                if not all((draft.record_type, draft.amount_cent, draft.category_id, draft.occurred_at)):
                    raise HTTPException(502, "AI 返回了不完整账单")
                category = category_map.get(draft.category_id)
                if not category or category.record_type != draft.record_type:
                    raise HTTPException(502, "AI 返回了无效分类")
                if draft.payment_method_id and draft.payment_method_id not in method_ids:
                    raise HTTPException(502, "AI 返回了无效支付方式")
                if draft.occurred_at.tzinfo is None:
                    raise HTTPException(502, "AI 返回了无时区的交易时间")
                created_records.append(await LedgerMateService.create_record(db, user_id, RecordCreate(**draft.model_dump(), idempotency_key=f"ai-chat:{session_id}:{uuid.uuid4()}:{index}"), source="ai"))
        payload = parsed.model_dump(mode="json")
        assistant_message = await LedgerMateService._save_message(db, user_id, session_id, "assistant", parsed.playful_text or "这笔我记下啦。", payload)
        for record in created_records:
            db.add(LedgerMateAiRecordReference(user_id=user_id, session_id=session_id, message_id=assistant_message.id, record_id=record.id))
        session.updated_at = now
        await db.commit()
        await db.refresh(user_message)
        await db.refresh(assistant_message)
        return session, user_message, assistant_message, created_records

    @staticmethod
    async def ensure_defaults(db: AsyncSession, user_id: uuid.UUID) -> None:
        """原子初始化用户的默认数据，可安全应对并发首屏请求。"""
        await db.execute(
            pg_insert(LedgerMateBook)
            .values(user_id=user_id)
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        category_values = [
            {
                "user_id": user_id,
                "record_type": record_type,
                "name": name,
                "is_system": True,
                "sort_order": index,
            }
            for record_type, names in DEFAULT_CATEGORIES.items()
            for index, name in enumerate(names)
        ]
        await db.execute(
            pg_insert(LedgerMateCategory)
            .values(category_values)
            .on_conflict_do_nothing(constraint="uq_ledger_mate_category")
        )
        await db.execute(
            pg_insert(LedgerMatePaymentMethod)
            .values([
                {"user_id": user_id, "name": name, "is_default": index == 0}
                for index, name in enumerate(DEFAULT_METHODS)
            ])
            .on_conflict_do_nothing(constraint="uq_ledger_mate_payment_method")
        )
        await db.commit()

    @staticmethod
    async def categories(db: AsyncSession, user_id: uuid.UUID, include_disabled: bool = False):
        await LedgerMateService.ensure_defaults(db, user_id)
        stmt = select(LedgerMateCategory).where(LedgerMateCategory.user_id == user_id, LedgerMateCategory.is_deleted == False)
        if not include_disabled:
            stmt = stmt.where(LedgerMateCategory.is_enabled == True)
        return (await db.scalars(stmt.order_by(LedgerMateCategory.record_type, LedgerMateCategory.sort_order, LedgerMateCategory.created_at))).all()

    @staticmethod
    async def add_category(db: AsyncSession, user_id: uuid.UUID, data: CategoryCreate):
        await LedgerMateService.ensure_defaults(db, user_id)
        exists = await db.scalar(select(LedgerMateCategory.id).where(LedgerMateCategory.user_id == user_id, LedgerMateCategory.record_type == data.record_type, LedgerMateCategory.name == data.name, LedgerMateCategory.is_deleted == False))
        if exists: raise HTTPException(400, "同类型分类名称不可重复")
        category = LedgerMateCategory(user_id=user_id, **data.model_dump())
        db.add(category); await db.commit(); await db.refresh(category)
        return category

    @staticmethod
    async def methods(db: AsyncSession, user_id: uuid.UUID):
        await LedgerMateService.ensure_defaults(db, user_id)
        return (await db.scalars(select(LedgerMatePaymentMethod).where(LedgerMatePaymentMethod.user_id == user_id, LedgerMatePaymentMethod.is_deleted == False, LedgerMatePaymentMethod.is_enabled == True).order_by(LedgerMatePaymentMethod.created_at))).all()

    @staticmethod
    async def add_method(db: AsyncSession, user_id: uuid.UUID, data: PaymentMethodCreate):
        await LedgerMateService.ensure_defaults(db, user_id)
        if data.is_default:
            for method in await LedgerMateService.methods(db, user_id): method.is_default = False
        method = LedgerMatePaymentMethod(user_id=user_id, **data.model_dump())
        db.add(method); await db.commit(); await db.refresh(method)
        return method

    @staticmethod
    async def _category(db: AsyncSession, user_id: uuid.UUID, category_id: uuid.UUID, record_type: str):
        category = await db.scalar(select(LedgerMateCategory).where(LedgerMateCategory.id == category_id, LedgerMateCategory.user_id == user_id, LedgerMateCategory.is_deleted == False, LedgerMateCategory.is_enabled == True))
        if not category or category.record_type != record_type: raise HTTPException(400, "分类不存在、已停用或与收支类型不匹配")
        return category

    @staticmethod
    async def create_record(db: AsyncSession, user_id: uuid.UUID, data: RecordCreate, source: str = "manual"):
        await LedgerMateService.ensure_defaults(db, user_id)
        if data.idempotency_key:
            existing = await db.scalar(select(LedgerMateRecord).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.idempotency_key == data.idempotency_key, LedgerMateRecord.is_deleted == False))
            if existing: return existing
        await LedgerMateService._category(db, user_id, data.category_id, data.record_type)
        book = await db.scalar(select(LedgerMateBook).where(LedgerMateBook.user_id == user_id, LedgerMateBook.is_deleted == False))
        record = LedgerMateRecord(user_id=user_id, book_id=book.id, source=source, **data.model_dump())
        db.add(record); await db.flush()
        db.add(LedgerMateOperationLog(user_id=user_id, record_id=record.id, action="create", after_data={"amount_cent": record.amount_cent}))
        await db.commit(); await db.refresh(record)
        return record

    @staticmethod
    async def get_record(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID):
        record = await db.scalar(select(LedgerMateRecord).where(LedgerMateRecord.id == record_id, LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False))
        if not record: raise HTTPException(404, "账单不存在")
        return record

    @staticmethod
    async def update_record(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID, data: RecordUpdate):
        record = await LedgerMateService.get_record(db, user_id, record_id)
        before = {"amount_cent": record.amount_cent, "record_type": record.record_type, "category_id": str(record.category_id)}
        values = data.model_dump(exclude_unset=True)
        target_type = values.get("record_type", record.record_type)
        if "category_id" in values: await LedgerMateService._category(db, user_id, values["category_id"], target_type)
        elif "record_type" in values: await LedgerMateService._category(db, user_id, record.category_id, target_type)
        for field, value in values.items(): setattr(record, field, value)
        db.add(LedgerMateOperationLog(user_id=user_id, record_id=record.id, action="update", before_data=before, after_data={"amount_cent": record.amount_cent, "record_type": record.record_type, "category_id": str(record.category_id)}))
        await db.commit(); await db.refresh(record); return record

    @staticmethod
    async def delete_record(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID):
        record = await LedgerMateService.get_record(db, user_id, record_id)
        record.is_deleted = True
        db.add(LedgerMateOperationLog(user_id=user_id, record_id=record.id, action="delete", before_data={"amount_cent": record.amount_cent}))
        await db.commit()

    @staticmethod
    async def list_records(db: AsyncSession, user_id: uuid.UUID, page: int, page_size: int, start_at: Optional[datetime], end_at: Optional[datetime], record_type: Optional[str], category_id: Optional[uuid.UUID], keyword: Optional[str]):
        stmt = select(LedgerMateRecord).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False)
        if start_at: stmt = stmt.where(LedgerMateRecord.occurred_at >= start_at)
        if end_at: stmt = stmt.where(LedgerMateRecord.occurred_at < end_at)
        if record_type: stmt = stmt.where(LedgerMateRecord.record_type == record_type)
        if category_id: stmt = stmt.where(LedgerMateRecord.category_id == category_id)
        if keyword: stmt = stmt.where(LedgerMateRecord.note.ilike(f"%{keyword}%"))
        total = await db.scalar(select(func.count()).select_from(stmt.subquery()))
        records = (await db.scalars(stmt.order_by(LedgerMateRecord.occurred_at.desc(), LedgerMateRecord.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return records, total or 0

    @staticmethod
    async def statistics(db: AsyncSession, user_id: uuid.UUID, start_at: datetime, end_at: datetime):
        rows = (await db.execute(select(LedgerMateRecord.record_type, LedgerMateRecord.amount_cent, LedgerMateRecord.category_id, LedgerMateRecord.occurred_at).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False, LedgerMateRecord.occurred_at >= start_at, LedgerMateRecord.occurred_at < end_at))).all()
        income = sum(row.amount_cent for row in rows if row.record_type == "income"); expense = sum(row.amount_cent for row in rows if row.record_type == "expense")
        by_category, by_day = defaultdict(int), defaultdict(lambda: {"income_cent": 0, "expense_cent": 0})
        for row in rows:
            by_day[row.occurred_at.date().isoformat()][f"{row.record_type}_cent"] += row.amount_cent
            if row.record_type == "expense": by_category[str(row.category_id)] += row.amount_cent
        return {"income_cent": income, "expense_cent": expense, "balance_cent": income - expense, "category_expenses": [{"category_id": key, "amount_cent": value} for key, value in sorted(by_category.items(), key=lambda item: item[1], reverse=True)], "daily": [{"date": key, **value} for key, value in sorted(by_day.items())]}
