"""账伴业务逻辑，不包含 HTTP 请求处理。"""
import uuid
import json
import hashlib
import logging
import csv
import io
import re
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Optional

from fastapi import HTTPException
from sqlalchemy import case, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiRecordReference, LedgerMateAiSession, LedgerMateBook, LedgerMateCategory, LedgerMateCategoryTemplate, LedgerMateImportBatch, LedgerMateOperationLog, LedgerMatePaymentMethod, LedgerMateRecord
from apps.ledger_mate.prompts import build_accounting_parser_prompt
from apps.ledger_mate.schemas import AiConfirmRequest, AiMessageCreate, AiParseResult, AiSessionCreate, CategoryCreate, CategoryTemplateCreate, CategoryTemplateUpdate, ImportConfirmRequest, ImportPreviewRequest, PaymentMethodCreate, RecordCreate, RecordUpdate
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
            if payload.get("request_status") == "failed":
                continue
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
    async def _parse_ai_input(content, history, categories, methods, now, session_id):
        context = {
            "current_date": now.date().isoformat(), "current_time": now.isoformat(), "timezone": "Asia/Shanghai",
            "categories": [{"id": str(item.id), "record_type": item.record_type, "name": item.name} for item in categories],
            "payment_methods": [{"id": str(item.id), "name": item.name, "is_default": item.is_default} for item in methods],
            "history": LedgerMateService._pending_history(history), "user_input": content,
        }
        try:
            raw = await generate_chat(
                [
                    {"role": "system", "content": build_accounting_parser_prompt(context)},
                    {"role": "user", "content": f"用户原文：{content}\n\n请按系统规则只返回合法 JSON。"},
                ],
                response_format={"type": "json_object"},
                diagnostic_sensitive_values=(content, *(item["content"] for item in context["history"])),
            )
        except ChatGenerationError as exc:
            logger.warning("AI 记账调用失败 session_id=%s diagnostics=%s", session_id, json.dumps(exc.diagnostics, ensure_ascii=True, sort_keys=True))
            raise HTTPException(502, str(exc)) from None
        except Exception as exc:
            logger.error("AI 记账调用异常 session_id=%s exception_type=%s", session_id, type(exc).__name__)
            raise HTTPException(502, "记账助手暂时没有回应，请稍后重试这条消息") from None
        try:
            return LedgerMateService._prepare_parsed(AiParseResult.model_validate(json.loads(raw)), categories, methods)
        except (TypeError, ValueError):
            raise HTTPException(502, "AI 返回的记账结构无效，请重试") from None

    @staticmethod
    async def _save_ai_result(db, user_id, session, user_message, parsed, client_message_id):
        created_records = []
        request_key = hashlib.sha256(f"{user_id}:{session.id}:{client_message_id}".encode("utf-8")).hexdigest()
        if parsed.status == "ready":
            for index, draft in enumerate(parsed.records):
                values = draft.model_dump(exclude={"category_name", "occurred_at"})
                record = await LedgerMateService.create_record(db, user_id, RecordCreate(**values, idempotency_key=f"ai:{request_key}:{index}"), source="ai", commit=False, initialize=False)
                created_records.append(record)
        payload = parsed.model_dump(mode="json")
        payload.update({"reply_to": str(user_message.id), "client_message_id": client_message_id, "auto_saved": parsed.status == "ready"})
        content = (parsed.playful_text or f"已记下 {len(created_records)} 笔账。") if created_records else "\n".join(parsed.questions)
        assistant = await LedgerMateService._save_message(db, user_id, session.id, "assistant", content, payload)
        for record in created_records:
            db.add(LedgerMateAiRecordReference(user_id=user_id, session_id=session.id, message_id=assistant.id, record_id=record.id))
        session.updated_at = datetime.now(SHANGHAI)
        return assistant, created_records

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
            pending = await db.scalar(select(LedgerMateAiMessage.id).where(
                LedgerMateAiMessage.user_id == user_id,
                LedgerMateAiMessage.session_id == session_id,
                LedgerMateAiMessage.role == "user",
                LedgerMateAiMessage.payload["request_status"].as_string().in_(["queued", "processing"]),
                LedgerMateAiMessage.is_deleted == False,
            ).limit(1))
            if pending:
                raise HTTPException(409, "此会话还有消息处理中，请等待结果后再发送")
            history = await LedgerMateService.get_ai_messages(db, user_id, session_id, limit=20)
            categories = await LedgerMateService.categories(db, user_id, initialize=False)
            methods = await LedgerMateService.methods(db, user_id, initialize=False)
            parsed = await LedgerMateService._parse_ai_input(data.content, history, categories, methods, datetime.now(SHANGHAI), session_id)
            user_message = await LedgerMateService._save_message(db, user_id, session_id, "user", data.content, {"client_message_id": data.client_message_id})
            assistant, created_records = await LedgerMateService._save_ai_result(db, user_id, session, user_message, parsed, data.client_message_id)
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
    async def create_record(db: AsyncSession, user_id: uuid.UUID, data: RecordCreate, source: str = "manual", *, commit: bool = True, initialize: bool = True, import_batch_id: Optional[uuid.UUID] = None):
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
        record = LedgerMateRecord(user_id=user_id, book_id=book.id, source=source, import_batch_id=import_batch_id, **data.model_dump(exclude={"occurred_date"}))
        db.add(record); await db.flush()
        db.add(LedgerMateOperationLog(user_id=user_id, record_id=record.id, action="create", after_data={"amount_cent": record.amount_cent}))
        if commit:
            await db.commit()
            await db.refresh(record)
        return record

    @staticmethod
    def _import_value(row: dict, *names):
        normalized = {str(key).strip().lower(): value for key, value in row.items() if key is not None}
        for name in names:
            value = normalized.get(name)
            if value is not None and str(value).strip() != "":
                return value
        return None

    @staticmethod
    def _import_amount(value, *, amount_cent=False) -> int:
        if value is None or isinstance(value, bool):
            raise ValueError("金额不能为空")
        text = str(value).strip().replace(",", "")
        if not text:
            raise ValueError("金额不能为空")
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            raise ValueError("金额必须是数字") from None
        if not amount.is_finite() or amount <= 0:
            raise ValueError("金额必须大于 0")
        if amount_cent:
            if amount != amount.to_integral_value():
                raise ValueError("amount_cent 必须是整数")
            cents = int(amount)
        else:
            cents_decimal = (amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            if amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) != amount:
                raise ValueError("金额最多保留两位小数")
            cents = int(cents_decimal)
        if cents > 100_000_000:
            raise ValueError("金额超出上限")
        return cents

    @staticmethod
    def _import_date(value) -> date:
        if value is None or not str(value).strip():
            raise ValueError("日期不能为空")
        try:
            parsed = date.fromisoformat(str(value).strip())
        except ValueError:
            raise ValueError("日期必须是 YYYY-MM-DD") from None
        return parsed

    @staticmethod
    def _import_uuid(value, label: str):
        try:
            return uuid.UUID(str(value).strip())
        except (ValueError, AttributeError, TypeError):
            raise ValueError(f"{label}编号无效") from None

    @staticmethod
    def _import_raw_rows(content: str, file_name: str):
        text = content.lstrip("\ufeff")
        looks_json = file_name.lower().endswith(".json") or text.lstrip().startswith(("[", "{"))
        if looks_json:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                raise HTTPException(400, "JSON 文件格式无效") from None
            if isinstance(payload, dict):
                payload = payload.get("records", payload.get("data"))
            if not isinstance(payload, list):
                raise HTTPException(400, "JSON 文件必须是账单数组或包含 records 数组")
            if not payload:
                raise HTTPException(400, "导入文件没有账单记录")
            return payload, 1
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if not reader.fieldnames:
            raise HTTPException(400, "CSV 文件缺少表头")
        rows = list(reader)
        if not rows:
            raise HTTPException(400, "导入文件没有账单记录")
        return rows, 2

    @staticmethod
    async def _normalize_import_rows(db: AsyncSession, user_id: uuid.UUID, content: str, file_name: str):
        categories = await LedgerMateService.categories(db, user_id, initialize=False)
        methods = await LedgerMateService.methods(db, user_id, initialize=False)
        raw_rows, first_row_number = LedgerMateService._import_raw_rows(content, file_name)
        category_by_id = {item.id: item for item in categories}
        category_by_name = {(item.record_type, item.name.strip()): item for item in categories}
        method_by_id = {item.id: item for item in methods}
        method_by_name = {item.name.strip(): item for item in methods}
        existing_rows = (await db.execute(select(LedgerMateRecord, LedgerMateCategory.name, LedgerMatePaymentMethod.name).outerjoin(LedgerMateCategory, LedgerMateCategory.id == LedgerMateRecord.category_id).outerjoin(LedgerMatePaymentMethod, LedgerMatePaymentMethod.id == LedgerMateRecord.payment_method_id).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False))).all()
        existing_fingerprints = set()
        for existing, category_name, method_name in existing_rows:
            category_keys = {str(existing.category_id)}
            if category_name:
                category_keys.add(f"name:{category_name.strip()}")
            method_keys = {None, str(existing.payment_method_id)} if existing.payment_method_id else {None}
            if method_name:
                method_keys.add(f"name:{method_name.strip()}")
            note_keys = {existing.note or ""}
            stripped_note = re.sub(r" ?\[(?:账本|二级分类):.*?\]", "", existing.note or "").strip()
            note_keys.add(stripped_note)
            for category_key in category_keys:
                for method_key in method_keys:
                    for note_key in note_keys:
                        existing_fingerprints.add((existing.record_type, existing.amount_cent, local_datetime(existing.occurred_at).date().isoformat(), category_key, method_key, note_key or None))
        rows = []
        fingerprints = set()
        for offset, raw in enumerate(raw_rows):
            row_number = first_row_number + offset
            row = {"row_number": row_number, "record_type": None, "amount_cent": None, "occurred_date": None, "category_name": None, "category_id": None, "ledger_name": None, "secondary_category": None, "payment_method_name": None, "payment_method_id": None, "note": None, "errors": [], "warnings": [], "duplicate": False}
            errors = row["errors"]
            if not isinstance(raw, dict):
                errors.append("每一行必须是对象")
                rows.append(row)
                continue
            raw_type = LedgerMateService._import_value(raw, "record_type", "type", "收支类型", "类型")
            type_aliases = {"收入": "income", "支出": "expense", "income": "income", "expense": "expense"}
            row["record_type"] = type_aliases.get(str(raw_type).strip().lower()) if raw_type is not None else None
            if row["record_type"] is None:
                errors.append("收支类型必须是 income 或 expense")
            raw_amount = LedgerMateService._import_value(raw, "amount", "金额")
            raw_amount_cent = LedgerMateService._import_value(raw, "amount_cent")
            try:
                row["amount_cent"] = LedgerMateService._import_amount(raw_amount_cent if raw_amount_cent is not None else raw_amount, amount_cent=raw_amount_cent is not None)
            except ValueError as exc:
                errors.append(str(exc))
            raw_date = LedgerMateService._import_value(raw, "occurred_date", "date", "日期", "时间", "发生日期")
            try:
                row["occurred_date"] = LedgerMateService._import_date(raw_date).isoformat()
            except ValueError as exc:
                errors.append(str(exc))
            row["ledger_name"] = str(LedgerMateService._import_value(raw, "book", "ledger", "账本") or "").strip() or None
            row["secondary_category"] = str(LedgerMateService._import_value(raw, "subcategory", "secondary_category", "二级分类") or "").strip() or None
            raw_category = LedgerMateService._import_value(raw, "category_id")
            raw_category_name = LedgerMateService._import_value(raw, "category", "category_name", "分类")
            if raw_category is not None:
                try:
                    category_id = LedgerMateService._import_uuid(raw_category, "分类")
                    category = category_by_id.get(category_id)
                    if not category or category.record_type != row["record_type"]:
                        raise ValueError("分类不存在、已停用或与收支类型不匹配")
                    row["category_id"] = str(category.id)
                    row["category_name"] = category.name
                except ValueError as exc:
                    errors.append(str(exc))
            elif raw_category_name is not None:
                category_name = str(raw_category_name).strip()
                if len(category_name) > 30:
                    errors.append("分类名称不能超过 30 个字符")
                category = category_by_name.get((row["record_type"], category_name)) if row["record_type"] else None
                if not category:
                    row["category_name"] = category_name
                    row["warnings"].append("确认时将创建此分类")
                else:
                    row["category_id"] = str(category.id)
                    row["category_name"] = category.name
            else:
                errors.append("分类不能为空")
            raw_method = LedgerMateService._import_value(raw, "payment_method_id")
            raw_method_name = LedgerMateService._import_value(raw, "payment_method", "payment_method_name", "account", "账户", "支付方式")
            if raw_method is not None:
                try:
                    method_id = LedgerMateService._import_uuid(raw_method, "支付方式")
                    method = method_by_id.get(method_id)
                    if not method:
                        raise ValueError("支付方式不存在或已停用")
                    row["payment_method_id"] = str(method.id)
                    row["payment_method_name"] = method.name
                except ValueError as exc:
                    errors.append(str(exc))
            elif raw_method_name is not None:
                method_name = str(raw_method_name).strip()
                if len(method_name) > 30:
                    errors.append("支付方式名称不能超过 30 个字符")
                method = method_by_name.get(method_name)
                if not method:
                    row["payment_method_name"] = method_name
                    row["warnings"].append("确认时将创建此支付方式")
                else:
                    row["payment_method_id"] = str(method.id)
                    row["payment_method_name"] = method.name
            raw_note = LedgerMateService._import_value(raw, "note", "remark", "备注")
            if raw_note is not None:
                row["note"] = str(raw_note).strip() or None
                if row["note"] and len(row["note"]) > 500:
                    errors.append("备注不能超过 500 个字符")
            expanded_note = " ".join(part for part in (row["note"] or "", f"[账本:{row['ledger_name']}]" if row["ledger_name"] else "", f"[二级分类:{row['secondary_category']}]" if row["secondary_category"] else "") if part).strip()
            if len(expanded_note) > 500 and "备注不能超过 500 个字符" not in errors:
                errors.append("备注扩展信息超出 500 个字符")
            if not errors:
                category_key = row["category_id"] or f"name:{row['category_name']}"
                method_key = row["payment_method_id"] or (f"name:{row['payment_method_name']}" if row["payment_method_name"] else None)
                fingerprint = (row["record_type"], row["amount_cent"], row["occurred_date"], category_key, method_key, row["note"])
                row["duplicate"] = fingerprint in fingerprints or fingerprint in existing_fingerprints
                fingerprints.add(fingerprint)
            rows.append(row)
        return rows

    @staticmethod
    def _import_summary(batch: LedgerMateImportBatch):
        rows = batch.rows or []
        return {
            "batch_id": batch.id,
            "file_name": batch.file_name,
            "total": len(rows),
            "valid_count": sum(not item.get("errors") and not item.get("duplicate") for item in rows),
            "error_count": sum(bool(item.get("errors")) for item in rows),
            "duplicate_count": sum(bool(item.get("duplicate")) for item in rows),
            "rows": rows,
        }

    @staticmethod
    async def preview_import(db: AsyncSession, user_id: uuid.UUID, data: ImportPreviewRequest):
        await LedgerMateService.ensure_defaults(db, user_id, commit=False)
        rows = await LedgerMateService._normalize_import_rows(db, user_id, data.content, data.file_name)
        batch = LedgerMateImportBatch(user_id=user_id, status="preview", file_name=data.file_name, rows=rows)
        db.add(batch)
        await db.commit()
        await db.refresh(batch)
        return LedgerMateService._import_summary(batch)

    @staticmethod
    async def _get_or_create_import_category(db: AsyncSession, user_id: uuid.UUID, record_type: str, name: str):
        category = await db.scalar(select(LedgerMateCategory).where(LedgerMateCategory.user_id == user_id, LedgerMateCategory.record_type == record_type, LedgerMateCategory.name == name, LedgerMateCategory.is_deleted == False))
        if category:
            if not category.is_enabled:
                category.is_enabled = True
            return category
        category = LedgerMateCategory(user_id=user_id, record_type=record_type, name=name, is_enabled=True, is_system=False)
        db.add(category)
        await db.flush()
        return category

    @staticmethod
    async def _get_or_create_import_method(db: AsyncSession, user_id: uuid.UUID, name: str):
        method = await db.scalar(select(LedgerMatePaymentMethod).where(LedgerMatePaymentMethod.user_id == user_id, LedgerMatePaymentMethod.name == name, LedgerMatePaymentMethod.is_deleted == False))
        if method:
            if not method.is_enabled:
                method.is_enabled = True
            return method
        method = LedgerMatePaymentMethod(user_id=user_id, name=name, is_enabled=True, is_default=False)
        db.add(method)
        await db.flush()
        return method

    @staticmethod
    async def confirm_import(db: AsyncSession, user_id: uuid.UUID, batch_id: uuid.UUID, data: ImportConfirmRequest):
        batch = await db.scalar(select(LedgerMateImportBatch).where(LedgerMateImportBatch.id == batch_id, LedgerMateImportBatch.user_id == user_id, LedgerMateImportBatch.is_deleted == False))
        if not batch:
            raise HTTPException(404, "导入批次不存在")
        if batch.status == "confirmed":
            return batch.result
        if batch.status != "preview":
            raise HTTPException(409, "导入批次当前状态不可确认")
        rows = batch.rows or []
        errors = [item for item in rows if item.get("errors")]
        duplicate_count = sum(bool(item.get("duplicate")) for item in rows)
        if errors:
            raise HTTPException(400, "导入文件存在校验错误，请修正后重新预览")
        if duplicate_count and not data.skip_duplicates:
            raise HTTPException(409, "导入文件包含重复账单，请确认 skip_duplicates=true 后重试")
        try:
            await LedgerMateService._lock_request(db, user_id, f"import:{batch_id}")
            record_ids = []
            skipped_count = 0
            for row in rows:
                if row.get("duplicate") and data.skip_duplicates:
                    skipped_count += 1
                    continue
                category_id = uuid.UUID(row["category_id"]) if row.get("category_id") else (await LedgerMateService._get_or_create_import_category(db, user_id, row["record_type"], row["category_name"])).id
                payment_method_id = uuid.UUID(row["payment_method_id"]) if row.get("payment_method_id") else (await LedgerMateService._get_or_create_import_method(db, user_id, row["payment_method_name"])).id if row.get("payment_method_name") else None
                note_parts = [row.get("note") or ""]
                if row.get("ledger_name"):
                    note_parts.append(f"[账本:{row['ledger_name']}]")
                if row.get("secondary_category"):
                    note_parts.append(f"[二级分类:{row['secondary_category']}]")
                note = " ".join(part for part in note_parts if part).strip() or None
                record = await LedgerMateService.create_record(
                    db,
                    user_id,
                    RecordCreate(
                        record_type=row["record_type"],
                        amount_cent=row["amount_cent"],
                        category_id=category_id,
                        payment_method_id=payment_method_id,
                        occurred_date=row["occurred_date"],
                        note=note,
                        idempotency_key=f"import:{batch_id}:{row['row_number']}",
                    ),
                    source="import",
                    commit=False,
                    initialize=False,
                    import_batch_id=batch_id,
                )
                record_ids.append(record.id)
            result = {"batch_id": str(batch.id), "status": "confirmed", "total": len(rows), "imported_count": len(record_ids), "skipped_count": skipped_count, "error_count": 0, "duplicate_count": duplicate_count, "record_ids": [str(item) for item in record_ids]}
            batch.status = "confirmed"
            batch.result = result
            await db.commit()
            return result
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def export_records(db: AsyncSession, user_id: uuid.UUID, start_at: Optional[datetime], end_at: Optional[datetime], record_type: Optional[str], category_id: Optional[uuid.UUID], keyword: Optional[str], output_format: str):
        stmt = select(LedgerMateRecord, LedgerMateBook.name, LedgerMateCategory.name, LedgerMatePaymentMethod.name).outerjoin(LedgerMateBook, LedgerMateBook.id == LedgerMateRecord.book_id).outerjoin(LedgerMateCategory, LedgerMateCategory.id == LedgerMateRecord.category_id).outerjoin(LedgerMatePaymentMethod, LedgerMatePaymentMethod.id == LedgerMateRecord.payment_method_id).where(LedgerMateRecord.user_id == user_id, LedgerMateRecord.is_deleted == False)
        if start_at:
            stmt = stmt.where(LedgerMateRecord.occurred_at >= start_at)
        if end_at:
            stmt = stmt.where(LedgerMateRecord.occurred_at < end_at)
        if record_type:
            stmt = stmt.where(LedgerMateRecord.record_type == record_type)
        if category_id:
            stmt = stmt.where(LedgerMateRecord.category_id == category_id)
        if keyword:
            stmt = stmt.where(LedgerMateRecord.note.ilike(f"%{keyword}%"))
        rows = (await db.execute(stmt.order_by(LedgerMateRecord.occurred_at.asc(), LedgerMateRecord.id.asc()))).all()
        values = []
        for record, book_name, category_name, method_name in rows:
            note = record.note or ""
            secondary_match = re.search(r"(?:^| )\[二级分类:(.*?)\]", note)
            ledger_match = re.search(r"(?:^| )\[账本:(.*?)\]", note)
            values.append({"date": local_datetime(record.occurred_at).date().isoformat(), "book": ledger_match.group(1) if ledger_match else (book_name or ""), "type": record.record_type, "category": category_name or "未命名分类", "subcategory": secondary_match.group(1) if secondary_match else "", "amount": f"{Decimal(record.amount_cent) / Decimal(100):.2f}", "payment_method": method_name or "", "note": re.sub(r" ?\[(?:账本|二级分类):.*?\]", "", note).strip()})
        if output_format == "json":
            content = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
            return {"file_name": "ledger-mate-export.json", "content": content, "mime_type": "application/json; charset=utf-8", "record_count": len(values)}
        output = io.StringIO(newline="")
        fieldnames = ["时间", "账本", "类型", "分类", "二级分类", "金额", "账户", "备注"]
        csv_values = [{"时间": item["date"], "账本": item["book"], "类型": "收入" if item["type"] == "income" else "支出", "分类": item["category"], "二级分类": item["subcategory"], "金额": item["amount"], "账户": item["payment_method"], "备注": item["note"]} for item in values]
        writer = csv.DictWriter(output, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_values)
        return {"file_name": "ledger-mate-export.csv", "content": "\ufeff" + output.getvalue(), "mime_type": "text/csv; charset=utf-8", "record_count": len(values)}

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
