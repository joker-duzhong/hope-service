"""
Celery 实例初始化与配置
"""
from importlib import import_module
import platform

from celery import Celery

from core.apps_config import REGISTERED_APPS
from core.config import settings

settings.validate_runtime_security()

celery_app = Celery(
    "hope_service",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
)

# Windows 不支持 prefork，使用 solo 或 threads
pool_type = "solo" if platform.system() == "Windows" else "prefork"

celery_app.conf.update(
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    result_expires=3600,
    task_track_started=True,
    worker_pool=pool_type,
    worker_prefetch_multiplier=1,
    worker_concurrency=1 if pool_type == "solo" else 4,
)

# Register Role before task modules import User and SQLAlchemy configures mappers.
import core.roles.models  # noqa: E402, F401

CORE_TASK_MODULES = ("core.storage.tasks",)


def register_app_tasks() -> None:
    """Import core tasks and task modules for active applications."""
    for task_module in CORE_TASK_MODULES:
        import_module(task_module)

    for app_config in REGISTERED_APPS.values():
        if not app_config.is_active:
            continue
        for task_module in app_config.task_modules:
            import_module(task_module)


register_app_tasks()

# 导入 Beat 调度表配置，确保 celery_app.conf.beat_schedule 被填充
# 必须在 celery_app 创建之后导入，避免循环引用
import worker.scheduler  # noqa: E402, F401


@celery_app.task
def debug_task():
    """调试任务"""
    return "Celery is working!"
