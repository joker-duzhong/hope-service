"""
Celery 实例初始化与配置
"""
from celery import Celery
import platform

from core.config import settings

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
    # 自动发现 apps 下所有 tasks.py
    autodiscover_tasks=["apps"],
)

# Register Role before task modules import User and SQLAlchemy configures mappers.
import core.roles.models  # noqa: E402, F401

# 显式导入所有任务模块，确保 Celery 能发现它们
try:
    from apps.trade_copilot import tasks as trade_copilot_tasks
except ImportError:
    pass

try:
    from apps.zaiwen_gaokao import tasks as zaiwen_gaokao_tasks
except ImportError:
    pass

try:
    from apps.nest_talk import tasks as nest_talk_tasks
except ImportError:
    pass

try:
    from apps.shadow_board import tasks as shadow_board_tasks
except ImportError:
    pass

try:
    from apps.aurakey import tasks as aurakey_tasks
except ImportError:
    pass

try:
    from apps.just_right import tasks as just_right_tasks
except ImportError:
    pass

# 导入 Beat 调度表配置，确保 celery_app.conf.beat_schedule 被填充
# 必须在 celery_app 创建之后导入，避免循环引用
import worker.scheduler  # noqa: E402, F401


@celery_app.task
def debug_task():
    """调试任务"""
    return "Celery is working!"
