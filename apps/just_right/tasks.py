"""
JustRight Tasks - 情侣应用后台任务
"""
import logging
import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Any

from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from apps.just_right.models import Couple, Anniversary, CoupleState
from apps.just_right.services import AnniversaryService, NotificationService

logger = logging.getLogger(__name__)


# ==================== 纪念日提醒任务 ====================

@shared_task(name="apps.just_right.tasks.send_anniversary_reminders")
def send_anniversary_reminders():
    """
    纪念日提醒任务（Celery 任务包装）

    每天早上 8:00 检查即将到来的纪念日（3天内）
    发送微信通知提醒
    """
    return asyncio.run(_send_anniversary_reminders_async())


async def _send_anniversary_reminders_async() -> Dict[str, Any]:
    """纪念日提醒任务的异步实现"""
    async with async_session_maker() as session:
        try:
            # 获取所有活跃的情侣关系
            stmt = select(Couple).where(
                Couple.status == "active",
                Couple.is_deleted == False
            )
            result = await session.execute(stmt)
            couples = result.scalars().all()

            notification_count = 0
            today = date.today()
            reminder_days = 3  # 提前3天提醒

            for couple in couples:
                # 获取该情侣的即将到来的纪念日
                upcoming = await AnniversaryService.get_upcoming_anniversaries(
                    session, couple.id, limit=10
                )

                for item in upcoming:
                    ann = item["anniversary"]
                    days_until = item["days_until"]

                    # 只提醒3天内的纪念日
                    if 0 <= days_until <= reminder_days:
                        # 为双方创建通知
                        title = f"📅 纪念日提醒"
                        content = f"距离 {ann.title} 还有 {days_until} 天哦~"

                        # 通知 user1
                        notification1 = await NotificationService.create_notification(
                            session, couple.id, couple.user1_id,
                            type="anniversary_reminder",
                            title=title,
                            content=content,
                            data={"anniversary_id": str(ann.id), "days_until": days_until}
                        )
                        await NotificationService.send_wechat_notification(session, notification1.id)
                        notification_count += 1

                        # 通知 user2
                        if couple.user2_id:
                            notification2 = await NotificationService.create_notification(
                                session, couple.id, couple.user2_id,
                                type="anniversary_reminder",
                                title=title,
                                content=content,
                                data={"anniversary_id": str(ann.id), "days_until": days_until}
                            )
                            await NotificationService.send_wechat_notification(session, notification2.id)
                            notification_count += 1

            logger.info(f"Anniversary reminders sent: {notification_count} notifications")
            return {
                "success": True,
                "couples_checked": len(couples),
                "notifications_sent": notification_count
            }

        except Exception as e:
            logger.error(f"Failed to send anniversary reminders: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }


# ==================== 状态更新通知任务 ====================

@shared_task(name="apps.just_right.tasks.notify_state_updates")
def notify_state_updates():
    """
    状态更新通知任务（Celery 任务包装）

    每10分钟检查一次状态更新
    如果对方更新了心情/留言/举白旗，发送通知
    """
    return asyncio.run(_notify_state_updates_async())


async def _notify_state_updates_async() -> Dict[str, Any]:
    """状态更新通知任务的异步实现"""
    async with async_session_maker() as session:
        try:
            # 查询最近10分钟内更新过的状态
            time_threshold = datetime.now(timezone.utc) - timedelta(minutes=10)

            stmt = select(CoupleState).where(
                CoupleState.updated_at >= time_threshold
            )
            result = await session.execute(stmt)
            states = result.scalars().all()

            notification_count = 0

            for state in states:
                # 检查 user1 的更新，通知 user2
                if state.user2_id:
                    # 检查是否举白旗
                    if state.user1_white_flag and state.user1_white_flag_at and \
                       state.user1_white_flag_at >= time_threshold:
                        notification = await NotificationService.create_notification(
                            session, state.couple_id, state.user2_id,
                            type="state_update",
                            title="🏳️ Ta举白旗了",
                            content="Ta想和好啦，快去看看吧~",
                            data={"type": "white_flag", "from_uid": str(state.user1_id)}
                        )
                        await NotificationService.send_wechat_notification(session, notification.id)
                        notification_count += 1

                    # 检查心情更新
                    elif state.user1_mood and state.updated_at >= time_threshold:
                        notification = await NotificationService.create_notification(
                            session, state.couple_id, state.user2_id,
                            type="state_update",
                            title="💭 Ta更新了心情",
                            content=f"Ta现在的心情是：{state.user1_mood}",
                            data={"type": "mood_update", "from_uid": str(state.user1_id), "mood": state.user1_mood}
                        )
                        await NotificationService.send_wechat_notification(session, notification.id)
                        notification_count += 1

                # 检查 user2 的更新，通知 user1
                if state.user2_id:
                    # 检查是否举白旗
                    if state.user2_white_flag and state.user2_white_flag_at and \
                       state.user2_white_flag_at >= time_threshold:
                        notification = await NotificationService.create_notification(
                            session, state.couple_id, state.user1_id,
                            type="state_update",
                            title="🏳️ Ta举白旗了",
                            content="Ta想和好啦，快去看看吧~",
                            data={"type": "white_flag", "from_uid": str(state.user2_id)}
                        )
                        await NotificationService.send_wechat_notification(session, notification.id)
                        notification_count += 1

                    # 检查心情更新
                    elif state.user2_mood and state.updated_at >= time_threshold:
                        notification = await NotificationService.create_notification(
                            session, state.couple_id, state.user1_id,
                            type="state_update",
                            title="💭 Ta更新了心情",
                            content=f"Ta现在的心情是：{state.user2_mood}",
                            data={"type": "mood_update", "from_uid": str(state.user2_id), "mood": state.user2_mood}
                        )
                        await NotificationService.send_wechat_notification(session, notification.id)
                        notification_count += 1

            logger.info(f"State update notifications sent: {notification_count} notifications")
            return {
                "success": True,
                "states_checked": len(states),
                "notifications_sent": notification_count
            }

        except Exception as e:
            logger.error(f"Failed to send state update notifications: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e)
            }
