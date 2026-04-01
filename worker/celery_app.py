"""
Celery 实例初始化与配置
"""
from celery import Celery

from core.config import settings

celery_app = Celery(
    "hope_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

celery_app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    task_track_started=True,
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    # 自动发现 apps 下所有 tasks.py
    autodiscover_tasks=["apps"],
)


@celery_app.task
def debug_task():
    """调试任务"""
    return "Celery is working!"
