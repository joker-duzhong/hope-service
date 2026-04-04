"""
Nest Talk Tasks - 语筑智能房产顾问后台任务
"""
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any

from celery import shared_task
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import async_session_maker
from apps.nest_talk.models import (
    NestTalkHouse,
    NestTalkRegion,
    NestTalkCommunity,
    NestTalkRegionPriceLog,
    NestTalkUserPreference,
)

logger = logging.getLogger(__name__)


# ==================== 捡漏检测任务 ====================

@shared_task(name="apps.nest_talk.tasks.detect_bargain_task")
def detect_bargain_task():
    """
    捡漏检测任务（Celery 任务包装）

    计算小区均价，判断房源是否低于均价，更新捡漏标记。
    每日早上 6:00 由 Celery Beat 触发。
    """
    import asyncio
    return asyncio.run(_detect_bargain_houses_async())


async def _detect_bargain_houses_async() -> Dict[str, Any]:
    """捡漏检测任务的异步实现"""
    async with async_session_maker() as session:
        try:
            # 1. 计算每个小区的均价
            stmt = select(
                NestTalkHouse.community_id,
                NestTalkHouse.community_name,
                func.avg(NestTalkHouse.unit_price).label("avg_price")
            ).where(
                NestTalkHouse.is_deleted == False,
                NestTalkHouse.status == "active",
                NestTalkHouse.community_id != None
            ).group_by(NestTalkHouse.community_id, NestTalkHouse.community_name)

            result = await session.execute(stmt)
            community_prices = {
                row.community_id: {"avg_price": row.avg_price, "name": row.community_name}
                for row in result.all()
            }

            # 2. 获取所有在售房源
            stmt = select(NestTalkHouse).where(
                NestTalkHouse.is_deleted == False,
                NestTalkHouse.status == "active"
            )
            result = await session.execute(stmt)
            houses = result.scalars().all()

            # 默认捡漏阈值（0.9 即 90%）
            DEFAULT_THRESHOLD = 0.9

            updated_count = 0
            for house in houses:
                if house.community_id and house.community_id in community_prices:
                    avg_price = community_prices[house.community_id]["avg_price"]
                    house.community_avg_price = avg_price

                    # 计算折扣率
                    discount_rate = house.unit_price / avg_price
                    house.discount_rate = discount_rate

                    # 判断是否为捡漏房（低于均价 10% 或更多）
                    if discount_rate <= DEFAULT_THRESHOLD:
                        house.is_bargain = True
                        house.bargain_reason = (
                            f"单价 {house.unit_price:.0f} 元/㎡ 低于小区均价 "
                            f"{avg_price:.0f} 元/㎡，折扣率 {discount_rate*100:.1f}%"
                        )
                        updated_count += 1
                    else:
                        house.is_bargain = False
                        house.bargain_reason = None
                else:
                    # 没有小区信息的房源，重置捡漏状态
                    house.is_bargain = False
                    house.bargain_reason = None
                    house.community_avg_price = None
                    house.discount_rate = None

            await session.commit()
            logger.info(f"捡漏检测完成，共更新 {updated_count} 套捡漏房源")
            return {"updated_count": updated_count, "total_checked": len(houses)}

        except Exception as e:
            logger.error(f"捡漏检测任务失败: {e}")
            await session.rollback()
            raise


# ==================== 用户捡漏推送 ====================

async def get_bargain_houses_for_user(user_id: int) -> List[Dict[str, Any]]:
    """
    根据用户偏好获取捡漏房源

    Args:
        user_id: 用户ID

    Returns:
        符合用户偏好的捡漏房源列表
    """
    async with async_session_maker() as session:
        # 获取用户偏好
        stmt = select(NestTalkUserPreference).where(
            NestTalkUserPreference.user_id == user_id,
            NestTalkUserPreference.is_deleted == False
        )
        result = await session.execute(stmt)
        preference = result.scalars().first()

        # 构建查询
        stmt = select(NestTalkHouse).where(
            NestTalkHouse.is_deleted == False,
            NestTalkHouse.status == "active",
            NestTalkHouse.is_bargain == True
        )

        if preference:
            # 应用用户偏好筛选
            if preference.budget_min:
                stmt = stmt.where(NestTalkHouse.total_price >= preference.budget_min)
            if preference.budget_max:
                stmt = stmt.where(NestTalkHouse.total_price <= preference.budget_max)
            if preference.area_min:
                stmt = stmt.where(NestTalkHouse.area >= preference.area_min)
            if preference.area_max:
                stmt = stmt.where(NestTalkHouse.area <= preference.area_max)
            if preference.rooms_min:
                stmt = stmt.where(NestTalkHouse.rooms >= preference.rooms_min)
            if preference.rooms_max:
                stmt = stmt.where(NestTalkHouse.rooms <= preference.rooms_max)
            if preference.preferred_regions:
                regions = [r.strip() for r in preference.preferred_regions.split(",")]
                stmt = stmt.where(NestTalkHouse.region_name.in_(regions))

            # 捡漏阈值筛选
            if preference.bargain_threshold:
                stmt = stmt.where(NestTalkHouse.discount_rate <= preference.bargain_threshold)

        stmt = stmt.order_by(NestTalkHouse.discount_rate.asc()).limit(20)

        result = await session.execute(stmt)
        houses = result.scalars().all()

        return [
            {
                "id": h.id,
                "house_id": h.house_id,
                "title": h.title,
                "total_price": h.total_price,
                "unit_price": h.unit_price,
                "area": h.area,
                "layout": h.layout,
                "rooms": h.rooms,
                "region_name": h.region_name,
                "community_name": h.community_name,
                "floor": h.floor,
                "total_floors": h.total_floors,
                "orientation": h.orientation,
                "decoration": h.decoration,
                "discount_rate": h.discount_rate,
                "bargain_reason": h.bargain_reason,
                "url": h.url,
                "image_url": h.image_url,
            }
            for h in houses
        ]


# ==================== 区域均价日志更新任务 ====================

@shared_task(name="apps.nest_talk.tasks.update_region_prices_task")
def update_region_prices_task():
    """
    更新区域均价日志（Celery 任务包装）

    计算每个区域的当日均价并记录到日志表。
    每日早上 6:30 由 Celery Beat 触发。
    """
    import asyncio
    return asyncio.run(_update_region_price_logs_async())


async def _update_region_price_logs_async() -> Dict[str, Any]:
    """更新区域均价日志的异步实现"""
    async with async_session_maker() as session:
        try:
            today = date.today()

            # 获取所有活跃区域
            stmt = select(NestTalkRegion).where(
                NestTalkRegion.is_deleted == False,
                NestTalkRegion.is_active == True
            )
            result = await session.execute(stmt)
            regions = result.scalars().all()

            updated_regions = 0
            for region in regions:
                # 计算该区域今日均价
                stmt = select(func.avg(NestTalkHouse.unit_price)).where(
                    NestTalkHouse.is_deleted == False,
                    NestTalkHouse.status == "active",
                    NestTalkHouse.region_name == region.name
                )
                result = await session.execute(stmt)
                avg_price = result.scalar()

                if avg_price:
                    # 获取昨日的均价用于计算涨跌幅
                    yesterday = today - timedelta(days=1)
                    stmt = select(NestTalkRegionPriceLog).where(
                        NestTalkRegionPriceLog.region_id == region.id,
                        NestTalkRegionPriceLog.record_date == yesterday
                    )
                    result = await session.execute(stmt)
                    yesterday_log = result.scalars().first()

                    change_rate = None
                    if yesterday_log and yesterday_log.average_price > 0:
                        change_rate = (avg_price - yesterday_log.average_price) / yesterday_log.average_price

                    # 检查今日记录是否已存在
                    stmt = select(NestTalkRegionPriceLog).where(
                        NestTalkRegionPriceLog.region_id == region.id,
                        NestTalkRegionPriceLog.record_date == today
                    )
                    result = await session.execute(stmt)
                    existing_log = result.scalars().first()

                    if existing_log:
                        # 更新现有记录
                        existing_log.average_price = avg_price
                        existing_log.change_rate = change_rate
                    else:
                        # 创建今日记录
                        log = NestTalkRegionPriceLog(
                            region_id=region.id,
                            region_name=region.name,
                            record_date=today,
                            average_price=avg_price,
                            change_rate=change_rate
                        )
                        session.add(log)

                    updated_regions += 1

            await session.commit()
            logger.info(f"区域均价日志更新完成，共 {updated_regions} 个区域")
            return {"updated_regions": updated_regions, "date": str(today)}

        except Exception as e:
            logger.error(f"更新区域均价日志失败: {e}")
            await session.rollback()
            raise
