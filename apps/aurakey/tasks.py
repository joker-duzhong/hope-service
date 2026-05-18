import asyncio
import base64
import logging
import uuid
from typing import List

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool

from worker.celery_app import celery_app
from apps.aurakey.models import AurakeyTask, AurakeyUserAsset, AurakeyAssetLog
from apps.aurakey.services import AurakeyService
from core.config import settings
from core.llm.engine import generate_stream_image_chat
from core.storage.services import StorageService
from core.users.models import User

logger = logging.getLogger(__name__)

STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS = 600.0

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


def _parse_reference_image_ids(values: list | None) -> List[uuid.UUID]:
    ids: List[uuid.UUID] = []
    for value in values or []:
        try:
            ids.append(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
        except (TypeError, ValueError):
            continue
    return ids


def _get_stream_image_timeout() -> float:
    provider = settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider, {}) if provider else {}
    timeout = config.get("image_timeout")
    if timeout is None:
        return STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS
    try:
        parsed = float(timeout)
    except (TypeError, ValueError):
        logger.warning(
            "[AuraKey] 无效的流式生图 image_timeout 配置 provider=%s value=%r，使用默认 %.0f 秒",
            provider,
            timeout,
            STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS,
        )
        return STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS
    if parsed <= 0:
        logger.warning(
            "[AuraKey] 流式生图 image_timeout 必须大于 0 provider=%s value=%r，使用默认 %.0f 秒",
            provider,
            timeout,
            STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS,
        )
        return STREAM_IMAGE_DEFAULT_TIMEOUT_SECONDS
    return parsed


async def _build_image_data_url(resource) -> str:
    if resource.url.startswith("data:image/"):
        return resource.url

    file_bytes, mime_type, _ = await StorageService._download_remote_file(
        remote_url=resource.url,
        name=resource.name,
        timeout=20.0,
        max_bytes=20 * 1024 * 1024,
    )
    if not mime_type.startswith("image/"):
        raise ValueError(f"参考图资源不是图片类型: {resource.id}")

    encoded = base64.b64encode(file_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


async def _build_stream_image_user_content(db: AsyncSession, task: AurakeyTask):
    prompt = task.prompt + ', 图片比例为:' + task.aspect_ratio
    print('发送给模型:' + prompt)
    content: list[dict] = [{"type": "text", "text": prompt}]
    reference_ids = _parse_reference_image_ids(task.reference_image_ids)
    if not reference_ids:
        return prompt

    resource_map = await StorageService.get_resources_by_ids(db, reference_ids)
    for resource_id in reference_ids:
        resource = resource_map.get(resource_id)
        if resource:
            content.append({"type": "image_url", "image_url": {"url": await _build_image_data_url(resource)}})
    return content if len(content) > 1 else prompt


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
            user_content = await _build_stream_image_user_content(db, task)
            timeout_seconds = _get_stream_image_timeout()
            result = await generate_stream_image_chat(
                messages=[
                    {"role": "system", "content": "你是一个专业的图片生成助手，只返回最终图片结果。"},
                    {"role": "user", "content": user_content},
                ],
                model="gpt-image-2",
                temperature=0.7,
                top_p=1.0,
                timeout=timeout_seconds,
            )
            task.status = "success"
            task.progress = 100
            resource = await StorageService.upload_remote_file(
                db=db,
                remote_url=result["image_url"],
                owner_id=task.user_id,
                scope="hope_aurakey",
            )
            task.image_resource_id = resource.id
            task.image_url = None
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
        except httpx.ReadTimeout as exc:
            timeout_seconds = _get_stream_image_timeout()
            reason = f"上游图片生成超过 {timeout_seconds:g} 秒未返回，请稍后重试"
            logger.error(f"[AuraKey] 流式生图任务超时 task_id={task_id}: {reason}", exc_info=True)
            await _refund_task(db, task, reason)
        except Exception as exc:
            logger.error(f"[AuraKey] 流式生图任务失败 task_id={task_id}: {exc}", exc_info=True)
            await _refund_task(db, task, str(exc))


@celery_app.task(name="aurakey_stream_image_task")
def run_stream_image_task(task_id: str, is_public: bool = False):
    return asyncio.run(_run_stream_image_task_async(task_id, is_public))
