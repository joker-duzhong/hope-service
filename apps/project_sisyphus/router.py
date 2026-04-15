"""
Project Sisyphus - API 路由定义
"""
import json
import time
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Path as PathParam
from sqlalchemy.ext.asyncio import AsyncSession

from apps.project_sisyphus.models import KnowledgeNode
from apps.project_sisyphus.schemas import (
    ChatRequest,
    ChatResponse,
    TutorResponse,
    ChallengeRequest,
    ChallengeResponse,
    SetGoalRequest,
    SetGoalResponse,
    GoalNode,
    StartSessionResponse,
    KnowledgeNodeRead,
    LearningSessionRead,
    SessionDetailResponse,
    InteractionLogRead,
    SessionStatusFilter,
    ScenarioTheme,
)
from apps.project_sisyphus.services import (
    KnowledgeNodeService,
    InteractionLogService,
    LearningSessionService,
    ScenarioGeneratorService,
    TutorEngineService,
    GoalDeconstructorService,
    ArbiterService,
    DialogueStateManager,
)
from core.database import get_db
from core.users.dependencies import get_current_user
from core.users.models import User
from core.response import ResponseModel, PaginatedResponse, PaginatedData

logger = logging.getLogger(__name__)

router = APIRouter()


# ==================== 设定学习目标 (冷启动) ====================

@router.post("/set-goal", response_model=ResponseModel[SetGoalResponse])
async def set_goal(
    data: SetGoalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    B-5.1 & B-5.2: 设定学习目标，AI 解构为知识节点并存入数据库。
    增加去重检查：如果用户已有相似 concept 的节点，会在 duplicate_warning 中返回。
    """
    goal_nodes = await GoalDeconstructorService.deconstruct_goal(data.goal)

    # 去重检查
    concept_strings = [gn.concept for gn in goal_nodes]
    duplicates = await KnowledgeNodeService.find_similar_nodes(
        db, current_user.id, concept_strings
    )

    saved_nodes = await KnowledgeNodeService.create_nodes_batch(
        db, current_user.id, goal_nodes
    )

    duplicate_warning = None
    if duplicates:
        duplicate_warning = [KnowledgeNodeRead.model_validate(n) for n in duplicates]

    return ResponseModel(
        data=SetGoalResponse(
            goal=data.goal,
            generated_nodes=goal_nodes,
            duplicate_warning=duplicate_warning,
        ),
        message="目标已设定" if not duplicates else "目标已设定，部分知识点与已有节点重复",
    )


# ==================== 开始学习会话 ====================

@router.post("/start-session", response_model=ResponseModel[StartSessionResponse])
async def start_session(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    B-4.1 & B-5.3: 获取当天复习节点 → 场景生成 → 创建会话。
    冷启动逻辑：
      1. 有到期复习节点 → SRS 正常逻辑
      2. 有节点但今天没复习 → 随机挑未掌握节点
      3. 无节点(纯新用户) → 提示先 set-goal
    """
    user_id = current_user.id

    # 优先级 1: 到期复习节点
    nodes = await KnowledgeNodeService.get_nodes_due_for_review(db, user_id)

    # 优先级 2: 未掌握的旧节点
    if not nodes:
        nodes = await KnowledgeNodeService.get_under_mastered_nodes(db, user_id)

    # 优先级 3: 纯新用户，无任何节点
    if not nodes:
        has_any = await KnowledgeNodeService.has_any_nodes(db, user_id)
        if not has_any:
            return ResponseModel(
                data=StartSessionResponse(
                    session_id=None,
                    scenario_description="欢迎来到西西弗斯认知引擎！请先通过 /set-goal 设定你的学习目标，我将为你定制专属学习场景。",
                    ai_initial_speech="Welcome! Before we begin, please tell me your learning goal. What do you want to achieve?",
                    interaction_type="chat",
                ),
                message="请先设定学习目标",
            )

    # 生成场景
    try:
        scenario = await ScenarioGeneratorService.generate_scenario(nodes)
    except Exception as e:
        logger.error(f"[Sisyphus] 场景生成失败: {e}")
        raise HTTPException(status_code=500, detail="场景生成失败，请稍后重试")

    # 创建学习会话
    target_ids = [n.id for n in nodes]
    session = await LearningSessionService.create_session(
        db,
        user_id=user_id,
        scenario_description=scenario.scenario_description,
        scenario_data=scenario.model_dump(),
        target_node_ids=target_ids,
    )

    # 提取视觉主题
    theme = scenario.theme if scenario.theme else None

    return ResponseModel(
        data=StartSessionResponse(
            session_id=session.id,
            scenario_description=scenario.scenario_description,
            scenario_data=scenario.model_dump(),
            target_nodes=[KnowledgeNodeRead.model_validate(n) for n in nodes],
            ai_initial_speech=scenario.scenario_setting,
            interaction_type="chat",
            theme=theme,
        )
    )


# ==================== 核心对话接口 ====================

@router.post("/chat", response_model=ResponseModel[ChatResponse])
async def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    B-4.2: 核心对话循环。
    用户输入 → 状态机评估 → 导师 Agent → 返回结构化 JSON。
    支持：前端思考时间(time_taken_ms)、渐进式死锁、达标庆祝。
    """
    start_time = time.time()
    user_id = current_user.id

    # 1. 获取会话
    session = await LearningSessionService.get_session(db, data.session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="学习会话不存在")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="该学习会话已结束")

    # 2. 获取目标节点
    target_node_ids = session.target_node_ids or []
    target_nodes = await KnowledgeNodeService.get_by_ids(
        db, [uuid.UUID(nid) for nid in target_node_ids], user_id
    )
    target_descriptions = ", ".join([n.concept_description for n in target_nodes])

    # 3. 获取当前轮数和最近对话
    max_turn = await InteractionLogService.get_max_turn_number(db, data.session_id)
    current_round = max_turn + 1
    recent_logs = await InteractionLogService.get_recent_logs(db, data.session_id)

    # 4. 检查死锁
    node_fail_counts = session.node_fail_counts or {}
    is_deadlock = DialogueStateManager.check_deadlock(node_fail_counts, target_node_ids)

    # 5. 调用导师 Agent
    recent_history = DialogueStateManager.format_recent_history(recent_logs)

    if is_deadlock:
        all_logs = await InteractionLogService.get_all_logs(db, data.session_id)
        full_history = DialogueStateManager.format_full_history(all_logs)
        tutor_response = await TutorEngineService.get_tutor_response(
            scenario_description=session.scenario_description,
            target_nodes=target_descriptions,
            user_input=data.user_input,
            current_round=current_round,
            recent_history=recent_history,
            is_deadlock=True,
            full_history=full_history,
        )
    else:
        tutor_response = await TutorEngineService.get_tutor_response(
            scenario_description=session.scenario_description,
            target_nodes=target_descriptions,
            user_input=data.user_input,
            current_round=current_round,
            recent_history=recent_history,
            time_taken_ms=data.time_taken_ms,
        )

    # 6. 记录交互日志（使用前端传入的思考时间）
    client_time_ms = data.time_taken_ms
    await InteractionLogService.create_log(
        db,
        session_id=data.session_id,
        turn_number=current_round,
        user_input=data.user_input,
        ai_json_response=tutor_response.model_dump_json(),
        time_taken_ms=client_time_ms,
        is_deadlock_triggered=is_deadlock,
    )

    # 7. 构建掌握度快照
    mastery_snapshot = {str(n.id): n.mastery_score for n in target_nodes}

    # 8. 处理结果：达标 / 死锁 / 失败递增
    session_completed = False
    deadlock_warning = False
    current_fail_count = node_fail_counts.get("__session__", 0)

    if tutor_response.is_target_met:
        # 用户达标：重置失败计数 + 庆祝 prompt + 完成会话
        await LearningSessionService.update_fail_counts(
            db, data.session_id, "__session__", failed=False
        )

        # 调用庆祝 prompt 生成深度解析
        try:
            celebration = await TutorEngineService.get_celebration_response(
                scenario_description=session.scenario_description,
                target_nodes=target_descriptions,
                user_input=data.user_input,
            )
            tutor_response = celebration
        except Exception as e:
            logger.warning(f"[Sisyphus] 庆祝 prompt 调用失败，使用原始响应: {e}")

        session_completed = True
        await LearningSessionService.complete_session(db, data.session_id)
        # 触发异步知识沉淀任务
        _trigger_consolidation(data.session_id, user_id)
    elif current_fail_count >= 2 and not is_deadlock:
        # 接近死锁：标记警告，让前端提示用户
        deadlock_warning = True
        await LearningSessionService.update_fail_counts(
            db, data.session_id, "__session__", failed=True
        )
    elif not is_deadlock:
        # 普通失败：递增计数
        await LearningSessionService.update_fail_counts(
            db, data.session_id, "__session__", failed=True
        )

    return ResponseModel(
        data=ChatResponse(
            session_id=data.session_id,
            turn_number=current_round,
            is_deadlock_triggered=is_deadlock,
            deadlock_warning=deadlock_warning,
            session_completed=session_completed,
            tutor=tutor_response,
            mastery_snapshot=mastery_snapshot,
        )
    )


# ==================== 挑战接口 (我不服) ====================

@router.post("/challenge", response_model=ResponseModel[ChallengeResponse])
async def challenge(
    data: ChallengeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    B-4.3: 用户对 AI 判决提出异议，调用独立裁判重新裁决。
    修复：检查会话状态、从日志读取 original_verdict。
    """
    user_id = current_user.id

    # 获取会话（检查状态）
    session = await LearningSessionService.get_session(db, data.session_id, user_id)
    if not session:
        raise HTTPException(status_code=404, detail="学习会话不存在")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="只能对进行中的会话发起挑战")

    # 获取最近一轮日志
    recent_logs = await InteractionLogService.get_recent_logs(db, data.session_id, limit=1)
    if not recent_logs:
        raise HTTPException(status_code=400, detail="没有可申诉的对话记录")

    last_log = recent_logs[-1]

    # 从日志中读取原始判断
    original_verdict = False
    if last_log.ai_json_response:
        try:
            last_resp = json.loads(last_log.ai_json_response)
            original_verdict = last_resp.get("is_target_met", False)
        except json.JSONDecodeError:
            pass

    # 获取目标节点
    target_node_ids = session.target_node_ids or []
    target_nodes = await KnowledgeNodeService.get_by_ids(
        db, [uuid.UUID(nid) for nid in target_node_ids], user_id
    )
    target_descriptions = ", ".join([n.concept_description for n in target_nodes])

    # 调用裁判引擎
    verdict = await ArbiterService.arbitrate(
        target_nodes=target_descriptions,
        scenario_description=session.scenario_description,
        user_original_input=last_log.user_input,
        challenge_reason=data.challenge_reason,
    )

    # 如果裁判改判，更新会话状态
    new_verdict = verdict.get("new_verdict", False)
    next_action = "start_new" if new_verdict else "continue"

    if new_verdict:
        await LearningSessionService.complete_session(db, data.session_id)
        _trigger_consolidation(data.session_id, user_id)

    return ResponseModel(
        data=ChallengeResponse(
            session_id=data.session_id,
            original_verdict=original_verdict,
            new_verdict=new_verdict,
            arbiter_explanation=verdict.get("arbiter_explanation", ""),
            next_action=next_action,
        )
    )


# ==================== 会话管理 (多会话 CRUD) ====================

@router.get("/sessions", response_model=PaginatedResponse[LearningSessionRead])
async def list_sessions(
    status: Optional[SessionStatusFilter] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页获取用户的学习会话列表，支持按状态筛选"""
    sessions, total = await LearningSessionService.list_sessions(
        db, current_user.id, status=status, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(
            items=[LearningSessionRead.model_validate(s) for s in sessions],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    )


@router.get("/active-sessions", response_model=ResponseModel[list[LearningSessionRead]])
async def list_active_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """快捷获取用户所有活跃会话"""
    sessions, _ = await LearningSessionService.list_sessions(
        db, current_user.id, status="active", page=1, page_size=50
    )
    return ResponseModel(data=[LearningSessionRead.model_validate(s) for s in sessions])


@router.get("/sessions/{session_id}", response_model=ResponseModel[SessionDetailResponse])
async def get_session_detail(
    session_id: uuid.UUID = PathParam(..., description="会话ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取会话详情（含交互日志和目标节点）"""
    session = await LearningSessionService.get_session(db, session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="学习会话不存在")

    logs = await InteractionLogService.get_all_logs(db, session_id)

    target_node_ids = session.target_node_ids or []
    target_nodes = await KnowledgeNodeService.get_by_ids(
        db, [uuid.UUID(nid) for nid in target_node_ids], current_user.id
    )

    return ResponseModel(
        data=SessionDetailResponse(
            **LearningSessionRead.model_validate(session).model_dump(),
            interaction_logs=[InteractionLogRead.model_validate(l) for l in logs],
            target_nodes=[KnowledgeNodeRead.model_validate(n) for n in target_nodes],
        )
    )


@router.post("/sessions/{session_id}/abandon", response_model=ResponseModel[LearningSessionRead])
async def abandon_session(
    session_id: uuid.UUID = PathParam(..., description="会话ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """放弃一个活跃的学习会话"""
    session = await LearningSessionService.get_session(db, session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="学习会话不存在")
    if session.status != "active":
        raise HTTPException(status_code=400, detail="只能放弃进行中的会话")

    await LearningSessionService.abandon_session(db, session_id)
    session = await LearningSessionService.get_session(db, session_id, current_user.id)
    return ResponseModel(
        data=LearningSessionRead.model_validate(session),
        message="会话已放弃",
    )


# ==================== 知识节点查询 ====================

@router.get("/nodes", response_model=PaginatedResponse[KnowledgeNodeRead])
async def list_nodes(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分页查看当前用户的知识节点"""
    nodes, total = await KnowledgeNodeService.get_all_nodes_paginated(
        db, current_user.id, page=page, page_size=page_size
    )
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(
            items=[KnowledgeNodeRead.model_validate(n) for n in nodes],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    )


# ==================== 辅助函数 ====================

def _trigger_consolidation(session_id: uuid.UUID, user_id: uuid.UUID) -> None:
    """触发异步知识沉淀任务"""
    try:
        from apps.project_sisyphus.tasks import consolidate_session_task
        consolidate_session_task.delay(str(session_id), str(user_id))
    except Exception as e:
        logger.error(f"[Sisyphus] 触发知识沉淀任务失败: {e}")
