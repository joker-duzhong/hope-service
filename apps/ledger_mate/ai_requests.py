"""持久记账请求：接收先提交，后台短事务领取，结果与账单原子保存。"""
import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional

from fastapi import HTTPException
from sqlalchemy import case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.dates import SHANGHAI, local_datetime
from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiSession
from apps.ledger_mate.schemas import AiMessageCreate
from apps.ledger_mate.services import LedgerMateService

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_SECONDS = 180
PROCESS_TIMEOUT_SECONDS = 120
RequestStatus = Literal["queued", "processing", "completed", "failed"]


@dataclass
class AiRequestState:
    session: LedgerMateAiSession
    user_message: LedgerMateAiMessage
    assistant_message: Optional[LedgerMateAiMessage]
    status: RequestStatus
    error_message: Optional[str] = None


class AiRequestService:
    @staticmethod
    def _active_messages():
        return select(LedgerMateAiMessage).join(
            LedgerMateAiSession, LedgerMateAiSession.id == LedgerMateAiMessage.session_id,
        ).where(
            LedgerMateAiMessage.role == "user",
            LedgerMateAiMessage.is_deleted == False,
            LedgerMateAiSession.is_deleted == False,
            LedgerMateAiSession.user_id == LedgerMateAiMessage.user_id,
        ).execution_options(populate_existing=True)

    @staticmethod
    async def _message(db, user_id, session_id, client_message_id):
        return await db.scalar(AiRequestService._active_messages().where(
            LedgerMateAiMessage.user_id == user_id,
            LedgerMateAiMessage.session_id == session_id,
            LedgerMateAiMessage.payload["client_message_id"].as_string() == client_message_id,
        ))

    @staticmethod
    async def _state(db, session, message):
        assistant = await db.scalar(select(LedgerMateAiMessage).where(
            LedgerMateAiMessage.user_id == message.user_id,
            LedgerMateAiMessage.session_id == message.session_id,
            LedgerMateAiMessage.role == "assistant",
            LedgerMateAiMessage.payload["reply_to"].as_string() == str(message.id),
            LedgerMateAiMessage.is_deleted == False,
        ).execution_options(populate_existing=True))
        payload = message.payload or {}
        status = "completed" if assistant else payload.get("request_status", "failed")
        return AiRequestState(session, message, assistant, status, payload.get("error_message") if status == "failed" else None)

    @staticmethod
    async def accept(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, data: AiMessageCreate) -> AiRequestState:
        await LedgerMateService.get_ai_session(db, user_id, session_id)
        await LedgerMateService.ensure_defaults(db, user_id)
        try:
            session = await LedgerMateService.get_ai_session(db, user_id, session_id, for_update=True)
            existing = await AiRequestService._message(db, user_id, session_id, data.client_message_id)
            if existing:
                if existing.content != data.content:
                    raise HTTPException(409, "此消息编号已用于其他内容，请为新消息生成新编号")
                state = await AiRequestService._state(db, session, existing)
                if state.status != "failed":
                    await db.commit()
                    return state
            pending = await db.scalar(AiRequestService._active_messages().where(
                LedgerMateAiMessage.user_id == user_id,
                LedgerMateAiMessage.session_id == session_id,
                LedgerMateAiMessage.payload["request_status"].as_string().in_(["queued", "processing"]),
            ).limit(1))
            if pending:
                raise HTTPException(409, "此会话还有消息处理中，请等待结果后再发送")
            now = datetime.now(SHANGHAI)
            payload = {
                "client_message_id": data.client_message_id,
                "request_status": "queued",
                "accepted_at": (existing.payload or {}).get("accepted_at", now.isoformat()) if existing else now.isoformat(),
                "attempts": 0,
            }
            if existing:
                existing.payload = payload
                message = existing
            else:
                message = await LedgerMateService._save_message(db, user_id, session_id, "user", data.content, payload)
            session.updated_at = now
            await db.commit()
            return AiRequestState(session, message, None, "queued")
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def get(db: AsyncSession, user_id: uuid.UUID, session_id: uuid.UUID, client_message_id: str) -> AiRequestState:
        session = await LedgerMateService.get_ai_session(db, user_id, session_id)
        message = await AiRequestService._message(db, user_id, session_id, client_message_id)
        if not message:
            raise HTTPException(404, "记账请求不存在")
        return await AiRequestService._state(db, session, message)

    @staticmethod
    async def list_pending(db: AsyncSession, user_id: uuid.UUID) -> list[AiRequestState]:
        messages = (await db.scalars(AiRequestService._active_messages().where(
            LedgerMateAiMessage.user_id == user_id,
            LedgerMateAiMessage.payload["request_status"].as_string().in_(["queued", "processing"]),
        ).order_by(LedgerMateAiMessage.created_at))).all()
        result = []
        for message in messages:
            session = await LedgerMateService.get_ai_session(db, user_id, message.session_id)
            result.append(await AiRequestService._state(db, session, message))
        return result

    @staticmethod
    def _is_due(value, now):
        return not value or local_datetime(datetime.fromisoformat(value)) <= now

    @staticmethod
    def _finished_payload(payload, status, *, error=None):
        result = {key: value for key, value in payload.items() if key not in ("lease_token", "lease_expires_at", "retry_after", "error_message")}
        result["request_status"] = status
        if error:
            result["error_message"] = error
        return result

    @staticmethod
    async def process(db: AsyncSession, message_id: uuid.UUID) -> str:
        message = await db.scalar(AiRequestService._active_messages().where(LedgerMateAiMessage.id == message_id))
        if not message or not (message.payload or {}).get("request_status"):
            await db.commit()
            return "missing"
        user_id, session_id = message.user_id, message.session_id
        lease_token = str(uuid.uuid4())
        try:
            await LedgerMateService.get_ai_session(db, user_id, session_id, for_update=True)
            message = await db.scalar(AiRequestService._active_messages().where(LedgerMateAiMessage.id == message_id))
            payload = dict(message.payload or {})
            status = payload["request_status"]
            now = datetime.now(SHANGHAI)
            if status in ("completed", "failed") or (status == "processing" and not AiRequestService._is_due(payload.get("lease_expires_at"), now)) or (status == "queued" and not AiRequestService._is_due(payload.get("retry_after"), now)):
                await db.commit()
                return status
            if payload.get("attempts", 0) >= MAX_ATTEMPTS:
                message.payload = AiRequestService._finished_payload(payload, "failed", error="处理多次未完成，请重试这条消息")
                await db.commit()
                return "failed"
            payload.update({"request_status": "processing", "attempts": payload.get("attempts", 0) + 1, "lease_token": lease_token, "lease_expires_at": (now + timedelta(seconds=LEASE_SECONDS)).isoformat()})
            message.payload = payload
            content, client_message_id = message.content, payload["client_message_id"]
            accepted_at = local_datetime(datetime.fromisoformat(payload["accepted_at"]))
            created_at = message.created_at
            await db.commit()

            history = (await db.scalars(select(LedgerMateAiMessage).where(
                LedgerMateAiMessage.user_id == user_id,
                LedgerMateAiMessage.session_id == session_id,
                LedgerMateAiMessage.is_deleted == False,
                LedgerMateAiMessage.id != message_id,
                LedgerMateAiMessage.created_at <= created_at,
                or_(LedgerMateAiMessage.role == "assistant", LedgerMateAiMessage.payload["request_status"].as_string().is_(None), LedgerMateAiMessage.payload["request_status"].as_string() == "completed"),
            ).order_by(LedgerMateAiMessage.created_at.desc(), case((LedgerMateAiMessage.role == "assistant", 1), else_=0).desc(), LedgerMateAiMessage.id.desc()).limit(20))).all()
            categories = await LedgerMateService.categories(db, user_id, initialize=False)
            methods = await LedgerMateService.methods(db, user_id, initialize=False)
            # 模型调用前释放数据库事务和连接，状态查询不需等待模型。
            await db.commit()
            parsed = await asyncio.wait_for(
                LedgerMateService._parse_ai_input(content, list(reversed(history)), categories, methods, accepted_at, session_id),
                timeout=PROCESS_TIMEOUT_SECONDS,
            )
            session = await LedgerMateService.get_ai_session(db, user_id, session_id, for_update=True)
            message = await db.scalar(AiRequestService._active_messages().where(LedgerMateAiMessage.id == message_id))
            if not message or (message.payload or {}).get("lease_token") != lease_token:
                await db.commit()
                return "processing"
            await LedgerMateService._save_ai_result(db, user_id, session, message, parsed, client_message_id)
            message.payload = AiRequestService._finished_payload(message.payload, "completed")
            await db.commit()
            return "completed"
        except Exception as exc:
            await db.rollback()
            logger.warning("后台记账处理失败 message_id=%s exception_type=%s", message_id, type(exc).__name__)
            await LedgerMateService.get_ai_session(db, user_id, session_id, for_update=True)
            message = await db.scalar(AiRequestService._active_messages().where(LedgerMateAiMessage.id == message_id))
            if not message or (message.payload or {}).get("lease_token") != lease_token:
                await db.commit()
                return "processing"
            attempts = message.payload.get("attempts", 0)
            status = "failed" if attempts >= MAX_ATTEMPTS else "queued"
            error = exc.detail if isinstance(exc, HTTPException) else "记账处理暂时未完成，请稍后重试这条消息"
            payload = AiRequestService._finished_payload(message.payload, status, error=error)
            if status == "queued":
                payload["retry_after"] = (datetime.now(SHANGHAI) + timedelta(seconds=10 * attempts)).isoformat()
            message.payload = payload
            await db.commit()
            return status
        except BaseException:
            await db.rollback()
            raise

    @staticmethod
    async def recover(db: AsyncSession, limit: int = 100) -> list[uuid.UUID]:
        now = datetime.now(SHANGHAI).isoformat()
        payload = LedgerMateAiMessage.payload
        queued = (payload["request_status"].as_string() == "queued") & or_(payload["retry_after"].as_string().is_(None), payload["retry_after"].as_string() <= now)
        expired = (payload["request_status"].as_string() == "processing") & or_(payload["lease_expires_at"].as_string().is_(None), payload["lease_expires_at"].as_string() <= now)
        messages = (await db.scalars(AiRequestService._active_messages().where(or_(queued, expired)).order_by(LedgerMateAiMessage.updated_at, LedgerMateAiMessage.id).limit(max(1, min(limit, 1000))))).all()
        message_ids = [message.id for message in messages]
        await db.commit()
        return message_ids
