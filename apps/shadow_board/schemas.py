import uuid
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field

# --- 输入模型 ---

class SendMessageRequest(BaseModel):
    """发送消息请求"""
    text: str = Field(..., description="用户输入的聊天内容")
    topic: Optional[str] = Field(None, description="若是新会话，则创建并指定主题")
    session_id: Optional[uuid.UUID] = Field(None, description="若为已有会话，则提供会话 ID")

# --- 输出模型 ---

class MessageResponse(BaseModel):
    """单条消息响应"""
    id: uuid.UUID
    session_id: uuid.UUID
    role: str
    content: str
    is_finalized: bool
    created_at: datetime
    meta_data: Optional[Dict[str, Any]] = None

    class Config:
        from_attributes = True

class SessionStatusResponse(BaseModel):
    """会话状态响应"""
    id: uuid.UUID
    topic: str
    status: str
    current_turn: int
    max_turns: int
    roles_config: List[str]
    created_at: datetime
    
    class Config:
        from_attributes = True

class ChatInitResponse(BaseModel):
    """对话初始化响应"""
    session_id: uuid.UUID
    status: str
    message: Optional[str] = "已接收消息，AI 后台辩论循环启动。"

class SessionHistoryResponse(BaseModel):
    """会话历史记录列表"""
    sessions: List[SessionStatusResponse]
    
class ChatHistoryResponse(BaseModel):
    """对话历史记录列表"""
    messages: List[MessageResponse]
