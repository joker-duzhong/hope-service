"""账伴业务逻辑，不包含 HTTP 请求处理。"""
import uuid
import json
import hashlib
import logging
from collections import defaultdict
from datetime import datetime
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiRecordReference, LedgerMateAiSession, LedgerMateBook, LedgerMateCategory, LedgerMateCategoryTemplate, LedgerMateOperationLog, LedgerMatePaymentMethod, LedgerMateRecord
from apps.ledger_mate.prompts import build_accounting_parser_prompt
from apps.ledger_mate.schemas import AiConfirmRequest, AiMessageCreate, AiParseResult, AiSessionCreate, CategoryCreate, CategoryTemplateCreate, CategoryTemplateUpdate, PaymentMethodCreate, RecordCreate, RecordUpdate
from apps.ledger_mate.dates import SHANGHAI, local_datetime
from core.llm.engine import generate_chat
from core.llm.errors import ChatGenerationError

logger = logging.getLogger(__name__)

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
    async def get_ai_session(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, *, for_update: bool = False):
        stmt = select(LedgerMateAiSession).where(LedgerMateAiSession.id == session_id, LedgerMateAiSession.user_id == user_id, LedgerMateAiSession.is_deleted == False)
        if for_update:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        session = await db.scalar(stmt)
        if not session:
            raise HTTPException(404, "AI 会话不存在")
        return session

    @staticmethod
    async def _message_records(db: AsyncSession, user_id: uuid.UUID, message_id: uuid.UUID):
        record_ids = (await db.scalars(select(LedgerMateAiRecordReference.record_id).where(LedgerMateAiRecordReference.message_id == message_id, LedgerMateAiRecordReference.user_id == user_id, LedgerMateAiRecordReference.is_deleted == False))).all()
        if not record_ids:
            return []
        return (await db.scalars(select(LedgerMateRecord).where(LedgerMateRecord.id.in_(record_ids), LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False).order_by(LedgerMateRecord.created_at, LedgerMateRecord.id).execution_options(populate_existing=True))).all()

    @staticmethod
    async def get_ai_messages(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, limit: int = 100):
        await LedgerMateService.get_ai_session(db, user_id, session_id)
        messages = (await db.scalars(select(LedgerMateAiMessage).where(LedgerMateAiMessage.session_id == session_id, LedgerMateAiMessage.user_id == user_id, LedgerMateAiMessage.is_deleted == False).order_by(LedgerMateAiMessage.created_at.desc(), case((LedgerMateAiMessage.role == "assistant", 1), else_=0).desc(), LedgerMateAiMessage.id.desc()).limit(max(1, min(limit, 100))))).all()
        return list(reversed(messages))

    @staticmethod
    async def _save_message(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, role: str, content: str, payload: Optional[dict] = None):
        message = LedgerMateAiMessage(user_id=user_id, session_id=session_id, role=role, content=content, payload=payload, created_at=datetime.now(SHANGHAI))
        db.add(message)
        await db.flush()
        return message

    @staticmethod
    def _pending_history(history):
        """已入账的一轮是上下文边界，不能被下一轮再次解析入账。"""
        pending = []
        for message in reversed(history):
            payload = message.payload or {}
            if message.role == "assistant" and (payload.get("status") == "ready" or payload.get("auto_saved")):
                break
            pending.append({"role": message.role, "content": message.content, "payload": payload})
        return list(reversed(pending))

    @staticmethod
    def _prepare_parsed(parsed, categories, methods):
        category_map = {item.id: item for item in categories if item.is_enabled}
        method_ids = {item.id for item in methods if item.is_enabled}
        questions = [question.strip() for question in parsed.questions if question.strip()]
        if not parsed.records:
            questions.append("这笔收支的金额和用途是什么？")
        for index, draft in enumerate(parsed.records, 1):
            if draft.amount_cent is None:
                questions.append(f"第 {index} 笔的金额是多少？")
            if draft.record_type is None:
                questions.append(f"第 {index} 笔是收入还是支出？")
            if draft.category_id is None and draft.category_name:
                match = next((item for item in categories if item.is_enabled and item.record_type == draft.record_type and item.name == draft.category_name.strip()), None)
                if match:
                    draft.category_id = match.id
            category = category_map.get(draft.category_id)
            if not category or category.record_type != draft.record_type:
                questions.append(f"第 {index} 笔应归入哪个分类？")
            if draft.occurred_at is None:
                questions.append(f"第 {index} 笔发生在哪一天？")
            else:
                draft.occurred_date = local_datetime(draft.occurred_at).date()
            if draft.payment_method_id and draft.payment_method_id not in method_ids:
                questions.append(f"第 {index} 笔使用了哪种支付方式？")
        if questions or parsed.status == "needs_clarification":
            parsed.status = "needs_clarification"
            parsed.questions = list(dict.fromkeys(questions))[:5] or ["请补充这笔收支的金额和用途。"]
        return parsed

    @staticmethod
    async def chat_with_ai(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, data: AiMessageCreate):
        await LedgerMateService.get_ai_session(db, user_id, session_id)
        await LedgerMateService.ensure_defaults(db, user_id)
        try:
            # 同一会话的发送和重试串行化；消息、账单、引用必须一起提交。
            session = await LedgerMateService.get_ai_session(db, user_id, session_id, for_update=True)
            existing = await db.scalar(select(LedgerMateAiMessage).where(
                LedgerMateAiMessage.user_id == user_id,
                LedgerMateAiMessage.session_id == session_id,
                LedgerMateAiMessage.role == "user",
                LedgerMateAiMessage.payload["client_message_id"].as_string() == data.client_message_id,
                LedgerMateAiMessage.is_deleted == False,
            ))
            if existing:
                if existing.content != data.content:
                    raise HTTPException(409, "此消息编号已用于其他内容，请为新消息生成新编号")
                assistant = await db.scalar(select(LedgerMateAiMessage).where(
                    LedgerMateAiMessage.user_id == user_id,
                    LedgerMateAiMessage.session_id == session_id,
                    LedgerMateAiMessage.role == "assistant",
                    LedgerMateAiMessage.payload["reply_to"].as_string() == str(existing.id),
                    LedgerMateAiMessage.is_deleted == False,
                ))
                if not assistant:
                    raise HTTPException(409, "这条消息尚未完成，请稍后使用原消息编号重试")
                records = await LedgerMateService._message_records(db, user_id, assistant.id)
                await db.commit()
                return session, existing, assistant, records
            history = await LedgerMateService.get_ai_messages(db, user_id, session_id, limit=20)
            categories = await LedgerMateService.categories(db, user_id, initialize=False)
            methods = await LedgerMateService.methods(db, user_id, initialize=False)
            now = datetime.now(SHANGHAI)
            context = {
                "current_date": now.date().isoformat(), "current_time": now.isoformat(), "timezone": "Asia/Shanghai",
                "categories": [{"id": str(item.id), "record_type": item.record_type, "name": item.name} for item in categories],
                "payment_methods": [{"id": str(item.id), "name": item.name, "is_default": item.is_default} for item in methods],
                "history": LedgerMateService._pending_history(history), "user_input": data.content,
            }
            try:
                raw = await generate_chat(
                    [
                        {"role": "system", "content": build_accounting_parser_prompt(context)},
                        {
                            "role": "user",
                            "content": f"用户原文：{data.content}\n\n请按系统规则只返回合法 JSON。",
                        },
                    ],
                    response_format={"type": "json_object"},
                    diagnostic_sensitive_values=(data.content, *(item["content"] for item in context["history"])),
                )
            except ChatGenerationError as exc:
                logger.warning("AI 记账调用失败 session_id=%s diagnostics=%s", session_id, json.dumps(exc.diagnostics, ensure_ascii=True, sort_keys=True))
                raise HTTPException(502, str(exc)) from None
            except Exception as exc:
                logger.error("AI 记账调用异常 session_id=%s exception_type=%s", session_id, type(exc).__name__)
                raise HTTPException(502, "记账助手暂时没有回应，请稍后重试这条消息") from None
            try:
                parsed = LedgerMateService._prepare_parsed(AiParseResult.model_validate(json.loads(raw)), categories, methods)
            except (TypeError, ValueError):
                raise HTTPException(502, "AI 返回的记账结构无效，请重试") from None
            user_message = await LedgerMateService._save_message(db, user_id, session_id, "user", data.content, {"client_message_id": data.client_message_id})
            created_records = []
            request_key = hashlib.sha256(f"{user_id}:{session_id}:{data.client_message_id}".encode("utf-8")).hexdigest()
            if parsed.status == "ready":
                for index, draft in enumerate(parsed.records):
                    values = draft.model_dump(exclude={"category_name", "occurred_at"})
                    record = await LedgerMateService.create_record(db, user_id, RecordCreate(**values, idempotency_key=f"ai:{request_key}:{index}"), source="ai", commit=False, initialize=False)
                    created_records.append(record)
            payload = parsed.model_dump(mode="json")
            payload.update({"reply_to": str(user_message.id), "client_message_id": data.client_message_id, "auto_saved": parsed.status == "ready"})
            content = (parsed.playful_text or f"已记下 {len(created_records)} 笔账。") if created_records else "\n".join(parsed.questions)
            assistant = await LedgerMateService._save_message(db, user_id, session_id, "assistant", content, payload)
            for record in created_records:
                db.add(LedgerMateAiRecordReference(user_id=user_id, session_id=session_id, message_id=assistant.id, record_id=record.id))
            session.updated_at = datetime.now(SHANGHAI)
            await db.commit()
            return session, user_message, assistant, created_records
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def ensure_category_templates(db: AsyncSession, *, commit: bool = True) -> None:
        """确保开发环境及旧数据库也有可管理的默认分类模板。"""
        template_values = [
            {
                "record_type": record_type,
                "name": name,
                "sort_order": index,
                "is_enabled": True,
            }
            for record_type, names in DEFAULT_CATEGORIES.items()
            for index, name in enumerate(names)
        ]
        await db.execute(
            pg_insert(LedgerMateCategoryTemplate)
            .values(template_values)
            .on_conflict_do_nothing(constraint="uq_ledger_mate_category_template")
        )
        if commit:
            await db.commit()
        else:
            await db.flush()

    @staticmethod
    async def ensure_defaults(db: AsyncSession, user_id: uuid.UUID, *, commit: bool = True) -> None:
        """原子初始化用户的默认数据，可安全应对并发首屏请求。"""
        await LedgerMateService.ensure_category_templates(db, commit=False)
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
        await db.flush()
        await LedgerMateService.sync_category_templates(db, user_id)
        await db.execute(
            pg_insert(LedgerMatePaymentMethod)
            .values([
                {"user_id": user_id, "name": name, "is_default": index == 0}
                for index, name in enumerate(DEFAULT_METHODS)
            ])
            .on_conflict_do_nothing(constraint="uq_ledger_mate_payment_method")
        )
        if commit:
            await db.commit()
        else:
            await db.flush()

    @staticmethod
    async def sync_category_templates(db: AsyncSession, user_id: uuid.UUID):
        """将全局模板同步到当前用户，保留已有分类 UUID 及历史账单引用。"""
        templates = (
            await db.scalars(
                select(LedgerMateCategoryTemplate).order_by(
                    LedgerMateCategoryTemplate.record_type,
                    LedgerMateCategoryTemplate.sort_order,
                    LedgerMateCategoryTemplate.created_at,
                )
            )
        ).all()
        if not templates:
            return
        categories = (
            await db.scalars(
                select(LedgerMateCategory).where(
                    LedgerMateCategory.user_id == user_id,
                    LedgerMateCategory.is_deleted == False,
                )
            )
        ).all()
        category_map = {(item.record_type, item.name): item for item in categories}
        active_template_keys = {
            (item.record_type, item.name)
            for item in templates
            if not item.is_deleted
        }
        # 模板改名或删除时停用旧系统分类，保留其 UUID 供历史账单继续引用。
        for category in categories:
            if category.is_system and (category.record_type, category.name) not in active_template_keys:
                category.is_enabled = False
        new_categories = []
        for template in templates:
            key = (template.record_type, template.name)
            category = category_map.get(key)
            if category is None:
                if template.is_deleted:
                    continue
                new_categories.append(
                    {
                        "user_id": user_id,
                        "record_type": template.record_type,
                        "name": template.name,
                        "icon": template.icon,
                        "sort_order": template.sort_order,
                        "is_enabled": template.is_enabled,
                        "is_system": True,
                    }
                )
                continue
            # 模板删除只停用用户分类，避免破坏既有账单的 category_id。
            desired_enabled = template.is_enabled and not template.is_deleted
            category.icon = template.icon
            category.sort_order = template.sort_order
            category.is_enabled = desired_enabled
            category.is_system = True
        if new_categories:
            await db.execute(
                pg_insert(LedgerMateCategory)
                .values(new_categories)
                .on_conflict_do_nothing(constraint="uq_ledger_mate_category")
            )

    @staticmethod
    async def list_category_templates(db: AsyncSession, *, include_deleted: bool = False):
        await LedgerMateService.ensure_category_templates(db)
        stmt = select(LedgerMateCategoryTemplate)
        if not include_deleted:
            stmt = stmt.where(LedgerMateCategoryTemplate.is_deleted == False)
        return (
            await db.scalars(
                stmt.order_by(
                    LedgerMateCategoryTemplate.record_type,
                    LedgerMateCategoryTemplate.sort_order,
                    LedgerMateCategoryTemplate.created_at,
                )
            )
        ).all()

    @staticmethod
    async def create_category_template(db: AsyncSession, data: CategoryTemplateCreate):
        exists = await db.scalar(
            select(LedgerMateCategoryTemplate.id).where(
                LedgerMateCategoryTemplate.record_type == data.record_type,
                LedgerMateCategoryTemplate.name == data.name,
            )
        )
        if exists:
            raise HTTPException(400, "同类型分类名称不可重复")
        template = LedgerMateCategoryTemplate(**data.model_dump())
        db.add(template)
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def update_category_template(db: AsyncSession, template_id: uuid.UUID, data: CategoryTemplateUpdate):
        template = await db.scalar(
            select(LedgerMateCategoryTemplate).where(
                LedgerMateCategoryTemplate.id == template_id,
                LedgerMateCategoryTemplate.is_deleted == False,
            )
        )
        if not template:
            raise HTTPException(404, "分类模板不存在")
        changes = data.model_dump(exclude_unset=True)
        record_type = changes.get("record_type", template.record_type)
        name = changes.get("name", template.name)
        duplicate = await db.scalar(
            select(LedgerMateCategoryTemplate.id).where(
                LedgerMateCategoryTemplate.record_type == record_type,
                LedgerMateCategoryTemplate.name == name,
                LedgerMateCategoryTemplate.id != template_id,
            )
        )
        if duplicate:
            raise HTTPException(400, "同类型分类名称不可重复")
        for key, value in changes.items():
            setattr(template, key, value)
        await db.commit()
        await db.refresh(template)
        return template

    @staticmethod
    async def delete_category_template(db: AsyncSession, template_id: uuid.UUID):
        template = await db.scalar(
            select(LedgerMateCategoryTemplate).where(
                LedgerMateCategoryTemplate.id == template_id,
                LedgerMateCategoryTemplate.is_deleted == False,
            )
        )
        if not template:
            raise HTTPException(404, "分类模板不存在")
        template.is_deleted = True
        template.is_enabled = False
        await db.commit()

    @staticmethod
    async def categories(db: AsyncSession, user_id: uuid.UUID, include_disabled: bool = False, *, initialize: bool = True):
        if initialize:
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
    async def methods(db: AsyncSession, user_id: uuid.UUID, *, initialize: bool = True):
        if initialize:
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
    async def _lock_request(db: AsyncSession, user_id: uuid.UUID, key: str):
        if db.get_bind().dialect.name == "postgresql":
            lock_id = int.from_bytes(hashlib.sha256(f"ledger:{user_id}:{key}".encode("utf-8")).digest()[:8], "big", signed=True)
            await db.execute(select(func.pg_advisory_xact_lock(lock_id)))

    @staticmethod
    async def _payment_method(db: AsyncSession, user_id: uuid.UUID, method_id: Optional[uuid.UUID]):
        if method_id is None:
            return
        method = await db.scalar(select(LedgerMatePaymentMethod).where(LedgerMatePaymentMethod.id == method_id, LedgerMatePaymentMethod.user_id == user_id, LedgerMatePaymentMethod.is_enabled == True, LedgerMatePaymentMethod.is_deleted == False))
        if not method:
            raise HTTPException(400, "支付方式不存在或已停用")

    @staticmethod
    async def create_record(db: AsyncSession, user_id: uuid.UUID, data: RecordCreate, source: str = "manual", *, commit: bool = True, initialize: bool = True):
        if initialize:
            await LedgerMateService.ensure_defaults(db, user_id, commit=commit)
        if data.idempotency_key:
            await LedgerMateService._lock_request(db, user_id, data.idempotency_key)
            existing = await db.scalar(select(LedgerMateRecord).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.idempotency_key == data.idempotency_key))
            if existing:
                if existing.is_deleted:
                    raise HTTPException(409, "这次请求的账单已被删除，请为新账单使用新的请求编号")
                if commit:
                    await db.commit()
                return existing
        await LedgerMateService._category(db, user_id, data.category_id, data.record_type)
        await LedgerMateService._payment_method(db, user_id, data.payment_method_id)
        book = await db.scalar(select(LedgerMateBook).where(LedgerMateBook.user_id == user_id, LedgerMateBook.is_deleted == False))
        if not book:
            raise HTTPException(400, "账本尚未准备好，请重试")
        record = LedgerMateRecord(user_id=user_id, book_id=book.id, source=source, **data.model_dump(exclude={"occurred_date"}))
        db.add(record); await db.flush()
        db.add(LedgerMateOperationLog(user_id=user_id, record_id=record.id, action="create", after_data={"amount_cent": record.amount_cent}))
        if commit:
            await db.commit()
            await db.refresh(record)
        return record

    @staticmethod
    async def confirm_ai_drafts(db: AsyncSession, user_id: uuid.UUID, data: AiConfirmRequest):
        """保留旧确认接口，并确保整组账单不会部分提交。"""
        await LedgerMateService.ensure_defaults(db, user_id)
        try:
            await LedgerMateService._lock_request(db, user_id, f"confirm:{data.idempotency_key}")
            digest = hashlib.sha256(data.idempotency_key.encode("utf-8")).hexdigest()
            records = []
            for index, draft in enumerate(data.drafts):
                legacy = await db.scalar(select(LedgerMateRecord).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.idempotency_key == f"{data.idempotency_key}:{index}"))
                if legacy:
                    if legacy.is_deleted:
                        raise HTTPException(409, "此前确认的账单已删除，请为新账单使用新的请求编号")
                    records.append(legacy)
                    continue
                record = await LedgerMateService.create_record(db, user_id, RecordCreate(**draft.model_dump(), idempotency_key=f"confirm:{digest}:{index}"), source="ai", commit=False, initialize=False)
                records.append(record)
            await db.commit()
            return records
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def get_record(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID):
        record = await db.scalar(select(LedgerMateRecord).where(LedgerMateRecord.id == record_id, LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False))
        if not record: raise HTTPException(404, "账单不存在")
        return record

    @staticmethod
    async def update_record(db: AsyncSession, user_id: uuid.UUID, record_id: uuid.UUID, data: RecordUpdate):
        record = await LedgerMateService.get_record(db, user_id, record_id)
        before = {"amount_cent": record.amount_cent, "record_type": record.record_type, "category_id": str(record.category_id)}
        values = data.model_dump(exclude_unset=True, exclude={"occurred_date"})
        target_type = values.get("record_type", record.record_type)
        if "category_id" in values: await LedgerMateService._category(db, user_id, values["category_id"], target_type)
        elif "record_type" in values: await LedgerMateService._category(db, user_id, record.category_id, target_type)
        if "payment_method_id" in values:
            await LedgerMateService._payment_method(db, user_id, values["payment_method_id"])
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
        records = (await db.scalars(stmt.order_by(LedgerMateRecord.occurred_at.desc(), LedgerMateRecord.created_at.desc(), LedgerMateRecord.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
        return records, total or 0

    @staticmethod
    async def statistics(db: AsyncSession, user_id: uuid.UUID, start_at: datetime, end_at: datetime):
        rows = (await db.execute(select(LedgerMateRecord.record_type, LedgerMateRecord.amount_cent, LedgerMateRecord.category_id, LedgerMateRecord.occurred_at).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False, LedgerMateRecord.occurred_at >= start_at, LedgerMateRecord.occurred_at < end_at))).all()
        income = sum(row.amount_cent for row in rows if row.record_type == "income")
        expense = sum(row.amount_cent for row in rows if row.record_type == "expense")
        names = dict((await db.execute(select(LedgerMateCategory.id, LedgerMateCategory.name).where(LedgerMateCategory.user_id == user_id))).all())
        by_category = {"income": defaultdict(lambda: {"amount_cent": 0, "count": 0}), "expense": defaultdict(lambda: {"amount_cent": 0, "count": 0})}
        by_day = defaultdict(lambda: {"income_cent": 0, "expense_cent": 0, "count": 0})
        for row in rows:
            day = by_day[local_datetime(row.occurred_at).date().isoformat()]
            day[f"{row.record_type}_cent"] += row.amount_cent
            day["count"] += 1
            category = by_category[row.record_type][row.category_id]
            category["amount_cent"] += row.amount_cent
            category["count"] += 1
        def category_totals(record_type):
            return [{"category_id": str(key), "name": names.get(key, "未命名分类"), **value} for key, value in sorted(by_category[record_type].items(), key=lambda item: (-item[1]["amount_cent"], str(item[0])))]
        return {
            "income_cent": income, "expense_cent": expense, "balance_cent": income - expense,
            "record_count": len(rows), "income_count": sum(row.record_type == "income" for row in rows), "expense_count": sum(row.record_type == "expense" for row in rows),
            "category_expenses": category_totals("expense"), "category_incomes": category_totals("income"),
            "daily": [{"date": key, **value, "balance_cent": value["income_cent"] - value["expense_cent"]} for key, value in sorted(by_day.items())],
        }
