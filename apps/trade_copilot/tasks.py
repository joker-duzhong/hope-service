import asyncio
import logging
from datetime import datetime
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from core.config import settings
from core.redis_client import redis_client
from apps.trade_copilot.models import Position, DailyMarketLog, Watchlist
from apps.trade_copilot.akshare_client import AkShareClient
from apps.trade_copilot.services import send_feishu_alert, MarketService, PositionSizingService, UserTradeSettingsService
from apps.trade_copilot.feishu_templates import (
    build_trade_alert_card, build_market_status_card, build_sniper_radar_card
)

logger = logging.getLogger(__name__)

async def is_trading_time() -> bool:
    """真实判断当前是否为 A 股交易时间和交易日"""
    now = datetime.now()
    
    hm_str = now.strftime("%H:%M")
    is_time_matched = False
    
    if "09:30" <= hm_str <= "11:30":
        is_time_matched = True
    elif "13:00" <= hm_str <= "15:00":
        is_time_matched = True
        
    if not is_time_matched:
        return False
        
    # 时间满足，再通过 akshare 验证今天是否是真正的 A 股交易日 (排除节假日)
    is_real_trading_date = await AkShareClient.is_trading_date(now)
    return is_real_trading_date

async def run_monitor() -> str:
    """盘中价格监控引擎异步执行逻辑"""
    if not await is_trading_time():
        logger.info("当前非交易时间或法定节假日休市，跳过监控...")
        return "Not trading time"

    # Celery 的每次 asyncio.run() 都会创建一个新的事件循环
    # 为了避免 `RuntimeError: Task attached to a different loop`
    # 在任务内部临时创建基于 NullPool/独立 的异步引擎和 session maker，避免池残留在上一次销毁的事件循环中
    from sqlalchemy import select
    from sqlalchemy.pool import NullPool
    local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

    from sqlalchemy.orm import joinedload

    try:
        async with local_session_maker() as session:
            # 查询所有持仓中股票，使用 joinedload 预加载关联的 strategy
            stmt = select(Position).options(joinedload(Position.strategy)).where(
                Position.status == "holding",
                Position.is_deleted == False
            )
            result = await session.execute(stmt)
            positions = list(result.scalars().all())

            if not positions:
                return "No holding positions"

            symbols = list(set([p.symbol for p in positions]))
            logger.info(f"开启盘面监控，当前持仓标的: {symbols}")

            # 请求 AkShare 实时价格快照
            try:
                spots = await AkShareClient.get_a_shares_spot(symbols)
                spot_map = {spot.symbol: spot for spot in spots}
            except Exception as e:
                logger.error(f"获取持仓实时价格失败: {e}")
                return "Failed to fetch spots"

            # 监控规则应用
            for pos in positions:
                spot = spot_map.get(pos.symbol)
                if not spot:
                    logger.warning(f"未能获取到 {pos.symbol}({pos.name}) 的实时行情")
                    continue
                
                curr_price = spot.latest_price
                cost_price = pos.cost_price
                hwm = pos.high_water_mark or cost_price
                
# ========================
                # 动态风控参数获取 (V2.0)
                # ========================
                stop_loss_pct = -0.05  # 系统默认绝对止损兜底 (-5%)
                take_profit_drawdown_pct = -0.08  # 系统默认高位回撤兜底 (-8%)
                strategy_name = "默认系统配置"
                
                if pos.strategy:
                    stop_loss_pct = pos.strategy.stop_loss_pct
                    take_profit_drawdown_pct = pos.strategy.take_profit_drawdown_pct
                    strategy_name = pos.strategy.name

                logger.info(f"标的: {pos.name}({pos.symbol}) - 现价: {curr_price}, 成本: {cost_price}, 历史最高: {hwm} | 策略组: [{strategy_name}]")

                # ========================
                # 规则 1: 破位绝对止损 (基于策略)
                # ========================
                if curr_price < cost_price * (1 + stop_loss_pct):
                    card = build_trade_alert_card(
                        title="🚨 强制割肉通知",
                        symbol=pos.symbol,
                        name=pos.name,
                        curr_price=curr_price,
                        ref_price=cost_price,
                        ref_price_label="持仓成本价",
                        desc=f"【触发策略：{strategy_name}】\n已经触发 {abs(stop_loss_pct)*100}% 绝对破位止损，纪律大于一切，请立即以市价清仓！",
                        color="red"
                    )
                    await send_feishu_alert("🚨 强制割肉通知", card=card)

                # ========================
                # 规则 2: 更新并突破高水位
                # ========================
                if curr_price > hwm:
                    logger.info(f"{pos.name} 破最高价：{hwm} -> {curr_price}")
                    pos.high_water_mark = curr_price
                    await session.commit()

                # ========================
                # 规则 3: 高位回撤止盈 (基于策略)
                # ========================
                elif curr_price < hwm * (1 + take_profit_drawdown_pct):
                    card = build_trade_alert_card(
                        title="🟠 止盈落袋通知",
                        symbol=pos.symbol,
                        name=pos.name,
                        curr_price=curr_price,
                        ref_price=hwm,
                        ref_price_label="盘后最高价(高水位线)",
                        desc=f"【触发策略：{strategy_name}】\n已经从高点回撤超过 {abs(take_profit_drawdown_pct)*100}%，保住利润，请考虑获利了结！",
                        color="orange"
                    )
                    await send_feishu_alert("🟠 止盈落袋通知", card=card)

        return "Success"
    finally:
        await local_engine.dispose()


@shared_task(name="apps.trade_copilot.tasks.monitor_positions_task")
def monitor_positions_task():
    """
    盘中价格监控引擎：
    由 Celery 每 5 分钟触发一次
    """
    # Celery 是同步进程，我们用 asyncio.run 驱动异步爬虫和数据库
    return asyncio.run(run_monitor())

async def run_daily_settlement() -> str:
    """盘后数据结算引擎异步执行逻辑"""
    now = datetime.now()
    if not await AkShareClient.is_trading_date(now):
        logger.info("今天是非交易日，跳过盘后结算...")
        return "Not trading date"

    logger.info("开始执行日常盘后结算引擎...")
    
    # 临时创建基于 NullPool 的异步引擎和 session maker，写入每天的结算记录
    from sqlalchemy.pool import NullPool
    local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

    try:
        # 强制清除旧缓存，保证拿到今天收盘后的最新真实数据
        await redis_client.delete(MarketService.REDIS_KEY_MARKET_STATUS)
        await redis_client.delete(MarketService.REDIS_KEY_ST_LIST)

        # 触发重拉与计算，并在服务内自动写入 Redis 保鲜
        market_status = await MarketService.get_market_status()
        # 仅调用获取以刷新 Redis 中的黑名单缓存供明天使用，但不再做任何通知和统计
        await MarketService.get_st_list()

        logger.info(f"盘后结算完成: 上证={market_status.sh_status}, 深证={market_status.sz_status}")
        
        # 将今天收盘后的最新结算数据永久存入数据库，供历史复盘查看
        async with local_session_maker() as session:
            # 检查今天是否已经记录过，防止重跑任务引发 unique constraint error
            from sqlalchemy import select
            stmt = select(DailyMarketLog).where(DailyMarketLog.record_date == now.date())
            existing_record = (await session.execute(stmt)).scalars().first()
            
            if existing_record:
                existing_record.sh_status = market_status.sh_status
                existing_record.sz_status = market_status.sz_status
                existing_record.sh_reason = market_status.sh_reason
                existing_record.sz_reason = market_status.sz_reason
            else:
                new_record = DailyMarketLog(
                    record_date=now.date(),
                    sh_status=market_status.sh_status,
                    sz_status=market_status.sz_status,
                    sh_reason=market_status.sh_reason,
                    sz_reason=market_status.sz_reason
                )
                session.add(new_record)
            await session.commit()

        # 推理整体警告颜色给大盘总结卡片标题打底
        card_color = "red"  # 默认都好为红色（A股特色）
        card_title = "🔴 红灯报喜：全市场均在进攻阵型"
        
        if market_status.sh_status == "green" and market_status.sz_status == "green":
            card_color = "green"
            card_title = "🟢 绿灯警报：两大市场全部破位，明日严禁开新仓"
        elif market_status.sh_status == "green" or market_status.sz_status == "green":
            card_color = "orange"
            card_title = "🟠 橙灯警报：出现结构性破位，注意控制仓位"

        if card_color in ["green", "orange"]:
            card = build_market_status_card(
                title=card_title,
                status_color=card_color,
                sh_reason=market_status.sh_reason,
                sz_reason=market_status.sz_reason
            )
            await send_feishu_alert("大盘防守警报", card=card)

        return "Success"
    finally:
        await local_engine.dispose()

async def run_sniper_radar() -> str:
    """尾盘狙击雷达判定引擎"""
    now = datetime.now()
    if not await AkShareClient.is_trading_date(now):
        logger.info("今天是非交易日，跳过尾盘狙击...")
        return "Not trading date"

    # 判断是否为最后一次播报 (14:55左右及之后调用的任务即为最终兜底播报)
    is_final_run = (now.hour == 14 and now.minute >= 54)
    logger.info(f"开始执行尾盘狙击雷达... (是否最终播报: {is_final_run})")
    
    from sqlalchemy.pool import NullPool
    try:
        local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
        local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)
    except Exception as e:
        if is_final_run:
            msg = f"❌ 严重警告：尾盘狙击调度引擎崩溃，数据库连通失败！\n错因: {e}\n\n由于系统状态不可知，强烈建议：今日【不要执行任何新仓买入】！"
            await send_feishu_alert("❌ 尾盘引擎严重崩溃", msg)
        return "DB Engine Error"

    try:
        async with local_session_maker() as session:
            # 查询监控中的观察池
            from sqlalchemy import select
            try:
                stmt = select(Watchlist).where(
                    Watchlist.monitor_status == "active",
                    Watchlist.is_deleted == False
                )
                result = await session.execute(stmt)
                watchlists = list(result.scalars().all())
            except Exception as e:
                 if is_final_run:
                     await send_feishu_alert("❌ 尾盘系统异常", msg=f"查询观察池失败：{e}\n请避免盲目操作！")
                 return "DB Query Error"

            if not watchlists:
                if is_final_run:
                     await send_feishu_alert("💡 尾盘狙击最终报告", msg="您的观察池目前为空。今日无任何扫描目标和买入信号。")
                return "No active watchlists"

            # V2.0 计算凯利仓位需要的环境因子
            try:
                m_status = await MarketService.get_market_status()
                m_sh = m_status.sh_status
                m_sz = m_status.sz_status
            except Exception as e:
                logger.error(f"获取大盘状态失败，默认降级为震荡市: {e}")
                m_sh, m_sz = "red", "green"

            hit_count = 0
            fetch_errors = []

            # 提前抓取相关用户的本金配置字典，以备凯利公式使用
            user_ids = list(set([w.user_id for w in watchlists]))
            user_capitals = {}
            user_used_capitals = {}
            for uid in user_ids:
                try:
                    s_config = await UserTradeSettingsService.get_settings(session, uid)
                    user_capitals[uid] = s_config.total_capital
                except Exception:
                    user_capitals[uid] = 100000.0 # 拿不到就默认10万
                
                # 计算当前已被持仓占用的总资金 (粗略估计成本市值 = 当前持有数量 * 成本价)
                try:
                    stmt_pos = select(Position).where(
                        Position.user_id == uid,
                        Position.status == "holding",
                        Position.is_deleted == False
                    )
                    positions = (await session.execute(stmt_pos)).scalars().all()
                    used_val = sum(p.cost_price * p.quantity for p in positions)
                    user_used_capitals[uid] = used_val
                except Exception as e:
                    logger.error(f"获取占用资金失败: {e}")
                    user_used_capitals[uid] = 0.0

            for item in watchlists:
                try:
                    kline = await AkShareClient.get_stock_kline(item.symbol)
                    if not kline:
                        logger.warning(f"获取不到 {item.name}({item.symbol}) K线数据")
                        fetch_errors.append(f"{item.name}({item.symbol})")
                        continue
                    
                    # 判定买点逻辑：收盘价站上 MA5，且 MA5 大于 MA10 (简单的金叉强势判断)
                    cond1 = kline.close > kline.ma5
                    cond2 = kline.ma5 > kline.ma10
                    cond3 = kline.close > kline.ma20

                    if cond1 and cond2 and cond3:
                        hit_count += 1
                        
                        # ========================
                        # V2.0 凯利仓位算盘推演
                        # ========================
                        t_cap = user_capitals.get(item.user_id, 100000.0)
                        u_cap = user_used_capitals.get(item.user_id, 0.0)
                        a_cap = max(0, t_cap - u_cap)  # 剩余可用资金 = 总本金 - 已占用

                        sizing = PositionSizingService.calculate_sizing(
                            spot_price=kline.close,
                            market_sh_status=m_sh,
                            market_sz_status=m_sz,
                            total_capital=t_cap,
                            available_capital=a_cap,
                            win_rate=0.5
                        )
                        if sizing['suggested_shares'] > 0:
                            sizing_str = f"{sizing['reason']}\n当前账户可用资金: **{a_cap:.2f} 元**\n建议分配资金: **{sizing['suggested_capital']:.2f} 元**\n建议买入数量: **{sizing['suggested_shares']} 手**"
                        else:
                            sizing_str = f"**当前不满足开仓条件**：{sizing['reason']}\n*(注: 账户可用资金为 {a_cap:.2f} 元)*"

                        # 触发狙击信号
                        card = build_sniper_radar_card(
                            symbol=item.symbol,
                            name=item.name,
                            reason=item.reason or "无",
                            sizing_info=sizing_str,
                            ma_details=f"最新价: {kline.close:.2f}\nMA5: {kline.ma5:.2f}\nMA10: {kline.ma10:.2f}\nMA20: {kline.ma20:.2f}",
                            desc="满足买入条件: MA5 > MA10 且股价站上多条均线。\n请结合凯利仓位参考，立刻人工确认是否在收盘前进行买入！"
                        )
                        await send_feishu_alert("🟢 狙击雷达发现买点", card=card)
                except Exception as e:
                    fetch_errors.append(f"{item.name}({item.symbol})")
                    logger.error(f"处理狙击雷达标的 {item.symbol} 时异常: {e}")

            # 如果是 14:55 的最终播报，必须必须给予一条总结卡片
            if is_final_run:
                if len(fetch_errors) == len(watchlists):
                    err_str = "、".join(fetch_errors[:5])
                    msg = f"❌ 严重警告：今日尾盘行情数据通道【全部断开】！\n无法获取 {err_str} 等标的最新 K线参数以研判买点。\n\n由于系统致盲，强烈要求：\n今日【严禁任何盲目操作和买入】！"
                    await send_feishu_alert("❌ 尾盘数据通道完全失败", msg)
                elif hit_count == 0:
                    note = ""
                    if fetch_errors:
                        note = f"\n（注：期间 {len(fetch_errors)} 只股票拉取异常，但其余均未达标）"
                    msg = f"今日尾盘雷达扫描完毕，观察池内有效标的均【未满足】右侧突破确认买入条件。{note}\n\n请保持当前底仓不动，管住手，提前祝下班愉快！"
                    await send_feishu_alert("💡 尾盘狙击总结报告", msg)

        return "Success"
    finally:
        await local_engine.dispose()

@shared_task(name="apps.trade_copilot.tasks.daily_settlement_task", bind=True, max_retries=3, default_retry_delay=300)
def daily_settlement_task(self):
    """
    盘后结算任务: 每天 15:05 执行一次
    失败重试机制: 默认 5分钟重试一次，最多重试 3 次 (即如果 AkShare/DB 意外宕机，将在 15:10，15:15 等自动弥补)
    """
    try:
        return asyncio.run(run_daily_settlement())
    except Exception as e:
        logger.error(f"盘后结算任务失败，准备重试: {e}")
        raise self.retry(exc=e)

@shared_task(name="apps.trade_copilot.tasks.sniper_radar_task", bind=True, max_retries=3, default_retry_delay=120)
def sniper_radar_task(self):
    """
    尾盘狙击雷达: 每天 14:45 执行一次
    """
    try:
        return asyncio.run(run_sniper_radar())
    except Exception as e:
        logger.error(f"狙击雷达任务失败，准备重试: {e}")
        raise self.retry(exc=e)


async def run_sync_stock_info() -> str:
    """同步A股股票基本信息到数据库"""
    logger.info("开始同步A股股票基本信息...")

    from sqlalchemy.pool import NullPool
    local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

    try:
        async with local_session_maker() as session:
            from apps.trade_copilot.services import StockInfoService
            count = await StockInfoService.sync_all_stocks(session)
            logger.info(f"A股股票基本信息同步完成，共同步 {count} 只股票")
            return f"Synced {count} stocks"
    except Exception as e:
        logger.error(f"同步A股股票基本信息失败: {e}")
        return f"Failed: {str(e)}"
    finally:
        await local_engine.dispose()


@shared_task(name="apps.trade_copilot.tasks.sync_stock_info_task", bind=True, max_retries=3, default_retry_delay=300)
def sync_stock_info_task(self):
    """
    同步A股股票基本信息任务: 每天凌晨 1:00 执行一次
    """
    try:
        return asyncio.run(run_sync_stock_info())
    except Exception as e:
        logger.error(f"同步股票信息任务失败，准备重试: {e}")
        raise self.retry(exc=e)
