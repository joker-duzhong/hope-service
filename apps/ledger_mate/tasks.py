"""已落库记账请求的独立处理与补投，不依赖客户端连接。"""

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator
from uuid import UUID

from celery import shared_task
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool


logger = logging.getLogger(__name__)
PROCESS_TASK_NAME = "apps.ledger_mate.tasks.process_ai_request"
RECOVER_TASK_NAME = "apps.ledger_mate.tasks.recover_ai_requests"
RETRY_DELAY_SECONDS = 20


def enqueue_ai_request(message_id: UUID | str) -> bool:
    """消息提交后尽力投递；失败的持久请求由 Beat 补投。"""
    message_id = str(UUID(str(message_id)))
    try:
        from worker.celery_app import celery_app

        celery_app.send_task(PROCESS_TASK_NAME, args=[message_id], retry=False)
        return True
    except Exception as exc:
        logger.warning("Ledger request dispatch deferred message=%s error=%s", message_id, type(exc).__name__)
        return False


@asynccontextmanager
async def _worker_session() -> AsyncIterator[AsyncSession]:
    from core.config import settings

    # 每次 asyncio.run 使用独立连接，避免跨进程、跨事件循环复用 API 连接池。
    engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    try:
        maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with maker() as db:
            yield db
    finally:
        await engine.dispose()


async def _process_ai_request(message_id: UUID) -> str:
    from apps.ledger_mate.ai_requests import AiRequestService

    async with _worker_session() as db:
        return await AiRequestService.process(db, message_id)


@shared_task(
    name=PROCESS_TASK_NAME,
    bind=True,
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
    max_retries=3,
)
def process_ai_request(self, message_id: str) -> str:
    try:
        request_id = UUID(message_id)
    except (TypeError, ValueError, AttributeError):
        return "invalid"
    try:
        status = asyncio.run(_process_ai_request(request_id))
    except Exception as exc:
        logger.warning("Ledger request worker retry message=%s error=%s", request_id, type(exc).__name__)
        status = None
    if status is None:
        raise self.retry(exc=RuntimeError("记账任务暂时无法处理"), countdown=RETRY_DELAY_SECONDS)
    if status == "queued":
        try:
            self.apply_async(args=[str(request_id)], countdown=RETRY_DELAY_SECONDS, retry=False)
        except Exception as exc:
            logger.warning("Ledger request retry deferred message=%s error=%s", request_id, type(exc).__name__)
    return status


async def _recover_ai_requests(limit: int) -> list[UUID]:
    from apps.ledger_mate.ai_requests import AiRequestService

    async with _worker_session() as db:
        return await AiRequestService.recover(db, limit=limit)


@shared_task(name=RECOVER_TASK_NAME, ignore_result=True)
def recover_ai_requests(limit: int = 100) -> dict[str, int]:
    """补投漏发请求及处理租约过期的请求，服务层负责原子抢占和重试上限。"""
    try:
        message_ids = asyncio.run(_recover_ai_requests(max(1, min(limit, 1000))))
    except Exception as exc:
        logger.warning("Ledger request recovery deferred error=%s", type(exc).__name__)
        message_ids = None
    if message_ids is None:
        raise RuntimeError("记账任务恢复扫描暂时不可用")
    dispatched = sum(enqueue_ai_request(message_id) for message_id in message_ids)
    return {"pending": len(message_ids), "dispatched": dispatched}
