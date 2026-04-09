"""
AI Gateway 模块数据库模型
"""
import uuid
from typing import Optional

from sqlalchemy import Column, String, ForeignKey, Text, Integer, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import CoreModel


class AISession(CoreModel):
    """AI 会话表"""
    __tablename__ = "ai_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    scope: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), default="新对话")
    provider: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(100))
    
    # 关联消息
    messages: Mapped[list["AIMessage"]] = relationship("AIMessage", back_populates="session", lazy="selectin", cascade="all, delete-orphan")


class AIMessage(CoreModel):
    """AI 消息表"""
    __tablename__ = "ai_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("ai_sessions.id"), index=True)
    scope: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(20))  # user/assistant/system
    content: Mapped[str] = mapped_column(Text)
    tokens_used: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # 反向关联
    session: Mapped["AISession"] = relationship("AISession", back_populates="messages")
