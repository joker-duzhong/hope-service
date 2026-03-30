"""
Celery 应用配置
"""
from celery import Celery

from app.core.config import settings

# 创建 Celery 应用
celery_app = Celery(
    "hope_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["celery_worker.tasks"],
)

# Celery 配置
celery_app.conf.update(
    # 时区设置
    timezone="Asia/Shanghai",
    enable_utc=True,

    # 任务序列化
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # 结果过期时间
    result_expires=3600,

    # 任务结果
    task_track_started=True,

    # 并发设置
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
)


@celery_app.task
def debug_task():
    """调试任务"""
    return "Celery is working!"
