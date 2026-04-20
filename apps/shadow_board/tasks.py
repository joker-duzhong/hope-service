import json
import asyncio
import logging
import uuid
import redis.asyncio as redis

from worker.celery_app import celery_app
from apps.shadow_board.services import ShadowBoardService
from core.database import async_session_maker
from core.config import settings

logger = logging.getLogger(__name__)

def async_to_sync(awaitable):
    """简单的 async 到 sync 包装器，替代 asgiref"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # 没有运行中的 loop，创建新的
        return asyncio.run(awaitable)
    else:
        # 已有 loop，使用 run_until_complete
        return loop.run_until_complete(awaitable)

# 使用 Redis Pub/Sub 广播流式内容
REDIS_URL = f"redis://{settings.REDIS_HOST}:{settings.REDIS_PORT}/0"

async def broadcast_chunk(session_id: str, role: str, chunk: str = None, status: str = None):
    """广播消息到 Redis"""
    r = redis.from_url(REDIS_URL)
    message = {
        "session_id": session_id,
        "role": role
    }
    if chunk:
        message["chunk"] = chunk
    if status:
        message["status"] = status
        
    await r.publish(f"shadow_board_exec_stream_{session_id}", json.dumps(message))
    await r.close()

@celery_app.task(name="shadow_board_agent_loop")
def run_agent_loop(session_id: str):
    """
    影子董事会核心辩论循环。
    由于 Celery 默认是同步执行环境（虽然可以配置异步，但通常在 Worker 内阻塞运行），
    我们在这里执行异步包装逻辑。
    """
    return async_to_sync(_run_agent_loop_async(session_id))

async def _run_agent_loop_async(session_id: str):
    session_uuid = uuid.UUID(session_id)
    
    async with async_session_maker() as db:
        session = await ShadowBoardService.get_session(db, session_uuid)
        if not session or session.status in ["done", "paused"]:
            return

        # 开始循环
        while session.current_turn < session.max_turns:
            # 1. 更新状态为打分中
            await ShadowBoardService.update_session_status(db, session_uuid, "scoring")
            await broadcast_chunk(session_id, "System", status="scoring")
            
            # 2. 获取历史记录并打分
            chat_history = await ShadowBoardService.get_chat_history(db, session_uuid)
            evaluation = await ShadowBoardService.evaluate_motivation(chat_history)
            winner = evaluation.get("winner", "None")
            
            # 3. 判断是否有人说话
            if winner == "None" or winner not in ["PM", "Architect", "Designer", "QA"]:
                await ShadowBoardService.update_session_status(db, session_uuid, "done")
                await broadcast_chunk(session_id, "System", status="done")
                logger.info(f"会话 {session_id} 已达成共识并结束。")
                break
            
            # 4. 指定角色开始说话
            await ShadowBoardService.update_session_status(db, session_uuid, "speaking")
            await broadcast_chunk(session_id, winner, status="speaking")
            
            full_content = ""
            async for chunk in ShadowBoardService.generate_role_reply_stream(winner, chat_history):
                full_content += chunk
                # 实时推送
                await broadcast_chunk(session_id, winner, chunk=chunk)
            
            # 5. 保存结果并更新轮次
            await ShadowBoardService.save_message(
                db, session_uuid, winner, full_content, 
                is_finalized=True, 
                meta_data={"turn": session.current_turn, "scores": evaluation.get("scores")}
            )
            
            # 增加轮次计数
            session.current_turn += 1
            await ShadowBoardService.update_session_status(db, session_uuid, "idle", session.current_turn)
            
            # 每次发言后广播一次状态刷新
            await broadcast_chunk(session_id, winner, status="idle")

        # 如果到达最大轮次
        if session.current_turn >= session.max_turns:
            await ShadowBoardService.update_session_status(db, session_uuid, "paused")
            await broadcast_chunk(session_id, "System", status="paused")
            logger.info(f"会话 {session_id} 到达最大轮次，已暂停。")
