"""
Celery 定时任务
"""
from celery_worker.celery_app import celery_app


# ==================== 语筑模块任务（预留） ====================

@celery_app.task(name="yuzhu.crawl_beike")
def crawl_beike():
    """
    爬取贝壳数据（预留）
    """
    # TODO: 实现贝壳数据爬取逻辑
    pass


@celery_app.task(name="yuzhu.push_wechat")
def push_wechat(user_id: int, message: str):
    """
    微信公众号推送（预留）
    """
    # TODO: 实现微信推送逻辑
    pass


# ==================== 天启量化模块任务（预留） ====================

@celery_app.task(name="tianqi.fetch_market_data")
def fetch_market_data():
    """
    获取市场数据（预留）
    """
    # TODO: 实现市场数据获取逻辑
    pass


@celery_app.task(name="tianqi.execute_strategy")
def execute_strategy(strategy_id: int):
    """
    执行量化策略（预留）
    """
    # TODO: 实现策略执行逻辑
    pass


# ==================== 定时任务配置 ====================

celery_app.conf.beat_schedule = {
    # 示例：每5分钟执行一次
    # "debug-every-5-minutes": {
    #     "task": "celery_worker.tasks.debug_task",
    #     "schedule": 300.0,
    # },

    # 语筑相关定时任务（预留）
    # "crawl-beike-daily": {
    #     "task": "celery_worker.tasks.crawl_beike",
    #     "schedule": crontab(hour=8, minute=0),  # 每天8点
    # },
}
