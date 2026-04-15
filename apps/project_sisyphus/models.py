"""
Project Sisyphus - 数据库模型
表前缀: sisyphus_
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Integer, Float, Boolean, DateTime, JSON, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import CoreModel


class KnowledgeNode(CoreModel):
    """知识节点表 - 抽象化的知识突触，不绑定具体学科"""
    __tablename__ = "sisyphus_knowledge_nodes"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    domain: Mapped[str] = mapped_column(String(50), default="esl", index=True)
    concept_description: Mapped[str] = mapped_column(Text)
    mastery_score: Mapped[float] = mapped_column(Float, default=0.0)
    next_review_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    # FSRS 算法状态字段
    fsrs_state: Mapped[int] = mapped_column(Integer, default=0)  # 0=New, 1=Learning, 2=Review, 3=Relearning
    fsrs_stability: Mapped[float] = mapped_column(Float, default=0.0)
    fsrs_difficulty: Mapped[float] = mapped_column(Float, default=0.0)
    fsrs_reps: Mapped[int] = mapped_column(Integer, default=0)
    fsrs_lapses: Mapped[int] = mapped_column(Integer, default=0)


class LearningSession(CoreModel):
    """学习会话表 - 追踪一次完整的场景学习流程"""
    __tablename__ = "sisyphus_learning_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    scenario_description: Mapped[str] = mapped_column(Text)
    scenario_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    target_node_ids: Mapped[Optional[list]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active, completed, abandoned
    node_fail_counts: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class InteractionLog(CoreModel):
    """交互日志表 - 记录每一轮对话"""
    __tablename__ = "sisyphus_interaction_logs"

    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    turn_number: Mapped[int] = mapped_column(Integer)
    user_input: Mapped[str] = mapped_column(Text)
    ai_json_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    time_taken_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_deadlock_triggered: Mapped[bool] = mapped_column(Boolean, default=False)
