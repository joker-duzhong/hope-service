import json
import uuid
import asyncio
import logging
import redis.asyncio as redis
from fastapi import APIRouter, Depends, Query, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel
from core.users.dependencies import get_current_user
from core.users.models import User
from core.config import settings

from apps.shadow_board.schemas import (
    SendMessageRequest, MessageResponse, SessionStatusResponse, 
    ChatInitResponse, ChatHistoryResponse, SessionHistoryResponse
)
from apps.shadow_board.services import ShadowBoardService
from apps.shadow_board.tasks import run_agent_loop

router = APIRouter()
logger = logging.getLogger(__name__)

# Redis Pub/Sub URL
REDIS_URL = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"

@router.post("/chat", response_model=ResponseModel[ChatInitResponse])
async def chat(
    request: SendMessageRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    接收用户消息，启动/恢复会话辩论
    """
    session_id = request.session_id
    
    if not session_id:
        # 创建新会话
        topic = request.topic or request.text[:50]
        session = await ShadowBoardService.create_session(db, current_user.id, topic)
        session_id = session.id
    else:
        session = await ShadowBoardService.get_session(db, session_id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        if session.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权操作此会话")
        if session.status in ["scoring", "speaking"]:
            raise HTTPException(status_code=400, detail="董事会成员正在激烈讨论中，请等待结束后再发言")
    
    # 1. 保存 CEO 消息
    await ShadowBoardService.save_message(db, session_id, "CEO", request.text)
    
    # 2. 更新状态并启动 Celery 任务
    await ShadowBoardService.update_session_status(db, session_id, "scoring")
    
    # 异步触发 Celery 任务
    run_agent_loop.delay(str(session_id))
    
    return ResponseModel(data=ChatInitResponse(
        session_id=session_id,
        status="scoring",
        message="消息已送达董事会，请稍候。"
    ))

@router.get("/history", response_model=ResponseModel[SessionHistoryResponse])
async def get_history(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取会话列表"""
    sessions = await ShadowBoardService.get_user_sessions(db, current_user.id)
    return ResponseModel(data=SessionHistoryResponse(
        sessions=[SessionStatusResponse.from_orm(s) for s in sessions]
    ))

@router.get("/messages", response_model=ResponseModel[ChatHistoryResponse])
async def get_messages(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取具体会话的历史消息"""
    session = await ShadowBoardService.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
         raise HTTPException(status_code=403, detail="无权操作此会话")
         
    messages = await ShadowBoardService.get_chat_history(db, session_id)
    return ResponseModel(data=ChatHistoryResponse(
        messages=[MessageResponse.from_orm(m) for m in messages]
    ))

@router.get("/status", response_model=ResponseModel[SessionStatusResponse])
async def get_status(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """获取会话当前状态"""
    session = await ShadowBoardService.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
         raise HTTPException(status_code=403, detail="无权操作此会话")
    return ResponseModel(data=SessionStatusResponse.from_orm(session))

@router.post("/retry", response_model=ResponseModel[ChatInitResponse])
async def retry_session(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    重试失败或卡住的会话任务
    """
    session = await ShadowBoardService.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权操作此会话")

    # 如果会话已完成，不允许重试
    if session.status == "done":
        raise HTTPException(status_code=400, detail="会话已完成，无需重试")

    # 重置状态为 scoring 并重新启动任务
    await ShadowBoardService.update_session_status(db, session_id, "scoring")
    run_agent_loop.delay(str(session_id))

    return ResponseModel(data=ChatInitResponse(
        session_id=session_id,
        status="scoring",
        message="任务已重新启动"
    ))

@router.get("/stream")
async def stream(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    SSE 流式输出，监听 Redis Pub/Sub (验证 Token)
    """
    # 验证会话归属权限
    session = await ShadowBoardService.get_session(db, session_id)
    if not session or session.user_id != current_user.id:
         raise HTTPException(status_code=403, detail="无权操作此会话")

    async def event_generator():
        r = redis.from_url(REDIS_URL)
        pubsub = r.pubsub()
        await pubsub.subscribe(f"shadow_board_exec_stream_{session_id}")

        # 超时配置：30秒没有消息则认为任务失败
        timeout = 30
        last_message_time = asyncio.get_event_loop().time()

        try:
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True)
                if message:
                    data = message['data'].decode('utf-8')
                    yield f"data: {data}\n\n"
                    last_message_time = asyncio.get_event_loop().time()

                    # 如果收到状态为 done 或 paused，关闭流
                    decoded_data = json.loads(data)
                    if decoded_data.get("status") in ["done", "paused"]:
                        break

                # 检查超时
                current_time = asyncio.get_event_loop().time()
                if current_time - last_message_time > timeout:
                    # 检查会话状态，如果还在 scoring/speaking 说明任务卡住了
                    async with get_db() as check_db:
                        current_session = await ShadowBoardService.get_session(check_db, session_id)
                        if current_session and current_session.status in ["scoring", "speaking"]:
                            # 任务超时，更新状态为 idle 并发送错误
                            await ShadowBoardService.update_session_status(check_db, session_id, "idle")
                            error_msg = json.dumps({
                                "session_id": str(session_id),
                                "role": "System",
                                "status": "error",
                                "error": "任务执行超时，请重试"
                            })
                            yield f"data: {error_msg}\n\n"
                            break

                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"SSE 订阅异常: {e}")
            error_msg = json.dumps({
                "session_id": str(session_id),
                "role": "System",
                "status": "error",
                "error": str(e)
            })
            yield f"data: {error_msg}\n\n"
        finally:
            await pubsub.unsubscribe(f"shadow_board_exec_stream_{session_id}")
            await r.close()

    return StreamingResponse(event_generator(), media_type="text/event-stream")
