import asyncio
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from worker.celery_app import celery_app
from apps.aurakey.models import AurakeyTask, AurakeyUserAsset, AurakeyAssetLog
from apps.aurakey.services import AurakeyService
from core.config import settings
from core.llm.engine import generate_stream_image_chat
from core.users.models import User

logger = logging.getLogger(__name__)

_stream_engine = None
_stream_session_maker = None


def _get_session_maker():
    global _stream_engine, _stream_session_maker
    if _stream_session_maker is None:
        _stream_engine = create_async_engine(
            settings.DATABASE_URL,
            poolclass=NullPool,
        )
        _stream_session_maker = async_sessionmaker(
            _stream_engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _stream_session_maker


async def _refund_task(db: AsyncSession, task: AurakeyTask, reason: str):
    asset = await db.scalar(select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == task.user_id))
    if asset and task.frozen_points > 0:
        refund_amount = await AurakeyService._restore_points(
            db,
            asset,
            task.point_deductions or [],
            task.frozen_points,
            description=reason,
        )
        db.add(
            AurakeyAssetLog(
                user_id=task.user_id,
                type=3,
                amount=refund_amount,
                balance_after=asset.balance,
                description=reason,
            )
        )
        task.point_deductions = []
        task.frozen_points = 0
    task.status = "failed"
    task.failed_reason = reason
    task.progress = 100
    await db.commit()


async def _run_stream_image_task_async(task_id: str, is_public: bool = False):
    session_maker = _get_session_maker()
    task_uuid = uuid.UUID(task_id)

    async with session_maker() as db:
        task = await db.get(AurakeyTask, task_uuid)
        if not task or task.is_deleted:
            return
        if task.status not in {"pending", "processing"}:
            return

        try:
            result = await generate_stream_image_chat(
                messages=[
                    {"role": "system", "content": "你是一个专业的图片生成助手，只返回最终图片结果。"},
                    {"role": "user", "content": task.prompt},
                ],
                model="gpt-image-2",
                temperature=0.7,
                top_p=1.0,
                timeout=180,
            )
            task.status = "success"
            task.progress = 100
            task.image_url = result["image_url"]
            task.failed_reason = None
            task.remote_task_id = None
            task.frozen_points = 0
            if is_public and not task.is_published:
                user = await db.get(User, task.user_id)
                await AurakeyService.publish_task_to_gallery(
                    db,
                    task,
                    (user.nickname or user.username) if user else None,
                    user.avatar if user else None,
                )
            await db.commit()
        except Exception as exc:
            logger.error(f"[AuraKey] 流式生图任务失败 task_id={task_id}: {exc}", exc_info=True)
            await _refund_task(db, task, str(exc))


@celery_app.task(name="aurakey_stream_image_task")
def run_stream_image_task(task_id: str, is_public: bool = False):
    return asyncio.run(_run_stream_image_task_async(task_id, is_public))
