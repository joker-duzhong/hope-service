"""
AI Gateway 模块数据格式校验
"""
import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.storage.schemas import ResourceResponse


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


class ImageStreamChatRequest(BaseModel):
    model: str = Field(default="gpt-image-2", description="图片模型名称")
    messages: List[dict[str, Any]] = Field(..., description="OpenAI Chat Completions messages")
    provider: Optional[str] = Field(default=None, description="LLM 提供商，不传则使用默认提供商")
    size: Optional[str] = Field(default=None, description="图片尺寸，如 1024x1024；不传则由上游默认处理")
    quality: Optional[str] = Field(default=None, description="图片质量，如 low/medium/high/auto；不传则由上游默认处理")
    background: Optional[str] = Field(default=None, description="背景模式，如 auto/transparent/opaque；不传则由上游默认处理")
    output_format: Optional[str] = Field(default=None, description="输出格式，如 png/jpeg/webp；不传则由上游默认处理")
    output_compression: Optional[int] = Field(default=None, ge=0, le=100, description="输出压缩质量，通常仅 jpeg/webp 有效")
    n: Optional[int] = Field(default=None, ge=1, le=10, description="生成图片数量；不传则由上游默认处理")
    temperature: float = Field(default=0.7, ge=0, le=2, description="采样随机性，越高越发散")
    top_p: float = Field(default=1.0, ge=0, le=1, description="核采样阈值，通常保持 1")
    timeout: Optional[float] = Field(default=None, gt=0, description="请求超时时间秒数，不传则使用提供商配置")
    extra_body: dict[str, Any] = Field(default_factory=dict, description="透传给上游的额外 JSON 参数")


class ImageStreamChatResponse(BaseModel):
    content: str
    resource: ResourceResponse
