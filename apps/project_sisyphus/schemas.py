"""
Project Sisyphus - Pydantic 数据验证模型
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any

from pydantic import BaseModel, ConfigDict, Field


# ==================== 知识节点 ====================

class KnowledgeNodeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    domain: str
    concept_description: str
    mastery_score: float
    next_review_at: Optional[datetime] = None
    fsrs_state: int = 0
    fsrs_reps: int = 0
    fsrs_lapses: int = 0
    created_at: datetime
    updated_at: datetime


# ==================== 视觉主题 ====================

class ScenarioTheme(BaseModel):
    """学习场景的视觉主题"""
    primary_color: str = "#1a1a2e"
    secondary_color: str = "#16213e"
    accent_color: str = "#e94560"
    text_color: str = "#ffffff"
    background_image: Optional[str] = None
    mood: str = "neutral"  # neutral, tense, celebratory, melancholic


class VisualElement(BaseModel):
    """随导师响应一起渲染的视觉元素"""
    type: str = "image"  # image, html_content, highlight_words
    url: Optional[str] = None
    html_content: Optional[str] = None
    words: Optional[List[str]] = None
    alt_text: Optional[str] = None


# ==================== 学习会话 ====================

class SessionStatusFilter(str, Enum):
    active = "active"
    completed = "completed"
    abandoned = "abandoned"


class LearningSessionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    scenario_description: str
    scenario_data: Optional[Dict[str, Any]] = None
    target_node_ids: Optional[List[str]] = None
    status: str
    node_fail_counts: Optional[Dict[str, int]] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class InteractionLogRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    turn_number: int
    user_input: str
    ai_json_response: Optional[str] = None
    time_taken_ms: Optional[int] = None
    is_deadlock_triggered: bool = False
    created_at: datetime


class SessionDetailResponse(LearningSessionRead):
    """会话详情（含交互日志和目标节点）"""
    interaction_logs: List[InteractionLogRead] = []
    target_nodes: List[KnowledgeNodeRead] = []


class StartSessionResponse(BaseModel):
    """开始学习会话的响应"""
    session_id: Optional[uuid.UUID] = None
    scenario_description: str
    scenario_data: Optional[Dict[str, Any]] = None
    target_nodes: List[KnowledgeNodeRead] = []
    ai_initial_speech: str = ""
    interaction_type: str = "chat"
    component_data: Optional[Dict[str, Any]] = None
    theme: Optional[ScenarioTheme] = None


# ==================== 对话 ====================

class ChatRequest(BaseModel):
    """用户对话请求"""
    session_id: uuid.UUID
    user_input: str = Field(..., max_length=2000, description="用户输入内容")
    time_taken_ms: Optional[int] = Field(None, ge=0, description="用户思考耗时(毫秒)，由前端记录")


class TutorResponse(BaseModel):
    """导师 Agent 的结构化响应"""
    thought_process: str = ""
    emotional_support: str = ""
    interaction_type: str = Field(default="chat", pattern="^(chat|cloze|reorder)$")
    ai_speech: str = ""
    component_data: Optional[Dict[str, Any]] = None
    is_target_met: bool = False
    visual_elements: Optional[List[VisualElement]] = None


class ChatResponse(BaseModel):
    """对话接口的响应"""
    session_id: uuid.UUID
    turn_number: int
    is_deadlock_triggered: bool = False
    deadlock_warning: bool = False
    session_completed: bool = False
    tutor: TutorResponse
    mastery_snapshot: Optional[Dict[str, float]] = None


# ==================== 挑战 (我不服) ====================

class ChallengeRequest(BaseModel):
    """用户挑战请求"""
    session_id: uuid.UUID
    challenge_reason: str


class ChallengeResponse(BaseModel):
    """挑战裁决响应"""
    session_id: uuid.UUID
    original_verdict: bool
    new_verdict: bool
    arbiter_explanation: str
    next_action: Optional[str] = None  # "continue" | "start_new"


# ==================== 设定目标 (冷启动) ====================

class SetGoalRequest(BaseModel):
    """设定学习目标"""
    goal: str = Field(..., min_length=2, max_length=500, description="学习目标描述")


class GoalNode(BaseModel):
    """目标解构出的知识节点"""
    concept: str
    domain: str = "esl"
    reason: str = ""


class SetGoalResponse(BaseModel):
    """设定目标响应"""
    goal: str
    generated_nodes: List[GoalNode] = []
    duplicate_warning: Optional[List[KnowledgeNodeRead]] = None


# ==================== 场景生成器内部结构 ====================

class ScenarioData(BaseModel):
    """场景生成器输出的结构"""
    scenario_title: str = ""
    scenario_description: str = ""
    scenario_setting: str = ""
    target_objectives: List[str] = []
    difficulty_level: str = "medium"
    theme: Optional[ScenarioTheme] = None


# ==================== 知识提取器内部结构 ====================

class ExtractedNode(BaseModel):
    """提取器输出的知识节点"""
    concept: str
    domain: str = "esl"
    node_type: str = "vocabulary"  # vocabulary, grammar, phrase, error_pattern
    mastery_delta: float = 0.0  # 正值表示进步，负值表示退步


class ExtractionResult(BaseModel):
    """提取器的完整输出"""
    new_nodes: List[ExtractedNode] = []
    existing_node_updates: List[Dict[str, Any]] = []
