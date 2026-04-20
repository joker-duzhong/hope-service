import json
import uuid
import logging
from typing import List, AsyncGenerator, Dict, Any, Optional
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from apps.shadow_board.models import ShadowBoardSession, ShadowBoardMessage
from apps.shadow_board.prompts import Global_Constitution, PM, Architect, Designer, QA, Silent_Scoring_Phase
from core.llm.engine import generate_chat, generate_stream_chat
from core.database import async_session_maker

logger = logging.getLogger(__name__)

ROLE_PROMPTS = {
    "PM": PM,
    "Architect": Architect,
    "Designer": Designer,
    "QA": QA
}

class ShadowBoardService:
    @staticmethod
    async def create_session(db: AsyncSession, user_id: uuid.UUID, topic: str) -> ShadowBoardSession:
        """创建新会话"""
        session = ShadowBoardSession(
            user_id=user_id,
            topic=topic,
            status="idle"
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def get_session(db: AsyncSession, session_id: uuid.UUID) -> Optional[ShadowBoardSession]:
        """获取单个会话详情"""
        result = await db.execute(select(ShadowBoardSession).where(ShadowBoardSession.id == session_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> List[ShadowBoardSession]:
        """获取用户所有会话记录"""
        result = await db.execute(
            select(ShadowBoardSession)
            .where(ShadowBoardSession.user_id == user_id, ShadowBoardSession.is_deleted == False)
            .order_by(desc(ShadowBoardSession.created_at))
        )
        return list(result.scalars().all())

    @staticmethod
    async def save_message(db: AsyncSession, session_id: uuid.UUID, role: str, content: str, is_finalized: bool = True, meta_data: dict = None) -> ShadowBoardMessage:
        """保存消息并在完成后更新会话状态"""
        msg = ShadowBoardMessage(
            session_id=session_id,
            role=role,
            content=content,
            is_finalized=is_finalized,
            meta_data=meta_data or {}
        )
        db.add(msg)
        await db.commit()
        await db.refresh(msg)
        return msg

    @staticmethod
    async def get_chat_history(db: AsyncSession, session_id: uuid.UUID) -> List[ShadowBoardMessage]:
        """获取会话全部聊天记录"""
        result = await db.execute(
            select(ShadowBoardMessage)
            .where(ShadowBoardMessage.session_id == session_id, ShadowBoardMessage.is_deleted == False)
            .order_by(ShadowBoardMessage.created_at.asc())
        )
        return list(result.scalars().all())

    @staticmethod
    async def update_session_status(db: AsyncSession, session_id: uuid.UUID, status: str, current_turn: int = None):
        """更新会话状态和轮次"""
        values = {"status": status}
        if current_turn is not None:
            values["current_turn"] = current_turn
        
        await db.execute(
            update(ShadowBoardSession)
            .where(ShadowBoardSession.id == session_id)
            .values(**values)
        )
        await db.commit()

    @staticmethod
    async def evaluate_motivation(chat_history: List[ShadowBoardMessage]) -> Dict[str, Any]:
        """评估阶段：调用 LLM 分辨下一位发言者"""
        history_text = "\n".join([f"{m.role}: {m.content}" for m in chat_history])
        
        system_prompt = Global_Constitution + "\n" + Silent_Scoring_Phase
        user_prompt = f"以下是当前的聊天记录，请进行裁决打分：\n\n{history_text}"
        
        try:
            response_str = await generate_chat(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={"type": "json_object"} # 确保输出 JSON
            )
            return json.loads(response_str)
        except Exception as e:
            logger.error(f"裁决打分失败: {e}")
            return {"winner": "None", "scores": {}}

    @staticmethod
    async def generate_role_reply_stream(role: str, chat_history: List[ShadowBoardMessage]) -> AsyncGenerator[str, None]:
        """发言阶段：调用 LLM 生成角色回复（流式）"""
        history_messages = [{"role": "user" if m.role == "CEO" else "assistant", "content": f"[{m.role}]: {m.content}"} for m in chat_history]
        
        role_prompt = ROLE_PROMPTS.get(role, "")
        system_prompt = Global_Constitution + "\n" + role_prompt
        
        # 将 system prompt 注入
        messages = [{"role": "system", "content": system_prompt}] + history_messages
        
        async for chunk in generate_stream_chat(messages=messages):
            yield chunk
