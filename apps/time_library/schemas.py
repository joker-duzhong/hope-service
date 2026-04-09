"""
时空图书馆 Pydantic 模型
遵守 Pydantic V2 语法
"""
import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- 公共基础模型 ---

class SchemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- 书籍内容 (Content) ---

class BookContentBase(SchemaBase):
    chapter_title: str = Field(..., max_length=200, description="章节名称")
    content: str = Field(..., description="内容全文")
    order: int = Field(0, description="章节排序")


class BookContentCreate(BookContentBase):
    pass


class BookContentRead(BookContentBase):
    id: uuid.UUID
    created_at: datetime


# --- AI 人设 (AIPersona) ---

class AIPersonaBase(SchemaBase):
    name: str = Field(..., max_length=100)
    system_prompt: str = Field(..., description="系统提示词")
    avatar_url: Optional[str] = Field(None, max_length=500)


class AIPersonaCreate(AIPersonaBase):
    pass


class AIPersonaRead(AIPersonaBase):
    id: uuid.UUID
    created_at: datetime


# --- 书籍 (Book) ---

class BookBase(SchemaBase):
    title: str = Field(..., max_length=200, description="书名")
    author: str = Field(..., max_length=100, description="作者")
    year: int = Field(..., description="年份")
    latitude: float = Field(..., description="纬度 (-90~90)")
    longitude: float = Field(..., description="经度 (-180~180)")
    cover_url: Optional[str] = Field(None, max_length=500)
    description: Optional[str] = Field(None)

    @field_validator("latitude")
    @classmethod
    def validate_latitude(cls, v: float) -> float:
        if not -90 <= v <= 90:
            raise ValueError("纬度必须在 -90 到 90 之间")
        return v

    @field_validator("longitude")
    @classmethod
    def validate_longitude(cls, v: float) -> float:
        if not -180 <= v <= 180:
            raise ValueError("经度必须在 -180 到 180 之间")
        return v


class BookCreate(BookBase):
    pass


class BookUpdate(SchemaBase):
    title: Optional[str] = Field(None, max_length=200)
    author: Optional[str] = Field(None, max_length=100)
    year: Optional[int] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    cover_url: Optional[str] = None
    description: Optional[str] = None


class BookListRead(BookBase):
    id: uuid.UUID
    created_at: datetime


class BookDetailRead(BookListRead):
    contents: List[BookContentRead] = []
    ai_persona: Optional[AIPersonaRead] = None


# --- 对话 (Chat) ---

class ChatSessionRead(SchemaBase):
    id: uuid.UUID
    book_id: uuid.UUID
    title: str
    created_at: datetime


class ChatMessageRead(SchemaBase):
    id: uuid.UUID
    role: str
    content: str
    created_at: datetime
