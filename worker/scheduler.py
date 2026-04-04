"""
定时任务 (Beat) 时间表配置

所有定时任务的 Cron 表达式必须在此文件集中配置，
严禁分散在各个应用中，方便一键暂停和维护。
"""
from celery.schedules import crontab

from worker.celery_app import celery_app

celery_app.conf.beat_schedule = {
    # ==================== 业务定时任务 ====================
    "trade_copilot_monitor_positions": {
        "task": "apps.trade_copilot.tasks.monitor_positions_task",
        # 交易日(周一到周五)上午 9点到下午 15点，每 5 分钟跑一次
        # 具体的 9:30 - 11:30, 13:00 - 15:00 范围的过滤在 task 用 is_trading_time 处理
        "schedule": crontab(minute="*/5", hour="9-15", day_of_week="1-5"),
    },
    "trade_copilot_daily_settlement": {
        "task": "apps.trade_copilot.tasks.daily_settlement_task",
        # 每天收盘后执行一次 (15:05 分)
        "schedule": crontab(minute=5, hour=15, day_of_week="1-5"),
    },
    "trade_copilot_sniper_radar": {
        "task": "apps.trade_copilot.tasks.sniper_radar_task",
        # 每天尾盘执行两次 (14:50 和 14:55)
        "schedule": crontab(minute="50,55", hour=14, day_of_week="1-5"),
    },
    "trade_copilot_sync_stock_info": {
        "task": "apps.trade_copilot.tasks.sync_stock_info_task",
        # 每天凌晨 1:00 执行一次，同步A股股票基本信息
        "schedule": crontab(minute=0, hour=1),
    },
}
