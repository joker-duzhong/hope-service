"""
Trade Copilot Models
表名前缀: trade_
"""
from typing import Optional
from sqlalchemy import String, Float, Integer, Date, ForeignKey, DateTime
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import CoreModel


class StockInfo(CoreModel):
    """A股股票基本信息表"""
    __tablename__ = "trade_stock_info"

    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, comment="股票代码")
    name: Mapped[str] = mapped_column(String(100), comment="股票名称")
    industry: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="所属行业")
    sector: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="所属板块")
    list_date: Mapped[Optional[Date]] = mapped_column(Date, nullable=True, comment="上市日期")
    total_market_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总市值(元)")
    circulating_market_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="流通市值(元)")
    is_st: Mapped[bool] = mapped_column(Integer, default=False, comment="是否ST股票")


class TradeStrategy(CoreModel):
    """风控策略配置表 V2.0"""
    __tablename__ = "trade_strategies"

    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    name: Mapped[str] = mapped_column(String(100), comment="策略名称 (如: 妖股激进战法, 白马稳健战法)")
    stop_loss_pct: Mapped[float] = mapped_column(Float, default=-0.05, comment="绝对止损比例 (默认: -5%)")
    take_profit_drawdown_pct: Mapped[float] = mapped_column(Float, default=-0.08, comment="回撤止盈比例 (高位回撤, 默认: -8%)")
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="策略备注描述")

class Position(CoreModel):
    """实盘持仓表"""
    __tablename__ = "trade_positions"

    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    symbol: Mapped[str] = mapped_column(String(20), index=True, comment="股票代码")
    name: Mapped[str] = mapped_column(String(100), comment="股票名称")
    buy_date: Mapped[Date] = mapped_column(Date, comment="买入日期")
    cost_price: Mapped[float] = mapped_column(Float, comment="持仓成本价")
    quantity: Mapped[int] = mapped_column(Integer, comment="买入数量")
    high_water_mark: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, comment="买入后的历史最高价"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="holding", index=True, comment="持有状态: holding(持仓), closed(已清仓)"
    )
    
    # V2.0 新增: 关联的风控策略ID (如果为空则使用系统默认兜底值)
    strategy_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("trade_strategies.id", ondelete="SET NULL"), nullable=True, comment="关联风控策略ID"
    )
    
    # 定义关联关系便于查询时直接获取策略配置
    strategy: Mapped[Optional["TradeStrategy"]] = relationship("TradeStrategy")

class Watchlist(CoreModel):
    """观察池表"""
    __tablename__ = "trade_watchlist"

    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    symbol: Mapped[str] = mapped_column(String(20), index=True, comment="股票代码")
    name: Mapped[str] = mapped_column(String(100), comment="股票名称")
    reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="加入理由")
    monitor_status: Mapped[str] = mapped_column(
        String(20), default="active", comment="监控状态: active(监控中), inactive(暂停)"
    )

class DailyMarketLog(CoreModel):
    """大盘每日状态记录表 (用于每日盘后复盘)"""
    __tablename__ = "trade_daily_market_logs"

    record_date: Mapped[Date] = mapped_column(Date, index=True, unique=True, comment="记录日期")
    sh_status: Mapped[str] = mapped_column(String(20), comment="上证状态(red/green)")
    sz_status: Mapped[str] = mapped_column(String(20), comment="深证状态(red/green)")
    sh_reason: Mapped[str] = mapped_column(String(500), comment="上证理由")
    sz_reason: Mapped[str] = mapped_column(String(500), comment="深证理由")

class TradingJournal(CoreModel):
    """交易日记表 (V2.0新增)"""
    __tablename__ = "trade_journals"

    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    record_date: Mapped[Date] = mapped_column(Date, index=True, comment="日记归属日期")
    execution_score: Mapped[int] = mapped_column(Integer, comment="执行力打分(1-10分)")
    notes: Mapped[str] = mapped_column(String(2000), comment="今日复盘与操作日记")
    mistakes_made: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True, comment="今日犯错与反思")
    emotions: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True, comment="心理状态/情绪描述")

class UserTradeSettings(CoreModel):
    """用户交易配置表 (V2.0新增，用于仓位管理等)"""
    __tablename__ = "trade_user_settings"

    user_id: Mapped[int] = mapped_column(Integer, index=True, unique=True, comment="所属用户ID")
    total_capital: Mapped[float] = mapped_column(Float, default=100000.0, comment="计划用于量化交易的总资金(本底)")
    # 交易费率
    commission_rate: Mapped[float] = mapped_column(Float, default=0.00025, comment="券商佣金率(如万2.5)")
    min_commission: Mapped[float] = mapped_column(Float, default=5.0, comment="最低佣金")
    stamp_duty_rate: Mapped[float] = mapped_column(Float, default=0.0005, comment="印花税率")

class TradeTransaction(CoreModel):
    """交易流水表"""
    __tablename__ = "trade_transactions"

    position_id: Mapped[int] = mapped_column(Integer, ForeignKey("trade_positions.id"), index=True, comment="持仓ID")
    action: Mapped[str] = mapped_column(String(10), comment="操作类型: buy 或 sell")
    price: Mapped[float] = mapped_column(Float, comment="交易价格")
    quantity: Mapped[int] = mapped_column(Integer, comment="交易数量")
    fee: Mapped[float] = mapped_column(Float, default=0.0, comment="总费用")
    transaction_time: Mapped[DateTime] = mapped_column(DateTime, default=func.now(), comment="交易数量")

    position = relationship("Position", backref="transactions")

