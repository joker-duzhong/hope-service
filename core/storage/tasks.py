"""
OSS 资源生命周期管理——异步任务
"""
from celery.utils.log import get_task_logger
from worker.celery_app import celery_app
from core.oss.qiniu_client import QiniuClient

logger = get_task_logger(__name__)

@celery_app.task(name="core.storage.delete_oss_file_task")
def delete_oss_file_task(object_key: str):
    """异步物理删除 OSS 文件内容"""
    logger.info(f"Starting to delete OSS file: {object_key}")
    success = QiniuClient.delete_file_from_oss(object_key)
    if success:
        logger.info(f"Successfully deleted OSS file: {object_key}")
    else:
        logger.error(f"Failed to delete OSS file: {object_key}")
    return success
