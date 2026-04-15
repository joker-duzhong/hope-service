import logging
from typing import List, Optional
from uuid import UUID
import json
from datetime import datetime
import httpx
import redis.asyncio as aioredis
import pandas as pd
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.redis_client import redis_client
from core.exceptions import AppException
from apps.trade_copilot.models import Position, Watchlist, TradeStrategy, TradingJournal, UserTradeSettings, TradeTransaction, StockInfo
from apps.trade_copilot.schemas import (
    PositionCreate, PositionUpdate, MarketStatusOut, STListOut,
    MarketThermometerOut, SectorItemOut,
    WatchlistCreate, WatchlistUpdate, TradeStrategyCreate, TradeStrategyUpdate, TradingJournalCreate, TradingJournalUpdate, UserTradeSettingsUpdate, TradeTransactionCreate
)
from apps.trade_copilot.akshare_client import AkShareClient

logger = logging.getLogger(__name__)

async def send_feishu_alert(title: str, msg: str = "", card: Optional[dict] = None) -> bool:
    """Task 1.1: 飞书 Webhook 封装"""
    webhook_url = settings.FEISHU_WEBHOOK_URL
    if not webhook_url:
        logger.warning("未配置飞书 Webhook 地址 (FEISHU_WEBHOOK_URL)")
        return False
        
    if card:
        payload = {
            "msg_type": "interactive",
            "card": card
        }
    else:
        payload = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": title,
                        "content": [
                            [
                                {
                                    "tag": "text",
                                    "text": msg
                                }
                            ]
                        ]
                    }
                }
            }
        }
    
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(webhook_url, json=payload)
            resp.raise_for_status()
            logger.info("飞书消息发送成功: %s", title)
            return True
        except Exception as e:
            logger.error("飞书消息发送失败: %s", e)
            return False


class PositionService:
    """实盘持仓 CRUD 逻辑"""

    @classmethod
    async def create_position(
        cls, 
        session: AsyncSession, 
        user_id: UUID, 
        data: PositionCreate
    ) -> Position:
        # 初始高水位线可以默认等于最高买入价（或者等于成本价）
        hwm = data.high_water_mark if data.high_water_mark is not None else data.cost_price
        
        position = Position(
            user_id=user_id,
            symbol=data.symbol,
            name=data.name,
            buy_date=data.buy_date,
            cost_price=data.cost_price,
            quantity=data.quantity,
            high_water_mark=hwm,
            status=data.status,
            strategy_id=data.strategy_id
        )
        session.add(position)
        await session.commit()
        await session.refresh(position)

        # 闭环真实联动：创建持仓时自动补入第一笔 buy Transaction 流水
        from apps.trade_copilot.schemas import TradeTransactionCreate
        
        # 为了不造成循环或二次更新cost，我们这里使用“不污染Position当前状态”的方式
        # 直接静默创建一条初始买入流水（无手续费）
        from apps.trade_copilot.models import TradeTransaction
        txn = TradeTransaction(
            position_id=position.id,
            action="buy",
            price=data.cost_price,
            quantity=data.quantity,
            fee=0.0
        )
        session.add(txn)
        await session.commit()

        return position

    @classmethod
    async def list_positions(
        cls, 
        session: AsyncSession, 
        user_id: UUID,
        status: Optional[str] = None
    ) -> List[Position]:
        stmt = select(Position).where(
            Position.user_id == user_id,
            Position.is_deleted == False
        )
        if status:
            stmt = stmt.where(Position.status == status)
        
        result = await session.execute(stmt)
        return list(result.scalars().all())
    
    @classmethod
    async def get_position(
        cls,
        session: AsyncSession,
        user_id: UUID,
        position_id: UUID
    ) -> Optional[Position]:
        stmt = select(Position).where(
            Position.id == position_id,
            Position.user_id == user_id,
            Position.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def update_position(
        cls,
        session: AsyncSession,
        user_id: UUID,
        position_id: UUID,
        data: PositionUpdate
    ) -> Optional[Position]:
        position = await cls.get_position(session, user_id, position_id)
        if not position:
            return None
            
        if data.high_water_mark is not None:
            position.high_water_mark = data.high_water_mark
        if data.status is not None:
            position.status = data.status
        if data.strategy_id is not None:
            position.strategy_id = data.strategy_id
            
        await session.commit()
        await session.refresh(position)
        return position

    @classmethod
    async def delete_position(
        cls,
        session: AsyncSession,
        user_id: UUID,
        position_id: UUID
    ) -> bool:
        position = await cls.get_position(session, user_id, position_id)
        if not position:
            return False
            
        position.is_deleted = True
        await session.commit()
        return True


class MarketService:
    """基础行情与大盘分析服务 (缓存驱动)"""
    
    REDIS_KEY_MARKET_STATUS = "trade_copilot:market_status"
    REDIS_KEY_ST_LIST = "trade_copilot:st_list"

    @classmethod
    async def get_market_status(cls, redis: aioredis.Redis = None) -> MarketStatusOut:
        """获取大盘红绿灯状态，优先从 Redis 缓存获取

        Args:
            redis: 可选的 Redis 客户端实例。Celery 任务中需传入独立创建的 local_redis，
                   以避免全局 redis_client 跨事件循环导致的 "Event loop is closed" 错误。
                   不传则使用全局 redis_client（适用于 FastAPI 请求上下文）。
        """
        r = redis or redis_client
        cached_data = await r.get(cls.REDIS_KEY_MARKET_STATUS)
        if cached_data:
            try:
                data = json.loads(cached_data)
                return MarketStatusOut(**data)
            except Exception as e:
                logger.error(f"解析缓存 market_status 失败: {e}")

        # 如果没有缓存，则实时计算
        # A股特色：红涨绿跌。均在线上（好）为红，均跌破（坏）为绿
        sh_status, sz_status = "red", "red"
        sh_reason, sz_reason = "上证运行在20日线上方，行情向好", "深证运行在20日线上方，行情向好"

        try:
            sh_kline = await AkShareClient.get_index_kline(symbol="000001")
            sz_kline = await AkShareClient.get_index_kline(symbol="399001")

            if getattr(sh_kline, "below_ma20", False):
                sh_status = "green"
                sh_reason = f"危险：上证已跌破20日防守线 (收盘:{sh_kline.close:.2f} MA20:{sh_kline.ma20:.2f})"
            if getattr(sz_kline, "below_ma20", False):
                sz_status = "green"
                sz_reason = f"危险：深证/创业板走弱，已跌破20日防守线 (收盘:{sz_kline.close:.2f} MA20:{sz_kline.ma20:.2f})"
                
        except Exception as e:
            logger.error(f"计算大盘状态遭遇异常: {e}")
            sh_status, sz_status = "unknown", "unknown"
            sh_reason, sz_reason = "获取上证指数并计算MA20出现异常", "获取深证指数并计算MA20出现异常"
            
        result = MarketStatusOut(
            sh_status=sh_status,
            sz_status=sz_status,
            sh_reason=sh_reason,
            sz_reason=sz_reason,
            update_time=datetime.now()
        )
        
        # 写入缓存，设置 60 分钟过期时间做兜底（实际上主要是由 daily beat 每天 15:05 跑批写入替换，这只是防止没数据的情况）
        await r.set(
            cls.REDIS_KEY_MARKET_STATUS,
            result.model_dump_json(),
            ex=3600
        )

        return result

    @classmethod
    async def get_st_list(cls, redis: aioredis.Redis = None) -> STListOut:
        """获取并缓存全市场 ST 股票列表

        Args:
            redis: 可选的 Redis 客户端实例。Celery 任务中需传入独立创建的 local_redis。
        """
        r = redis or redis_client
        # 尝试从 Redis 缓存获取
        try:
            cached_data = await r.get(cls.REDIS_KEY_ST_LIST)
            if cached_data:
                try:
                    data = json.loads(cached_data)
                    return STListOut(**data)
                except Exception as e:
                    logger.error(f"解析缓存 st_list 失败: {e}")
        except Exception as e:
            logger.warning(f"Redis 读取失败，跳过缓存: {e}")

        # 如果没有缓存，则调接口查
        try:
            stocks = await AkShareClient.get_all_st_stocks()
        except Exception as e:
            logger.error(f"获取 ST 股票列表失败: {e}")
            # 返回空列表，不影响业务
            stocks = []

        result = STListOut(
            count=len(stocks),
            stocks=stocks,
            update_time=datetime.now()
        )

        # 尝试写入缓存
        try:
            await r.set(
                cls.REDIS_KEY_ST_LIST,
                result.model_dump_json(),
                ex=86400
            )
        except Exception as e:
            logger.warning(f"Redis 写入失败，跳过缓存: {e}")

        return result

    @classmethod
    async def get_market_thermometer(cls) -> MarketThermometerOut:
        """获取大盘温度计（纯缓存读取，数据由定时任务预热）"""
        REDIS_KEY = "trade_copilot:market_thermometer"

        try:
            cached_data = await redis_client.get(REDIS_KEY)
            if cached_data:
                data = json.loads(cached_data)
                return MarketThermometerOut(**data)
        except Exception as e:
            logger.error(f"读取温度计缓存失败: {e}")

        raise AppException(code=504, message="市场温度计数据尚未就绪，盘后 15:10 后可查看")

class WatchlistService:
    """观察池 CRUD 逻辑"""

    @classmethod
    async def create_watchlist(
        cls, 
        session: AsyncSession, 
        user_id: UUID, 
        data: WatchlistCreate
    ) -> Watchlist:
        # 添加前先排雷：检查是否为 ST 股票
        st_data = await MarketService.get_st_list()
        if data.symbol in st_data.stocks:
            raise ValueError(f"无法加入观察池：{data.name} ({data.symbol}) 属于 ST 风险标的！")

        watchlist = Watchlist(
            user_id=user_id,
            symbol=data.symbol,
            name=data.name,
            reason=data.reason,
            monitor_status=data.monitor_status
        )
        session.add(watchlist)
        await session.commit()
        await session.refresh(watchlist)
        return watchlist

    @classmethod
    async def list_watchlist(
        cls, 
        session: AsyncSession, 
        user_id: UUID,
        status: Optional[str] = None
    ) -> List[Watchlist]:
        stmt = select(Watchlist).where(
            Watchlist.user_id == user_id,
            Watchlist.is_deleted == False
        )
        if status:
            stmt = stmt.where(Watchlist.monitor_status == status)
        
        result = await session.execute(stmt)
        return list(result.scalars().all())
    
    @classmethod
    async def get_watchlist(
        cls,
        session: AsyncSession,
        user_id: UUID,
        watchlist_id: UUID
    ) -> Optional[Watchlist]:
        stmt = select(Watchlist).where(
            Watchlist.id == watchlist_id,
            Watchlist.user_id == user_id,
            Watchlist.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def update_watchlist(
        cls,
        session: AsyncSession,
        user_id: UUID,
        watchlist_id: UUID,
        data: WatchlistUpdate
    ) -> Optional[Watchlist]:
        watchlist = await cls.get_watchlist(session, user_id, watchlist_id)
        if not watchlist:
            return None
            
        if data.reason is not None:
            watchlist.reason = data.reason
        if data.monitor_status is not None:
            watchlist.monitor_status = data.monitor_status
            
        await session.commit()
        await session.refresh(watchlist)
        return watchlist

    @classmethod
    async def delete_watchlist(
        cls,
        session: AsyncSession,
        user_id: UUID,
        watchlist_id: UUID
    ) -> bool:
        watchlist = await cls.get_watchlist(session, user_id, watchlist_id)
        if not watchlist:
            return False
            
        watchlist.is_deleted = True
        await session.commit()
        return True


class TradeStrategyService:
    @classmethod
    async def create_strategy(
        cls, 
        session: AsyncSession, 
        user_id: UUID, 
        data: TradeStrategyCreate
    ) -> TradeStrategy:
        strategy = TradeStrategy(
            user_id=user_id,
            name=data.name,
            stop_loss_pct=data.stop_loss_pct,
            take_profit_drawdown_pct=data.take_profit_drawdown_pct,
            description=data.description
        )
        session.add(strategy)
        await session.commit()
        await session.refresh(strategy)
        return strategy

    @classmethod
    async def list_strategies(
        cls, 
        session: AsyncSession, 
        user_id: UUID
    ) -> List[TradeStrategy]:
        stmt = select(TradeStrategy).where(
            TradeStrategy.user_id == user_id,
            TradeStrategy.is_deleted == False
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_strategy(
        cls,
        session: AsyncSession,
        user_id: UUID,
        strategy_id: UUID
    ) -> Optional[TradeStrategy]:
        stmt = select(TradeStrategy).where(
            TradeStrategy.id == strategy_id,
            TradeStrategy.user_id == user_id,
            TradeStrategy.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def update_strategy(
        cls,
        session: AsyncSession,
        user_id: UUID,
        strategy_id: UUID,
        data: TradeStrategyUpdate
    ) -> Optional[TradeStrategy]:
        strategy = await cls.get_strategy(session, user_id, strategy_id)
        if not strategy:
            return None
            
        if data.name is not None:
            strategy.name = data.name
        if data.stop_loss_pct is not None:
            strategy.stop_loss_pct = data.stop_loss_pct
        if data.take_profit_drawdown_pct is not None:
            strategy.take_profit_drawdown_pct = data.take_profit_drawdown_pct
        if data.description is not None:
            strategy.description = data.description
            
        await session.commit()
        await session.refresh(strategy)
        return strategy

    @classmethod
    async def delete_strategy(
        cls,
        session: AsyncSession,
        user_id: UUID,
        strategy_id: UUID
    ) -> bool:
        strategy = await cls.get_strategy(session, user_id, strategy_id)
        if not strategy:
            return False
            
        strategy.is_deleted = True
        await session.commit()
        return True

class TradingJournalService:
    @classmethod
    async def create_journal(
        cls, 
        session: AsyncSession, 
        user_id: UUID, 
        data: TradingJournalCreate
    ) -> TradingJournal:
        from sqlalchemy import select
        # 每天只能有一条记录
        stmt = select(TradingJournal).where(
            TradingJournal.user_id == user_id, 
            TradingJournal.record_date == data.record_date,
            TradingJournal.is_deleted == False
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing:
            raise ValueError(f"您在 {data.record_date} 已经写过日志了，可以改为更新操作。")
            
        journal = TradingJournal(
            user_id=user_id,
            record_date=data.record_date,
            execution_score=data.execution_score,
            notes=data.notes,
            mistakes_made=data.mistakes_made,
            emotions=data.emotions
        )
        session.add(journal)
        await session.commit()
        await session.refresh(journal)
        return journal

    @classmethod
    async def list_journals(
        cls, 
        session: AsyncSession, 
        user_id: UUID
    ) -> List[TradingJournal]:
        from sqlalchemy import select
        stmt = select(TradingJournal).where(
            TradingJournal.user_id == user_id,
            TradingJournal.is_deleted == False
        ).order_by(TradingJournal.record_date.desc())
        
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_journal(
        cls,
        session: AsyncSession,
        user_id: UUID,
        journal_id: UUID
    ) -> Optional[TradingJournal]:
        from sqlalchemy import select
        stmt = select(TradingJournal).where(
            TradingJournal.id == journal_id,
            TradingJournal.user_id == user_id,
            TradingJournal.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def update_journal(
        cls,
        session: AsyncSession,
        user_id: UUID,
        journal_id: UUID,
        data: TradingJournalUpdate
    ) -> Optional[TradingJournal]:
        journal = await cls.get_journal(session, user_id, journal_id)
        if not journal:
            return None
            
        if data.execution_score is not None:
            journal.execution_score = data.execution_score
        if data.notes is not None:
            journal.notes = data.notes
        if data.mistakes_made is not None:
            journal.mistakes_made = data.mistakes_made
        if data.emotions is not None:
            journal.emotions = data.emotions
            
        await session.commit()
        await session.refresh(journal)
        return journal

class PositionSizingService:
    @classmethod
    def calculate_sizing(
        cls, 
        spot_price: float, 
        market_sh_status: str, 
        market_sz_status: str,
        total_capital: float = 100_000.0,
        available_capital: float = None,
        win_rate: float = 0.5,
        stop_loss_pct: float = 0.05,
        take_profit_pct: float = 0.15
    ) -> dict:
        """
        根据简版凯利公式和市场红绿灯，计算建议买入仓位
        """
        # 1. 判定大盘环境得分 (市场红绿灯因子: red=好, green=坏)
        if market_sh_status == "red" and market_sz_status == "red":
            market_score = 1.0  # 大盘大好
            real_win_rate = max(win_rate, 0.6)
            max_limit = 0.4     # 激进：单只股票上限 40%
        elif market_sh_status == "red" or market_sz_status == "red":
            market_score = 0.5  # 大盘震荡
            real_win_rate = min(win_rate, 0.4)
            max_limit = 0.2     # 偏保守：单只股票上限 20%
        else:
            market_score = 0.0  # 大盘破位
            real_win_rate = 0.1 # 胜率极小
            max_limit = 0.0

        if market_score == 0:
            return {"suggested_position_pct": 0, "suggested_capital": 0, "suggested_shares": 0, "kelly_f": 0, "reason": "大盘双指标破位(绿灯)，系统建议[空仓]规避系统性风险。"}

        # 2. 凯利公式计算 f = p - q/b 
        # b = 盈亏比 = take_profit / stop_loss 
        b = take_profit_pct / stop_loss_pct
        p = real_win_rate
        q = 1 - p
        
        kelly_f = p - (q / b)
        
        # 凯利公式通常比较满仓激进，这里采用 半凯利 (Half-Kelly) 理念以控制回撤
        safe_f = kelly_f / 2 
        
        # 将最大仓位限制在规定范围内
        final_pct = min(safe_f, max_limit)

        if final_pct <= 0:
            return {"suggested_position_pct": 0, "suggested_capital": 0, "suggested_shares": 0, "kelly_f": round(kelly_f, 3), "reason": f"盈亏比或胜率过低，原凯利算盘结果为不发车 (Kelly={kelly_f:.2f})。"}
            
        suggested_capital = total_capital * final_pct
        if available_capital is not None:
            suggested_capital = min(suggested_capital, available_capital)
        
        # 计算能买多少手（1手=100股）
        one_lot_cost = spot_price * 100
        shares_100 = int(suggested_capital // one_lot_cost) * 100
        
        if shares_100 == 0:
             return {"suggested_position_pct": final_pct, "suggested_capital": suggested_capital, "suggested_shares": 0, "kelly_f": round(kelly_f, 3), "reason": f"即使建议仓位为 {final_pct*100:.1f}%, 但分配资金({suggested_capital:.2f})不足以买入一手({one_lot_cost:.2f})。"}

        actual_capital = shares_100 * spot_price
        return {
            "suggested_position_pct": final_pct,
            "suggested_capital": actual_capital,
            "suggested_shares": shares_100 // 100,
            "kelly_f": round(kelly_f, 3),
            "reason": f"当前胜率估算: {real_win_rate*100:.0f}%, 盈亏比估算: {b:.1f}\n建议采用半凯利法投入仓位 **{final_pct*100:.1f}%**。"
        }

class UserTradeSettingsService:
    @classmethod
    async def get_settings(
        cls,
        session: AsyncSession,
        user_id: UUID
    ) -> UserTradeSettings:
        from sqlalchemy import select
        stmt = select(UserTradeSettings).where(
            UserTradeSettings.user_id == user_id,
            UserTradeSettings.is_deleted == False
        )
        result = await session.execute(stmt)
        settings = result.scalars().first()
        
        # 如果没有配置就初始化一个默认十万资金兜底
        if not settings:
            settings = UserTradeSettings(user_id=user_id, total_capital=100000.0)
            session.add(settings)
            await session.commit()
            await session.refresh(settings)

        return settings

    @classmethod
    async def update_settings(
        cls,
        session: AsyncSession,
        user_id: UUID,
        data: UserTradeSettingsUpdate
    ) -> UserTradeSettings:
        settings = await cls.get_settings(session, user_id)
        if data.total_capital is not None:
            settings.total_capital = data.total_capital
        if getattr(data, "commission_rate", None) is not None:
            settings.commission_rate = data.commission_rate
        if getattr(data, "min_commission", None) is not None:
            settings.min_commission = data.min_commission
        if getattr(data, "stamp_duty_rate", None) is not None:
            settings.stamp_duty_rate = data.stamp_duty_rate
            
        await session.commit()
        await session.refresh(settings)
        return settings

class TradeTransactionService:
    @classmethod
    async def add_transaction(
        cls,
        session: AsyncSession,
        user_id: UUID,
        position_id: UUID,
        data: TradeTransactionCreate
    ) -> Optional[TradeTransaction]:
        from apps.trade_copilot.models import Position, UserTradeSettings
        from sqlalchemy import select
        
        # 1. 确认该 Position 属于该 User
        stmt = select(Position).where(
            Position.id == position_id,
            Position.user_id == user_id,
            Position.is_deleted == False
        )
        pos = (await session.execute(stmt)).scalars().first()
        if not pos:
            raise ValueError("持仓不存在或无权操作")

        # 2. 拉取用户的费率设置
        settings = await UserTradeSettingsService.get_settings(session, user_id)
        
        # 3. 计算手续费 (Commission + Stamp Duty)
        trade_amount = data.price * data.quantity
        commission_fee = max(trade_amount * settings.commission_rate, settings.min_commission)
        
        if data.action == "sell":
            stamp_duty_fee = trade_amount * settings.stamp_duty_rate
        else:
            stamp_duty_fee = 0.0
            
        total_fee = commission_fee + stamp_duty_fee
        
        # 4. 生成交易流水
        txn = TradeTransaction(
            position_id=position_id,
            action=data.action,
            price=data.price,
            quantity=data.quantity,
            fee=total_fee
        )
        session.add(txn)
        
        # 5. 更新 Position 的总成本和数量
        old_qty = pos.quantity
        old_cost = pos.cost_price
        
        if data.action == "buy":
            new_qty = old_qty + data.quantity
            # 成本价重排: (旧总成本 + 新买入总投入 + 新产生的费用) / 新股数
            new_cost = ((old_cost * old_qty) + trade_amount + total_fee) / new_qty if new_qty > 0 else 0
            pos.quantity = new_qty
            pos.cost_price = new_cost
            if pos.status == "closed":
                pos.status = "holding"
        
        elif data.action == "sell":
            if data.quantity > old_qty:
                raise ValueError(f"卖出数量({data.quantity})不能大于当前持仓数量({old_qty})")
                
            new_qty = old_qty - data.quantity
            if new_qty == 0:
                # 清仓
                pos.quantity = 0
                pos.status = "closed"
            else:
                # 摊薄成本计算: (旧总成本 - 这部分卖出套现到的净回笼资金) -> 这部分净回笼指的是 卖出总价 - 卖出手续费
                # 量化里经常把盈利减仓用来降成本: (旧总成本 - (现价 * 数量 - 手续费)) / 剩余数量
                pos.quantity = new_qty
                new_cost = ((old_cost * old_qty) - (trade_amount - total_fee)) / new_qty
                # 如果套利极好，成本可能降为负数，真实量化允许成本为负
                pos.cost_price = new_cost

        await session.commit()
        await session.refresh(txn)
        return txn

    @classmethod
    async def get_transactions(
        cls,
        session: AsyncSession,
        user_id: UUID,
        position_id: UUID
    ) -> List[TradeTransaction]:
        from apps.trade_copilot.models import Position
        from sqlalchemy import select
        stmt = select(TradeTransaction).join(Position).where(
            Position.id == position_id,
            Position.user_id == user_id,
            TradeTransaction.is_deleted == False
        ).order_by(TradeTransaction.transaction_time.asc())
        
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_market_thermometer(cls):
        """获取市场温度计与板块轮动（已弃用 DataFrame，保留兼容）"""
        # 委托给 MarketService.get_market_thermometer
        return await MarketService.get_market_thermometer()


class StockInfoService:
    """A股股票基本信息服务"""

    @classmethod
    async def sync_all_stocks(cls, session: AsyncSession) -> int:
        """
        同步全A股股票基本信息到数据库
        返回同步的股票数量
        """
        from apps.trade_copilot.models import StockInfo

        # 获取所有A股股票信息
        stocks = await AkShareClient.get_all_a_stock_info()
        if not stocks:
            logger.warning("未获取到股票信息")
            return 0

        # 获取ST股票列表用于标记
        st_symbols = set()
        try:
            st_data = await MarketService.get_st_list()
            st_symbols = set(st_data.stocks)
        except Exception as e:
            logger.warning(f"获取ST股票列表失败，跳过ST标记: {e}")

        sync_count = 0
        for stock in stocks:
            try:
                # 检查是否已存在
                stmt = select(StockInfo).where(StockInfo.symbol == stock.symbol)
                existing = (await session.execute(stmt)).scalars().first()

                is_st = stock.symbol in st_symbols

                if existing:
                    # 更新现有记录
                    existing.name = stock.name
                    existing.is_st = is_st
                else:
                    # 新增记录
                    new_stock = StockInfo(
                        symbol=stock.symbol,
                        name=stock.name,
                        industry=stock.industry,
                        sector=stock.sector,
                        list_date=stock.list_date,
                        total_market_value=stock.total_market_value,
                        circulating_market_value=stock.circulating_market_value,
                        is_st=is_st
                    )
                    session.add(new_stock)
                sync_count += 1

                # 每500条提交一次，避免事务过大
                if sync_count % 500 == 0:
                    await session.commit()

            except Exception as e:
                logger.error(f"同步股票 {stock.symbol} 失败: {e}")
                await session.rollback()
                continue

        await session.commit()
        logger.info(f"成功同步 {sync_count} 只股票信息")
        return sync_count

    @classmethod
    async def search_stocks(
        cls,
        session: AsyncSession,
        keyword: str,
        limit: int = 20
    ) -> List:
        """
        搜索股票（按代码或名称模糊匹配）
        """
        from apps.trade_copilot.models import StockInfo

        keyword = keyword.strip()
        if not keyword:
            return []

        stmt = select(StockInfo).where(
            StockInfo.is_deleted == False,
            (StockInfo.symbol.ilike(f"%{keyword}%") | StockInfo.name.ilike(f"%{keyword}%"))
        ).limit(limit)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_stock_by_symbol(cls, session: AsyncSession, symbol: str):
        """
        根据股票代码获取股票信息
        """
        from apps.trade_copilot.models import StockInfo

        stmt = select(StockInfo).where(
            StockInfo.symbol == symbol,
            StockInfo.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()
