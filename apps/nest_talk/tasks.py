"""
Nest Talk Tasks - 语筑智能房产顾问后台任务
"""
import logging
import asyncio
from datetime import date, datetime, timedelta
from typing import List, Optional, Dict, Any
from uuid import UUID

from celery import shared_task
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from core.database import async_session_maker
from core.config import settings
from apps.nest_talk.models import (
    NestTalkHouse,
    NestTalkRegion,
    NestTalkCommunity,
    NestTalkRegionPriceLog,
    NestTalkUserPreference,
    NestTalkUserMatchHouse,
)
from apps.nest_talk.crawler import BeikeCrawler
from apps.nest_talk.services import HouseMatchService

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
                NestTalkHouse.community_id.is_not(None)
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
    local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

    try:
        async with local_session_maker() as session:
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
        raise
    finally:
        await local_engine.dispose()


# ==================== 爬虫与匹配任务 ====================

@shared_task(
    name="apps.nest_talk.tasks.crawl_and_match_task",
    bind=True,
    max_retries=3,
    default_retry_delay=300
)
def crawl_and_match_task(self):
    """
    主爬虫任务：爬取 → 匹配 → 通知

    流程：
    1. 获取所有活跃用户的偏好区域
    2. 并发爬取这些区域的房源
    3. 对每个房源进行匹配
    4. 保存匹配结果
    5. 推送 WeChat 通知
    """
    try:
        return asyncio.run(_crawl_and_match_async())
    except Exception as e:
        logger.error(f"Crawl and match task failed: {e}")
        raise self.retry(exc=e)


async def _crawl_and_match_async() -> Dict[str, Any]:
    """异步实现"""
    # 创建隔离的引擎
    local_engine = create_async_engine(
        settings.DATABASE_URL,
        poolclass=NullPool
    )
    local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

    try:
        async with local_session_maker() as session:
            # 1. 获取所有活跃用户的偏好区域
            regions = await _get_active_regions(session)
            logger.info(f"Found {len(regions)} active regions to crawl")

            if not regions:
                logger.info("No active regions to crawl")
                return {
                    "crawled": 0,
                    "upserted": 0,
                    "matched": 0,
                    "notified": 0
                }

            # 2. 爬取房源
            houses_data = await BeikeCrawler.crawl_all_regions(regions, max_pages=2)
            logger.info(f"Crawled {len(houses_data)} houses")

            if not houses_data:
                logger.info("No houses crawled")
                return {
                    "crawled": 0,
                    "upserted": 0,
                    "matched": 0,
                    "notified": 0
                }

            # 3. 入库或更新房源
            upserted_count = await _upsert_houses(session, houses_data)
            logger.info(f"Upserted {upserted_count} houses")

            # 4. 匹配 & 保存
            matched_count = await _match_and_save(session, houses_data)
            logger.info(f"Matched {matched_count} user-house pairs")

            # 5. 推送通知
            notified_count = await _notify_users(session)
            logger.info(f"Notified {notified_count} users")

            await session.commit()

            return {
                "crawled": len(houses_data),
                "upserted": upserted_count,
                "matched": matched_count,
                "notified": notified_count
            }
    finally:
        await local_engine.dispose()


async def _get_active_regions(session: AsyncSession) -> List[str]:
    """获取所有活跃用户的偏好区域"""
    stmt = select(NestTalkUserPreference).where(
        NestTalkUserPreference.bargain_enabled == True,
        NestTalkUserPreference.is_deleted == False
    )
    result = await session.execute(stmt)
    prefs = result.scalars().all()

    regions = set()
    for pref in prefs:
        if pref.preferred_regions:
            regions.update(r.strip() for r in pref.preferred_regions.split(","))

    return list(regions)


async def _upsert_houses(session: AsyncSession, houses_data: List[Dict]) -> int:
    """入库或更新房源"""
    count = 0
    for data in houses_data:
        stmt = select(NestTalkHouse).where(
            NestTalkHouse.house_id == data["house_id"]
        )
        result = await session.execute(stmt)
        existing = result.scalar_one_or_none()

        if existing:
            # 更新
            for key, value in data.items():
                if hasattr(existing, key) and key != "id":
                    setattr(existing, key, value)
        else:
            # 创建
            house = NestTalkHouse(**data)
            session.add(house)
            count += 1

    await session.flush()
    return count


async def _match_and_save(session: AsyncSession, houses_data: List[Dict]) -> int:
    """匹配房源到用户偏好并保存"""
    count = 0

    # 先查询入库后的房源
    stmt = select(NestTalkHouse).where(
        NestTalkHouse.house_id.in_([h["house_id"] for h in houses_data])
    )
    result = await session.execute(stmt)
    houses = result.scalars().all()

    for house in houses:
        matches = await HouseMatchService.match_house_to_preferences(session, house)
        count += await HouseMatchService.save_matches(session, house.id, matches)

    await session.flush()
    return count


async def _notify_users(session: AsyncSession) -> int:
    """推送未通知的匹配给用户"""
    from core.users.models import User
    from core.wechat.services import WeChatService

    count = 0

    # 查询未通知的匹配
    stmt = select(NestTalkUserMatchHouse).where(
        NestTalkUserMatchHouse.is_notified == False
    ).limit(100)  # 分批处理

    result = await session.execute(stmt)
    matches = result.scalars().all()

    for match in matches:
        # 获取用户信息
        user_stmt = select(User).where(User.id == match.user_id)
        user_result = await session.execute(user_stmt)
        user = user_result.scalar_one_or_none()

        if not user or not user.openid:
            match.is_notified = True
            continue

        # 获取房源信息
        house_stmt = select(NestTalkHouse).where(NestTalkHouse.id == match.house_id)
        house_result = await session.execute(house_stmt)
        house = house_result.scalar_one_or_none()

        if not house:
            match.is_notified = True
            continue

        # 构建消息
        message = (
            f"🏠 发现新房源！\n"
            f"标题: {house.title}\n"
            f"总价: {house.total_price}万元\n"
            f"单价: {house.unit_price}元/㎡\n"
            f"面积: {house.area}㎡\n"
            f"匹配度: {match.match_score:.0f}%"
        )

        try:
            # 获取第一个 appid
            wechat_apps = settings.WECHAT_APPS.split(",")
            if wechat_apps:
                appid = wechat_apps[0].split(":")[0]
                await WeChatService.send_customer_message(
                    appid=appid,
                    openid=user.openid,
                    content=message
                )
                match.is_notified = True
                match.notified_at = datetime.utcnow()
                count += 1
        except Exception as e:
            logger.error(f"Failed to notify user {user.id}: {e}")

    await session.flush()
    return count
