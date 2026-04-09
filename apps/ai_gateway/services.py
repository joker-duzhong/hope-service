"""
AI Gateway 模块核心业务逻辑
"""
import uuid
from typing import List, Optional, AsyncGenerator

from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from apps.ai_gateway.models import AISession, AIMessage
from apps.ai_gateway.schemas import AISessionCreate, AISessionUpdate
from core.llm import engine
from core.config import settings


class AISessionService:
    """AI 会话服务"""

    @staticmethod
    async def create_session(
        db: SqlAsyncSession, 
        user_id: uuid.UUID, 
        data: AISessionCreate,
        scope: Optional[str] = None
    ) -> AISession:
        provider = data.provider or settings.LLM_DEFAULT_PROVIDER
        config = settings.LLM_PROVIDERS.get(provider, {})
        model_name = data.model_name or config.get("default_model", "gpt-3.5-turbo")
        
        session = AISession(
            user_id=user_id,
            scope=scope,
            title=data.title,
            provider=provider,
            model_name=model_name
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def get_session(db: SqlAsyncSession, session_id: uuid.UUID, user_id: uuid.UUID) -> Optional[AISession]:
        stmt = select(AISession).where(AISession.id == session_id, AISession.user_id == user_id, AISession.is_deleted == False)
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def list_sessions(db: SqlAsyncSession, user_id: uuid.UUID) -> List[AISession]:
        stmt = select(AISession).where(AISession.user_id == user_id, AISession.is_deleted == False).order_by(desc(AISession.updated_at))
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def delete_session(db: SqlAsyncSession, session_id: uuid.UUID, user_id: uuid.UUID):
        stmt = update(AISession).where(AISession.id == session_id, AISession.user_id == user_id).values(is_deleted=True)
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def update_session(db: SqlAsyncSession, session_id: uuid.UUID, user_id: uuid.UUID, data: AISessionUpdate) -> Optional[AISession]:
        stmt = update(AISession).where(AISession.id == session_id, AISession.user_id == user_id).values(**data.model_dump(exclude_unset=True))
        await db.execute(stmt)
        await db.commit()
        return await AISessionService.get_session(db, session_id, user_id)


class AIMessageService:
    """AI 消息服务"""

    @staticmethod
    async def create_message(
        db: SqlAsyncSession,
        session_id: uuid.UUID,
        role: str,
        content: str,
        tokens_used: Optional[int] = None,
        scope: Optional[str] = None
    ) -> AIMessage:
        message = AIMessage(
            session_id=session_id,
            role=role,
            content=content,
            tokens_used=tokens_used,
            scope=scope
        )
        db.add(message)
        # 更新会话的 updated_at 以使会话在列表中置顶
        await db.execute(update(AISession).where(AISession.id == session_id).values(updated_at=None))
        await db.commit()
        await db.refresh(message)
        return message

    @staticmethod
    async def get_history_messages(db: SqlAsyncSession, session_id: uuid.UUID, limit: int = 10) -> List[dict]:
        """
        获取上下文历史消息，已排好序供 OpenAI 使用
        """
        stmt = select(AIMessage).where(AIMessage.session_id == session_id).order_by(desc(AIMessage.created_at)).limit(limit)
        result = await db.execute(stmt)
        messages = list(result.scalars().all())
        # 反转列表以保持时间顺序
        return [{"role": m.role, "content": m.content} for m in reversed(messages)]


class AIChatService:
    """AI 对话集成服务"""

    @staticmethod
    async def chat_background_save(
        db_factory,
        session_id: uuid.UUID,
        user_prompt: str,
        ai_response: str,
        scope: Optional[str] = None
    ):
        """
        后台任务：异步保存用户提问和 AI 回答
        通常在流式响应结束后执行
        """
        async with db_factory() as db:
            # 1. 保存用户提问
            await AIMessageService.create_message(db, session_id, "user", user_prompt, scope=scope)
            # 2. 保存 AI 回答
            await AIMessageService.create_message(db, session_id, "assistant", ai_response, scope=scope)

    @staticmethod
    async def stream_generator(
        db_factory,
        session_id: uuid.UUID,
        user_prompt: str,
        history: list[dict],
        provider: Optional[str] = None,
        model: Optional[str] = None
    ) -> AsyncGenerator[str, None]:
        """
        SSE 流式生成器，并在结束后在内部累积全文，由外部 BackgroundTasks 调用异步落库
        """
        full_content = ""
        # 将用户当前输入加入上下文（不重复落库，仅用于生成）
        current_messages = history + [{"role": "user", "content": user_prompt}]
        
        async for chunk in engine.generate_stream_chat(current_messages, provider, model):
            full_content += chunk
            yield f"data: {chunk}\n\n"
        
        yield "data: [DONE]\n\n"
        
        # 注意：这里并不直接调用落库，而是由 router 控制 BackgroundTask
        # 但我们需要某种方式把 full_content 带回 router
        # 这里实际上我们会返回 full_content 的存储任务
