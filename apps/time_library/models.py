"""
时空图书馆领域模型
表名前缀: time_library_
"""
import uuid
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import String, Integer, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from core.database import CoreModel

if TYPE_CHECKING:
    from core.users.models import User


class Book(CoreModel):
    """书籍主表"""
    __tablename__ = "time_library_books"

    title: Mapped[str] = mapped_column(String(200), nullable=False, index=True, comment="书名")
    author: Mapped[str] = mapped_column(String(100), nullable=False, index=True, comment="作者")
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="年份 (负数为公元前)")
    latitude: Mapped[float] = mapped_column(Float, nullable=False, comment="纬度")
    longitude: Mapped[float] = mapped_column(Float, nullable=False, comment="经度")
    cover_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="封面地址")
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="书籍简介")

    # 关联
    contents: Mapped[List["BookContent"]] = relationship("BookContent", back_populates="book", cascade="all, delete-orphan")
    ai_persona: Mapped[Optional["AIPersona"]] = relationship("AIPersona", back_populates="book", uselist=False, cascade="all, delete-orphan")
    chat_sessions: Mapped[List["ChatSession"]] = relationship("ChatSession", back_populates="book")


class BookContent(CoreModel):
    """书籍内容/章节表"""
    __tablename__ = "time_library_book_contents"

    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("time_library_books.id"), nullable=False, index=True)
    chapter_title: Mapped[str] = mapped_column(String(200), nullable=False, comment="章节名称")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="章节内容")
    order: Mapped[int] = mapped_column(Integer, default=0, comment="章节排序")

    book: Mapped["Book"] = relationship("Book", back_populates="contents")


class AIPersona(CoreModel):
    """AI 人设表 (模拟书中角色)"""
    __tablename__ = "time_library_ai_personas"

    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("time_library_books.id"), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="人设名称")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False, comment="系统级人设提示词")
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="AI 头像")

    book: Mapped["Book"] = relationship("Book", back_populates="ai_persona")


class ChatSession(CoreModel):
    """对话会话表 (预留)"""
    __tablename__ = "time_library_chat_sessions"

    book_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("time_library_books.id"), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), default="新对话")

    book: Mapped["Book"] = relationship("Book", back_populates="chat_sessions")
    messages: Mapped[List["ChatMessage"]] = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(CoreModel):
    """对话详情表 (预留)"""
    __tablename__ = "time_library_chat_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("time_library_chat_sessions.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色: user/assistant/system")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")

    session: Mapped["ChatSession"] = relationship("ChatSession", back_populates="messages")
