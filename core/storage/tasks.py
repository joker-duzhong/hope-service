"""
OSS 资源生命周期管理——遗留任务兼容
"""
from celery.utils.log import get_task_logger
from worker.celery_app import celery_app

logger = get_task_logger(__name__)

@celery_app.task(name="core.storage.delete_oss_file_task")
def delete_oss_file_task(object_key: str):
    """兼容队列中的旧消息；软删除策略下不再执行任何物理删除。"""
    logger.info("Skipped legacy OSS deletion task: soft-delete-only policy")
    return False
