"""
定时任务 (Beat) 时间表配置

所有定时任务的 Cron 表达式必须在此文件集中配置，
严禁分散在各个应用中，方便一键暂停和维护。
"""
from celery.schedules import crontab

from worker.celery_app import celery_app

celery_app.conf.beat_schedule = {
    # ==================== 示例 ====================
    # "debug-every-5-minutes": {
    #     "task": "worker.celery_app.debug_task",
    #     "schedule": 300.0,
    # },
    #
    # ==================== 业务定时任务 ====================
    # 添加新应用定时任务时，在此处集中配置：
    #
    # "app-name-task-name": {
    #     "task": "apps.app_name.tasks.task_function",
    #     "schedule": crontab(hour=8, minute=0),
    # },
}
