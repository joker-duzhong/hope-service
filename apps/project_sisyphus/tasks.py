"""
Project Sisyphus - Celery 异步任务
会话结束后的知识沉淀（调用 Memory Extractor Agent）
"""
import asyncio
import logging
import uuid

from celery import shared_task
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from core.config import settings
from apps.project_sisyphus.services import KnowledgeConsolidationService

logger = logging.getLogger(__name__)

# 独立的异步引擎，避免跨事件循环问题
_consolidation_engine = None
_consolidation_session_maker = None


def _get_session_maker():
    global _consolidation_engine, _consolidation_session_maker
    if _consolidation_session_maker is None:
        _consolidation_engine = create_async_engine(
            settings.DATABASE_URL,
            poolclass=NullPool,
        )
        _consolidation_session_maker = async_sessionmaker(
            _consolidation_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _consolidation_session_maker


async def _consolidate_session_async(session_id: str, user_id: str):
    """异步执行知识沉淀"""
    session_maker = _get_session_maker()
    async with session_maker() as db:
        try:
            await KnowledgeConsolidationService.consolidate_session(
                db, uuid.UUID(session_id), uuid.UUID(user_id)
            )
        except Exception as e:
            logger.error(f"[Sisyphus] 知识沉淀任务失败: {e}", exc_info=True)
            raise


@shared_task(bind=True, max_retries=3)
def consolidate_session_task(self, session_id: str, user_id: str):
    """
    Celery 任务：会话结束后的异步知识沉淀。
    - 提取对话中的新知识和错误模式
    - 更新 FSRS 间隔重复调度
    - 更新知识节点掌握度
    """
    try:
        asyncio.run(_consolidate_session_async(session_id, user_id))
    except Exception as exc:
        logger.error(f"[Sisyphus] 沉淀任务异常，重试中: {exc}")
        raise self.retry(exc=exc, countdown=30)
