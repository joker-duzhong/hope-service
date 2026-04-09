"""
AI Gateway 模块数据格式校验
"""
import uuid
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict


class AISessionBase(BaseModel):
    title: str = "新对话"
    provider: Optional[str] = None
    model_name: Optional[str] = None


class AISessionCreate(AISessionBase):
    pass


class AISessionUpdate(BaseModel):
    title: Optional[str] = None


class AIMessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    content: str
    tokens_used: Optional[int] = None
    scope: Optional[str] = None
    created_at: datetime


class AISessionRead(AISessionBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scope: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ChatCompletionRequest(BaseModel):
    session_id: Optional[uuid.UUID] = None  # 如果不传，则新建会话
    prompt: str
    stream: bool = False
    provider: Optional[str] = None
    model: Optional[str] = None
    max_history: int = 10  # 最大上下文历史条数


class ChatCompletionResponse(BaseModel):
    session_id: uuid.UUID
    role: str = "assistant"
    content: str
    history_count: int
