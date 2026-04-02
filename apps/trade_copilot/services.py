import logging
from typing import List, Optional
import json
from datetime import datetime
import httpx
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.redis_client import redis_client
from apps.trade_copilot.models import Position, Watchlist, TradeStrategy, TradingJournal, UserTradeSettings, TradeTransaction
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
        user_id: int, 
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
        user_id: int,
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
        user_id: int,
        position_id: int
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
        user_id: int,
        position_id: int,
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
        user_id: int,
        position_id: int
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
    async def get_market_status(cls) -> MarketStatusOut:
        """获取大盘红绿灯状态，优先从 Redis 缓存获取"""
        cached_data = await redis_client.get(cls.REDIS_KEY_MARKET_STATUS)
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
        await redis_client.set(
            cls.REDIS_KEY_MARKET_STATUS,
            result.model_dump_json(),
            ex=3600
        )
        
        return result

    @classmethod
    async def get_st_list(cls) -> STListOut:
        """获取并缓存全市场 ST 股票列表"""
        cached_data = await redis_client.get(cls.REDIS_KEY_ST_LIST)
        if cached_data:
            try:
                data = json.loads(cached_data)
                return STListOut(**data)
            except Exception as e:
                logger.error(f"解析缓存 st_list 失败: {e}")

        # 如果没有缓存，则调接口查
        stocks = await AkShareClient.get_all_st_stocks()
        result = STListOut(
            count=len(stocks),
            stocks=stocks,
            update_time=datetime.now()
        )
        
        # 同样作为 Miss 后的回源兜底，过期时间设置长一点 (例如一天 86400秒)
        await redis_client.set(
            cls.REDIS_KEY_ST_LIST,
            result.model_dump_json(),
            ex=86400
        )
        return result

    @classmethod
    async def get_market_thermometer(cls) -> MarketThermometerOut:
        """获取大盘温度计和板块轮动"""
        REDIS_KEY = "trade_copilot:market_thermometer"
        cached_data = await redis_client.get(REDIS_KEY)
        if cached_data:
            try:
                data = json.loads(cached_data)
                return MarketThermometerOut(**data)
            except Exception as e:
                logger.error(f"解析缓存 market_thermometer 失败: {e}")

        # 如果没有缓存，通过 AkShare 获取实时结果
        data = await AkShareClient.get_market_thermometer_data()
        df_spot = data['spot']
        df_board = data['board']

        # 统计市场家数（排除退市等无数据的）
        df_spot = df_spot.dropna(subset=['涨跌幅'])
        total_stocks = len(df_spot)
        up_count = len(df_spot[df_spot['涨跌幅'] > 0])
        down_count = len(df_spot[df_spot['涨跌幅'] < 0])
        limit_up_count = len(df_spot[df_spot['涨跌幅'] >= 9.8])
        limit_down_count = len(df_spot[df_spot['涨跌幅'] <= -9.8])

        # 按照涨停家数和上涨家数综合打分（一个极其简化的温度计）
        score = min(100, max(0, int((up_count / max(1, total_stocks)) * 60 + (limit_up_count / 100) * 40)))
        
        # 温度标语：冰点 / 分歧 / 弱修复 / 温和 / 高潮
        if score < 20:
            temperature = "冰点 (情绪极度低迷，注意杀跌风险)"
        elif score < 40:
            temperature = "分歧 (亏钱效应发酵，适合空仓或试错)"
        elif score < 60:
            temperature = "弱修复 (局部赚钱效应，控制仓位)"
        elif score < 80:
            temperature = "温和 (普涨行情，适宜持股)"
        else:
            temperature = "高潮 (情绪亢奋，注意高位落袋或警惕分歧转一致的尾声)"

        # 板块轮动数据 (取前5)
        top_sectors = []
        if not df_board.empty and '板块名称' in df_board.columns and '涨跌幅' in df_board.columns:
            # AkShare 返回的值大多是字符串或能转成数值型
            df_board['涨跌幅'] = df_board['涨跌幅'].apply(lambda x: float(x) if x != '-' and x else 0.0)
            df_board = df_board.sort_values(by='涨跌幅', ascending=False)
            for _, row in df_board.head(5).iterrows():
                top_sectors.append(
                    SectorItemOut(
                        sector_name=str(row['板块名称']),
                        pct_change=float(row['涨跌幅'])
                    )
                )

        result = MarketThermometerOut(
            total_stocks=total_stocks,
            up_count=up_count,
            down_count=down_count,
            limit_up_count=limit_up_count,
            limit_down_count=limit_down_count,
            score=score,
            temperature=temperature,
            top_sectors=top_sectors
        )

        await redis_client.set(REDIS_KEY, result.model_dump_json(), ex=300)  # 缓存 5 分钟
        return result

class WatchlistService:
    """观察池 CRUD 逻辑"""

    @classmethod
    async def create_watchlist(
        cls, 
        session: AsyncSession, 
        user_id: int, 
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
        user_id: int,
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
        user_id: int,
        watchlist_id: int
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
        user_id: int,
        watchlist_id: int,
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
        user_id: int,
        watchlist_id: int
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
        user_id: int, 
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
        user_id: int
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
        user_id: int,
        strategy_id: int
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
        user_id: int,
        strategy_id: int,
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
        user_id: int,
        strategy_id: int
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
        user_id: int, 
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
        user_id: int
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
        user_id: int,
        journal_id: int
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
        user_id: int,
        journal_id: int,
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
        user_id: int
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
        user_id: int,
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
        user_id: int,
        position_id: int,
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
        user_id: int,
        position_id: int
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
        """获取市场温度计与板块轮动"""
        try:
            data = await AkShareClient.get_market_thermometer_data()
            df_spot = data["spot"]
            df_board = data["board"]

            # 计算市场温度
            # 过滤掉涨跌幅为 NaN 的数据
            spot = df_spot[~df_spot["涨跌幅"].isna()]
            total_count = len(spot)

            advance_count = len(spot[spot["涨跌幅"] > 0])
            decline_count = len(spot[spot["涨跌幅"] < 0])
            flat_count = total_count - advance_count - decline_count

            # 估算涨跌停
            limit_up_count = len(spot[spot["涨跌幅"] >= 9.8])
            limit_down_count = len(spot[spot["涨跌幅"] <= -9.8])

            median_pct = float(spot["涨跌幅"].median()) if not spot.empty else 0.0

            # 赚钱效应打分 (0-100) 简单模型: 基础分50 + 涨跌比加成 + 涨跌停加成
            score = 50.0
            if total_count > 0:
                adv_ratio = advance_count / total_count
                score += (adv_ratio - 0.5) * 50  # -25 to +25

                # 涨跌停加成
                if limit_up_count + limit_down_count > 0:
                    limit_ratio = limit_up_count / (limit_up_count + limit_down_count)
                    score += (limit_ratio - 0.5) * 50 # -25 to +25

            score = max(0, min(100, int(score)))

            # 解析板块数据 (前5)
            top_sectors = []
            if not df_board.empty:
                df_board_sorted = df_board.sort_values(by="涨跌幅", ascending=False).head(5)
                from apps.trade_copilot.schemas import SectorItemOut
                for _, row in df_board_sorted.iterrows():
                    top_sectors.append(SectorItemOut(
                        sector_name=str(row.get("板块名称", "")),
                        pct_change=float(row.get("涨跌幅", 0.0))
                    ))

            return MarketThermometerOut(
                total_stocks=total_count,
                up_count=advance_count,
                down_count=decline_count,
                flat_count=flat_count,
                limit_up_count=limit_up_count,
                limit_down_count=limit_down_count,
                score=score,
                median_pct_change=median_pct,
                top_sectors=top_sectors
            )
        except Exception as e:
            logger.error(f"计算市场温度计异常: {e}")
            raise
