"""
Project Sisyphus - 核心业务逻辑层
包含：知识节点 CRUD、学习会话管理、AI Agent 调度、FSRS 调度、JSON 解析
"""
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from string import Template
from typing import Optional, List, Dict, Any, Tuple

from sqlalchemy import select, update, func, or_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.project_sisyphus.models import KnowledgeNode, LearningSession, InteractionLog
from apps.project_sisyphus.schemas import (
    TutorResponse,
    ScenarioData,
    GoalNode,
    ExtractionResult,
)
from apps.project_sisyphus.prompts import (
    TUTOR_SYSTEM_PROMPT,
    TUTOR_DEADLOCK_OVERRIDE_PROMPT,
    TUTOR_TARGET_MET_PROMPT,
    SCENARIO_GENERATOR_PROMPT,
    MEMORY_EXTRACTOR_PROMPT,
    GOAL_DECONSTRUCTOR_PROMPT,
    ARBITER_PROMPT,
)
from core.llm import engine

logger = logging.getLogger(__name__)


# ==================== 健壮的 JSON 解析器 ====================

def extract_json_from_llm(raw: str) -> dict:
    """
    健壮的 JSON 提取器，处理 LLM 输出的各种格式：
    1. 纯 JSON
    2. ```json ... ``` 代码块包裹
    3. ``` ... ``` 包裹但无语言标识
    4. JSON 前后有无关文字
    5. 多层嵌套的代码块
    """
    text = raw.strip()

    # 策略 1: 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # 策略 2: 提取 markdown 代码块中的内容
    code_block_patterns = [
        r"```json\s*\n?(.*?)\n?\s*```",
        r"```\s*\n?(.*?)\n?\s*```",
    ]
    for pattern in code_block_patterns:
        matches = re.findall(pattern, text, re.DOTALL)
        for match in matches:
            try:
                return json.loads(match.strip())
            except json.JSONDecodeError:
                continue

    # 策略 3: 找到第一个 { 和最后一个 } 之间的内容
    first_brace = text.find("{")
    last_brace = text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        candidate = text[first_brace : last_brace + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass

    # 策略 4: 找 [ ... ] 数组格式
    first_bracket = text.find("[")
    last_bracket = text.rfind("]")
    if first_bracket != -1 and last_bracket != -1 and last_bracket > first_bracket:
        candidate = text[first_bracket : last_bracket + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"无法从 LLM 输出中提取 JSON。原始输出前200字符: {text[:200]}")


# ==================== 知识节点 CRUD ====================

class KnowledgeNodeService:
    """知识节点 CRUD 服务"""

    @staticmethod
    async def get_nodes_due_for_review(
        db: AsyncSession, user_id: uuid.UUID, limit: int = 5
    ) -> List[KnowledgeNode]:
        """获取今日需复习的知识节点"""
        now = datetime.now(timezone.utc)
        stmt = (
            select(KnowledgeNode)
            .where(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.is_deleted == False,
                KnowledgeNode.next_review_at <= now,
            )
            .order_by(KnowledgeNode.next_review_at.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_under_mastered_nodes(
        db: AsyncSession, user_id: uuid.UUID, limit: int = 5, domain: str = "esl"
    ) -> List[KnowledgeNode]:
        """获取尚未完全掌握的节点（用于无复习任务时随机挑选）"""
        stmt = (
            select(KnowledgeNode)
            .where(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.is_deleted == False,
                KnowledgeNode.domain == domain,
                KnowledgeNode.mastery_score < 8.0,
            )
            .order_by(KnowledgeNode.mastery_score.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_all_nodes(
        db: AsyncSession, user_id: uuid.UUID, domain: str = "esl"
    ) -> List[KnowledgeNode]:
        """获取用户的所有知识节点"""
        stmt = (
            select(KnowledgeNode)
            .where(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.is_deleted == False,
                KnowledgeNode.domain == domain,
            )
            .order_by(KnowledgeNode.mastery_score.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_all_nodes_paginated(
        db: AsyncSession,
        user_id: uuid.UUID,
        domain: str = "esl",
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[KnowledgeNode], int]:
        """分页获取用户知识节点"""
        base_where = [
            KnowledgeNode.user_id == user_id,
            KnowledgeNode.is_deleted == False,
            KnowledgeNode.domain == domain,
        ]

        count_stmt = select(func.count()).select_from(KnowledgeNode).where(*base_where)
        total = (await db.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = (
            select(KnowledgeNode)
            .where(*base_where)
            .order_by(KnowledgeNode.mastery_score.asc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all()), total

    @staticmethod
    async def has_any_nodes(db: AsyncSession, user_id: uuid.UUID) -> bool:
        """检查用户是否有任何知识节点"""
        stmt = (
            select(func.count())
            .select_from(KnowledgeNode)
            .where(KnowledgeNode.user_id == user_id, KnowledgeNode.is_deleted == False)
        )
        result = await db.execute(stmt)
        return result.scalar() > 0

    @staticmethod
    async def find_similar_nodes(
        db: AsyncSession, user_id: uuid.UUID, concepts: List[str], domain: str = "esl"
    ) -> List[KnowledgeNode]:
        """查找与给定 concept 列表重叠的已有节点"""
        if not concepts:
            return []
        stmt = (
            select(KnowledgeNode)
            .where(
                KnowledgeNode.user_id == user_id,
                KnowledgeNode.domain == domain,
                KnowledgeNode.is_deleted == False,
                or_(*[KnowledgeNode.concept_description.ilike(f"%{c}%") for c in concepts]),
            )
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def create_node(
        db: AsyncSession,
        user_id: uuid.UUID,
        concept: str,
        domain: str = "esl",
    ) -> KnowledgeNode:
        """创建单个知识节点"""
        node = KnowledgeNode(
            user_id=user_id,
            domain=domain,
            concept_description=concept,
            mastery_score=0.0,
        )
        db.add(node)
        await db.commit()
        await db.refresh(node)
        return node

    @staticmethod
    async def create_nodes_batch(
        db: AsyncSession, user_id: uuid.UUID, concepts: List[GoalNode]
    ) -> List[KnowledgeNode]:
        """批量创建知识节点"""
        nodes = []
        for c in concepts:
            node = KnowledgeNode(
                user_id=user_id,
                domain=c.domain,
                concept_description=c.concept,
                mastery_score=0.0,
            )
            db.add(node)
            nodes.append(node)
        await db.commit()
        for node in nodes:
            await db.refresh(node)
        return nodes

    @staticmethod
    async def get_by_ids(
        db: AsyncSession, node_ids: List[uuid.UUID], user_id: uuid.UUID
    ) -> List[KnowledgeNode]:
        """根据 ID 列表获取节点"""
        stmt = select(KnowledgeNode).where(
            KnowledgeNode.id.in_(node_ids),
            KnowledgeNode.user_id == user_id,
            KnowledgeNode.is_deleted == False,
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def find_by_concept(
        db: AsyncSession, user_id: uuid.UUID, concept: str, domain: str = "esl"
    ) -> Optional[KnowledgeNode]:
        """根据 concept 模糊查找已有节点"""
        stmt = select(KnowledgeNode).where(
            KnowledgeNode.user_id == user_id,
            KnowledgeNode.domain == domain,
            KnowledgeNode.concept_description.ilike(f"%{concept}%"),
            KnowledgeNode.is_deleted == False,
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def update_mastery_and_fsrs(
        db: AsyncSession, node_id: uuid.UUID, rating: int, mastery_delta: float = 0.0
    ) -> None:
        """
        根据 FSRS 算法更新知识节点的复习计划和掌握度。
        rating: 1=Again, 2=Hard, 3=Good, 4=Easy
        """
        try:
            from fsrs import FSRS, Card, Rating

            fsrs = FSRS()
            stmt = select(KnowledgeNode).where(KnowledgeNode.id == node_id)
            result = await db.execute(stmt)
            node = result.scalars().first()
            if not node:
                return

            # 从数据库状态重建 FSRS Card
            card = Card()
            card.state = node.fsrs_state
            card.stability = node.fsrs_stability
            card.difficulty = node.fsrs_difficulty
            card.reps = node.fsrs_reps
            card.lapses = node.fsrs_lapses

            # 执行 FSRS 调度
            now = datetime.now(timezone.utc)
            rating_enum = Rating(rating)
            scheduling = fsrs.repeat(card, now)
            new_card = scheduling[rating_enum].card

            # 写回数据库
            node.fsrs_state = int(new_card.state)
            node.fsrs_stability = float(new_card.stability)
            node.fsrs_difficulty = float(new_card.difficulty)
            node.fsrs_reps = int(new_card.reps)
            node.fsrs_lapses = int(new_card.lapses)
            node.next_review_at = new_card.due
            node.mastery_score = max(0.0, min(10.0, node.mastery_score + mastery_delta))

            await db.commit()
            logger.info(f"[Sisyphus] FSRS 更新节点 {node_id}: rating={rating}, next_review={new_card.due}")

        except ImportError:
            logger.warning("[Sisyphus] fsrs 库未安装，使用简易调度")
            await KnowledgeNodeService._simple_schedule_update(db, node_id, rating, mastery_delta)
        except Exception as e:
            logger.error(f"[Sisyphus] FSRS 更新失败: {e}")
            await KnowledgeNodeService._simple_schedule_update(db, node_id, rating, mastery_delta)

    @staticmethod
    async def _simple_schedule_update(
        db: AsyncSession, node_id: uuid.UUID, rating: int, mastery_delta: float = 0.0
    ) -> None:
        """FSRS 不可用时的简易备用调度"""
        from datetime import timedelta

        intervals = {1: timedelta(hours=1), 2: timedelta(hours=8), 3: timedelta(days=1), 4: timedelta(days=3)}
        interval = intervals.get(rating, timedelta(days=1))

        stmt = select(KnowledgeNode).where(KnowledgeNode.id == node_id)
        result = await db.execute(stmt)
        node = result.scalars().first()
        if not node:
            return

        node.next_review_at = datetime.now(timezone.utc) + interval
        node.mastery_score = max(0.0, min(10.0, node.mastery_score + mastery_delta))
        node.fsrs_reps += 1
        if rating == 1:
            node.fsrs_lapses += 1
        if node.fsrs_state == 0 and node.fsrs_reps > 0:
            node.fsrs_state = 1
        elif node.fsrs_state == 1 and node.fsrs_reps >= 2:
            node.fsrs_state = 2
        if rating == 1 and node.fsrs_state >= 2:
            node.fsrs_state = 3  # Relearning
        await db.commit()


# ==================== 交互日志 ====================

class InteractionLogService:
    """交互日志 CRUD 服务"""

    @staticmethod
    async def create_log(
        db: AsyncSession,
        session_id: uuid.UUID,
        turn_number: int,
        user_input: str,
        ai_json_response: Optional[str] = None,
        time_taken_ms: Optional[int] = None,
        is_deadlock_triggered: bool = False,
    ) -> InteractionLog:
        log = InteractionLog(
            session_id=session_id,
            turn_number=turn_number,
            user_input=user_input,
            ai_json_response=ai_json_response,
            time_taken_ms=time_taken_ms,
            is_deadlock_triggered=is_deadlock_triggered,
        )
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    @staticmethod
    async def get_recent_logs(
        db: AsyncSession, session_id: uuid.UUID, limit: int = 3
    ) -> List[InteractionLog]:
        """获取最近 N 轮对话日志（按时间正序）"""
        stmt = (
            select(InteractionLog)
            .where(InteractionLog.session_id == session_id)
            .order_by(InteractionLog.turn_number.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        logs = list(result.scalars().all())
        return list(reversed(logs))

    @staticmethod
    async def get_all_logs(
        db: AsyncSession, session_id: uuid.UUID
    ) -> List[InteractionLog]:
        """获取会话的全部对话日志"""
        stmt = (
            select(InteractionLog)
            .where(InteractionLog.session_id == session_id)
            .order_by(InteractionLog.turn_number.asc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_max_turn_number(db: AsyncSession, session_id: uuid.UUID) -> int:
        """获取当前会话的最大轮数"""
        stmt = select(func.max(InteractionLog.turn_number)).where(
            InteractionLog.session_id == session_id
        )
        result = await db.execute(stmt)
        return result.scalar() or 0


# ==================== 学习会话管理 ====================

class LearningSessionService:
    """学习会话 CRUD 服务"""

    @staticmethod
    async def create_session(
        db: AsyncSession,
        user_id: uuid.UUID,
        scenario_description: str,
        scenario_data: Optional[dict] = None,
        target_node_ids: Optional[List[uuid.UUID]] = None,
    ) -> LearningSession:
        session = LearningSession(
            user_id=user_id,
            scenario_description=scenario_description,
            scenario_data=scenario_data,
            target_node_ids=[str(nid) for nid in (target_node_ids or [])],
            status="active",
            node_fail_counts={},
        )
        db.add(session)
        await db.commit()
        await db.refresh(session)
        return session

    @staticmethod
    async def get_session(
        db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> Optional[LearningSession]:
        stmt = select(LearningSession).where(
            LearningSession.id == session_id,
            LearningSession.user_id == user_id,
            LearningSession.is_deleted == False,
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    @staticmethod
    async def complete_session(db: AsyncSession, session_id: uuid.UUID) -> None:
        stmt = (
            update(LearningSession)
            .where(LearningSession.id == session_id)
            .values(status="completed", completed_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def abandon_session(db: AsyncSession, session_id: uuid.UUID) -> None:
        """标记会话为已放弃"""
        stmt = (
            update(LearningSession)
            .where(LearningSession.id == session_id, LearningSession.status == "active")
            .values(status="abandoned", completed_at=datetime.now(timezone.utc))
        )
        await db.execute(stmt)
        await db.commit()

    @staticmethod
    async def list_sessions(
        db: AsyncSession,
        user_id: uuid.UUID,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[LearningSession], int]:
        """分页获取用户会话列表"""
        base_where = [
            LearningSession.user_id == user_id,
            LearningSession.is_deleted == False,
        ]
        if status:
            base_where.append(LearningSession.status == status)

        count_stmt = select(func.count()).select_from(LearningSession).where(*base_where)
        total = (await db.execute(count_stmt)).scalar() or 0

        offset = (page - 1) * page_size
        stmt = (
            select(LearningSession)
            .where(*base_where)
            .order_by(LearningSession.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
        result = await db.execute(stmt)
        sessions = list(result.scalars().all())
        return sessions, total

    @staticmethod
    async def update_fail_counts(
        db: AsyncSession, session_id: uuid.UUID, node_id: str, failed: bool
    ) -> int:
        """更新失败计数。failed=True 递增，failed=False 递减（渐进式重置）"""
        stmt = select(LearningSession).where(LearningSession.id == session_id)
        result = await db.execute(stmt)
        session = result.scalars().first()
        if not session:
            return 0

        counts = session.node_fail_counts or {}
        node_key = str(node_id)
        if failed:
            counts[node_key] = counts.get(node_key, 0) + 1
        else:
            # 渐进式重置：递减而非直接归零
            counts[node_key] = max(0, counts.get(node_key, 0) - 1)
        session.node_fail_counts = counts
        await db.commit()
        return counts.get(node_key, 0)


# ==================== AI Agent 调度 ====================

class ScenarioGeneratorService:
    """场景生成器 Agent"""

    @staticmethod
    async def generate_scenario(
        target_nodes: List[KnowledgeNode], domain: str = "esl"
    ) -> ScenarioData:
        node_descriptions = ", ".join([n.concept_description for n in target_nodes])
        prompt = Template(SCENARIO_GENERATOR_PROMPT).substitute(
            target_nodes=node_descriptions, domain=domain
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请根据上述知识点生成学习场景。"},
        ]

        raw = await engine.generate_chat(messages)
        data = extract_json_from_llm(raw)
        return ScenarioData(**data)


class TutorEngineService:
    """苏格拉底导师 Agent"""

    @staticmethod
    async def get_tutor_response(
        scenario_description: str,
        target_nodes: str,
        user_input: str,
        current_round: int,
        recent_history: str,
        is_deadlock: bool = False,
        full_history: str = "",
        time_taken_ms: Optional[int] = None,
    ) -> TutorResponse:
        if is_deadlock:
            prompt = Template(TUTOR_DEADLOCK_OVERRIDE_PROMPT).substitute(
                scenario_description=scenario_description,
                target_nodes=target_nodes,
                full_history=full_history,
            )
        else:
            time_hint = f"{time_taken_ms}ms" if time_taken_ms else "未知"
            prompt = Template(TUTOR_SYSTEM_PROMPT).substitute(
                scenario_description=scenario_description,
                target_nodes=target_nodes,
                current_round=current_round,
                recent_history=recent_history,
                time_taken_ms_hint=time_hint,
            )

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ]

        raw = await engine.generate_chat(messages)
        data = extract_json_from_llm(raw)
        return TutorResponse(**data)

    @staticmethod
    async def get_celebration_response(
        scenario_description: str, target_nodes: str, user_input: str
    ) -> TutorResponse:
        """用户达标后的庆祝+深度解析"""
        prompt = Template(TUTOR_TARGET_MET_PROMPT).substitute(
            scenario_description=scenario_description,
            target_nodes=target_nodes,
            user_input=user_input,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user_input},
        ]

        raw = await engine.generate_chat(messages)
        data = extract_json_from_llm(raw)
        return TutorResponse(**data)


class MemoryExtractorService:
    """知识提取机 Agent"""

    @staticmethod
    async def extract_knowledge(
        target_nodes: str, conversation_history: str
    ) -> ExtractionResult:
        prompt = Template(MEMORY_EXTRACTOR_PROMPT).substitute(
            target_nodes=target_nodes,
            conversation_history=conversation_history,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请根据上述对话记录提取知识。"},
        ]

        raw = await engine.generate_chat(messages)
        data = extract_json_from_llm(raw)
        return ExtractionResult(**data)


class GoalDeconstructorService:
    """目标解构器 Agent"""

    @staticmethod
    async def deconstruct_goal(goal: str) -> List[GoalNode]:
        prompt = Template(GOAL_DECONSTRUCTOR_PROMPT).substitute(goal=goal)
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请将上述学习目标拆解为知识节点。"},
        ]

        raw = await engine.generate_chat(messages)
        data = extract_json_from_llm(raw)
        nodes = data.get("nodes", [])
        return [GoalNode(**n) for n in nodes]


class ArbiterService:
    """裁判引擎 Agent"""

    @staticmethod
    async def arbitrate(
        target_nodes: str,
        scenario_description: str,
        user_original_input: str,
        challenge_reason: str,
    ) -> dict:
        prompt = Template(ARBITER_PROMPT).substitute(
            target_nodes=target_nodes,
            scenario_description=scenario_description,
            user_original_input=user_original_input,
            challenge_reason=challenge_reason,
        )
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": "请对上述申诉进行独立裁决。"},
        ]

        raw = await engine.generate_chat(messages)
        return extract_json_from_llm(raw)


# ==================== 对话状态管理器 ====================

class DialogueStateManager:
    """
    核心状态机：维护对话上下文、统计失败次数、触发死锁熔断。
    """

    MAX_FAIL_COUNT = 3  # 死锁阈值
    MAX_CONTEXT_TURNS = 3  # 保留最近轮数

    @staticmethod
    def format_recent_history(logs: List[InteractionLog]) -> str:
        """将最近日志格式化为文本"""
        if not logs:
            return "(这是第一轮对话)"
        lines = []
        for log in logs:
            lines.append(f"用户: {log.user_input}")
            if log.ai_json_response:
                try:
                    resp = json.loads(log.ai_json_response)
                    lines.append(f"导师: {resp.get('ai_speech', '(无)')}")
                except json.JSONDecodeError:
                    lines.append(f"导师: {log.ai_json_response}")
        return "\n".join(lines)

    @staticmethod
    def format_full_history(logs: List[InteractionLog]) -> str:
        """将全部日志格式化为文本"""
        lines = []
        for log in logs:
            lines.append(f"第{log.turn_number}轮 - 用户: {log.user_input}")
            if log.ai_json_response:
                try:
                    resp = json.loads(log.ai_json_response)
                    lines.append(f"第{log.turn_number}轮 - 导师: {resp.get('ai_speech', '(无)')}")
                except json.JSONDecodeError:
                    lines.append(f"第{log.turn_number}轮 - 导师: {log.ai_json_response}")
        return "\n".join(lines)

    @staticmethod
    def check_deadlock(node_fail_counts: dict, node_ids: List[str]) -> bool:
        """检查连续失败是否达到死锁阈值（会话级别）"""
        return node_fail_counts.get("__session__", 0) >= DialogueStateManager.MAX_FAIL_COUNT

    @staticmethod
    def get_max_fail_node(node_fail_counts: dict, node_ids: List[str]) -> Optional[str]:
        """获取失败次数最多的节点 ID"""
        max_count = 0
        max_node = None
        for nid in node_ids:
            count = node_fail_counts.get(str(nid), 0)
            if count > max_count:
                max_count = count
                max_node = str(nid)
        return max_node


# ==================== 知识沉淀服务 ====================

class KnowledgeConsolidationService:
    """
    对话结束后的知识沉淀：提取新知识、更新 FSRS 调度。
    通常由 Celery 异步任务调用。
    """

    @staticmethod
    async def consolidate_session(
        db: AsyncSession, session_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """处理一次完整会话的知识沉淀"""
        # 1. 获取会话
        session = await LearningSessionService.get_session(db, session_id, user_id)
        if not session:
            logger.warning(f"[Sisyphus] 沉淀失败: 会话 {session_id} 不存在")
            return

        # 2. 获取全部对话日志
        logs = await InteractionLogService.get_all_logs(db, session_id)
        if not logs:
            logger.warning(f"[Sisyphus] 沉淀失败: 会话 {session_id} 无对话记录")
            return

        # 3. 获取目标节点描述
        target_nodes = await KnowledgeNodeService.get_by_ids(
            db, [uuid.UUID(nid) for nid in (session.target_node_ids or [])], user_id
        )
        target_descriptions = ", ".join([n.concept_description for n in target_nodes])

        # 4. 调用知识提取 Agent
        history_text = DialogueStateManager.format_full_history(logs)
        extraction = await MemoryExtractorService.extract_knowledge(
            target_nodes=target_descriptions,
            conversation_history=history_text,
        )

        # 5. 处理提取结果
        # 构建目标节点 concept 集合，用于步骤 5b 去重
        target_concepts = {n.concept_description.lower().strip() for n in target_nodes}

        # 5a. 创建新知识节点
        for new_node in extraction.new_nodes:
            existing = await KnowledgeNodeService.find_by_concept(
                db, user_id, new_node.concept, new_node.domain
            )
            if not existing:
                await KnowledgeNodeService.create_node(
                    db, user_id, new_node.concept, new_node.domain
                )

        # 5b. 更新已有节点的掌握度和 FSRS（跳过目标节点，由 5c 统一处理）
        for update_info in extraction.existing_node_updates:
            concept = update_info.get("concept", "")
            if concept.lower().strip() in target_concepts:
                continue
            delta = update_info.get("mastery_delta", 0.0)
            existing = await KnowledgeNodeService.find_by_concept(db, user_id, concept)
            if existing:
                rating = 3 if delta >= 0 else 1
                await KnowledgeNodeService.update_mastery_and_fsrs(
                    db, existing.id, rating, delta
                )

        # 5c. 更新目标节点（会话主节点）的 FSRS
        was_deadlock = any(log.is_deadlock_triggered for log in logs)
        for node in target_nodes:
            if was_deadlock:
                await KnowledgeNodeService.update_mastery_and_fsrs(db, node.id, 1, -1.0)
            else:
                await KnowledgeNodeService.update_mastery_and_fsrs(db, node.id, 3, 1.0)

        logger.info(
            f"[Sisyphus] 会话 {session_id} 沉淀完成: "
            f"新节点={len(extraction.new_nodes)}, "
            f"更新节点={len(extraction.existing_node_updates)}"
        )
