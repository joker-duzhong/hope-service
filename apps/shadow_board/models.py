import uuid
from sqlalchemy import Column, String, JSON, ForeignKey, Text, Integer, Boolean, DateTime, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column
from core.database import CoreModel

class ShadowBoardSession(CoreModel):
    """影子董事会会话表"""
    __tablename__ = "shadow_board_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    topic: Mapped[str] = mapped_column(String(255), nullable=False)
    # 状态: idle (待发言), scoring (打分中), speaking (生成中), done (结束/共识), paused (到达轮次限制)
    status: Mapped[str] = mapped_column(String(50), default="idle")
    
    # 扩展字段：参与角色 (List of strings), 模型配置 (Config dict)
    roles_config: Mapped[list[str]] = mapped_column(JSON, default=["PM", "Architect", "Designer", "QA"])
    model_config: Mapped[dict] = mapped_column(JSON, default={})
    
    # 轮次统计与限制
    current_turn: Mapped[int] = mapped_column(Integer, default=0)
    max_turns: Mapped[int] = mapped_column(Integer, default=10)

class ShadowBoardMessage(CoreModel):
    """影子董事会消息表"""
    __tablename__ = "shadow_board_messages"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, nullable=False)
    role: Mapped[str] = mapped_column(String(50), nullable=False) # CEO (User), PM, Architect, Designer, QA
    content: Mapped[str] = mapped_column(Text, nullable=False)
    is_finalized: Mapped[bool] = mapped_column(Boolean, default=True)
    
    # 额外的元数据（如果是 AI 生成，记录消耗或打分信息）
    meta_data: Mapped[dict] = mapped_column(JSON, default={})
