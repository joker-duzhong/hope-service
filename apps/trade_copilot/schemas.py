from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field


# --- 股票基本信息 Schemas ---
class StockInfoBase(BaseModel):
    symbol: str = Field(..., description="股票代码", max_length=20)
    name: str = Field(..., description="股票名称", max_length=100)
    industry: Optional[str] = Field(None, description="所属行业", max_length=100)
    sector: Optional[str] = Field(None, description="所属板块", max_length=100)
    list_date: Optional[date] = Field(None, description="上市日期")
    total_market_value: Optional[float] = Field(None, description="总市值(元)")
    circulating_market_value: Optional[float] = Field(None, description="流通市值(元)")
    is_st: bool = Field(False, description="是否ST股票")


class StockInfoCreate(StockInfoBase):
    pass


class StockInfoOut(StockInfoBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class StockInfoSearchResult(BaseModel):
    """股票搜索结果"""
    symbol: str = Field(..., description="股票代码")
    name: str = Field(..., description="股票名称")
    industry: Optional[str] = Field(None, description="所属行业")


class PositionBase(BaseModel):
    symbol: str = Field(..., description="股票代码", max_length=20)
    name: str = Field(..., description="股票名称", max_length=100)
    buy_date: date = Field(..., description="买入日期")
    cost_price: float = Field(..., description="持仓成本价")
    quantity: int = Field(..., description="买入数量")
    high_water_mark: Optional[float] = Field(None, description="盘后最高价(高水位线)")
    status: str = Field("holding", description="持有状态: holding(持仓), closed(已清仓)", max_length=20)
    strategy_id: Optional[int] = Field(None, description="关联风控策略ID (V2.0新增)")

class PositionCreate(PositionBase):
    pass

class PositionUpdate(BaseModel):
    high_water_mark: Optional[float] = None
    status: Optional[str] = None
    strategy_id: Optional[int] = None

class PositionOut(PositionBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True

# --- V2.0 TradeStrategy Schemas ---
class TradeStrategyBase(BaseModel):
    name: str = Field(..., description="策略名称", max_length=100)
    stop_loss_pct: float = Field(-0.05, description="绝对止损比例 (如 -0.05)")
    take_profit_drawdown_pct: float = Field(-0.08, description="回撤止盈比例 (如 -0.08)")
    description: Optional[str] = Field(None, description="策略描述", max_length=500)

class TradeStrategyCreate(TradeStrategyBase):
    pass

class TradeStrategyUpdate(BaseModel):
    name: Optional[str] = Field(None, description="策略名称", max_length=100)
    stop_loss_pct: Optional[float] = None
    take_profit_drawdown_pct: Optional[float] = None
    description: Optional[str] = Field(None, description="策略描述", max_length=500)

class TradeStrategyOut(TradeStrategyBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True

class MarketStatusOut(BaseModel):
    sh_status: str = Field(..., description="上证状态: red(线上) / green(破位)")
    sz_status: str = Field(..., description="深证状态: red(线上) / green(破位)")
    sh_reason: str = Field(..., description="上证判定理由与均线数值")
    sz_reason: str = Field(..., description="深证判定理由与均线数值")
    update_time: datetime = Field(default_factory=datetime.now)

class SectorItemOut(BaseModel):
    sector_name: str = Field(..., description="板块名称")
    pct_change: float = Field(..., description="板块涨跌幅")

class MarketThermometerOut(BaseModel):
    total_stocks: int = Field(..., description="全市场股票总数")
    up_count: int = Field(..., description="上涨家数")
    down_count: int = Field(..., description="下跌家数")
    flat_count: int = Field(0, description="平盘家数")
    limit_up_count: int = Field(..., description="涨停家数")
    limit_down_count: int = Field(..., description="跌停家数")
    score: int = Field(..., description="赚钱效应打分 0-100")
    temperature: str = Field("", description="市场温度描述")
    median_pct_change: float = Field(0.0, description="中位数涨跌幅")
    top_sectors: list[SectorItemOut] = Field(default_factory=list, description="领涨板块")
    update_time: datetime = Field(default_factory=datetime.now)

class STListOut(BaseModel):
    count: int = Field(..., description="ST 股票总数")
    stocks: list[str] = Field(..., description="ST 股票代码列表")
    update_time: datetime = Field(default_factory=datetime.now)

# --- V2.0 Trading Journal Schemas ---
class TradingJournalBase(BaseModel):
    record_date: date = Field(..., description="日记日期")
    execution_score: int = Field(..., description="执行力打分 (1-10分)", ge=1, le=10)
    notes: str = Field(..., description="今日复盘与操作日记", max_length=2000)
    mistakes_made: Optional[str] = Field(None, description="今日主观犯错记录与反思", max_length=1000)
    emotions: Optional[str] = Field(None, description="情绪或心理状态捕捉", max_length=1000)

class TradingJournalCreate(TradingJournalBase):
    pass

class TradingJournalUpdate(BaseModel):
    execution_score: Optional[int] = Field(None, description="执行力打分", ge=1, le=10)
    notes: Optional[str] = Field(None, description="笔记内容", max_length=2000)
    mistakes_made: Optional[str] = Field(None, description="犯错反思", max_length=1000)
    emotions: Optional[str] = Field(None, description="情绪捕捉", max_length=1000)

class TradingJournalOut(TradingJournalBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

# --- V2.0 User Trade Settings Schemas ---
class UserTradeSettingsUpdate(BaseModel):
    total_capital: Optional[float] = Field(None, description="计划用于量化的总资本(本金)", ge=1000.0)
    commission_rate: Optional[float] = Field(None, description="券商佣金费率(如0.00025代表万2.5)", ge=0.0)
    min_commission: Optional[float] = Field(None, description="单笔最低佣金", ge=0.0)
    stamp_duty_rate: Optional[float] = Field(None, description="印花税费率(如0.0005代表千0.5)", ge=0.0)

class UserTradeSettingsOut(BaseModel):
    id: int
    user_id: int
    total_capital: float
    commission_rate: float
    min_commission: float
    stamp_duty_rate: float
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class WatchlistBase(BaseModel):
    symbol: str = Field(..., description="股票代码", max_length=20)
    name: str = Field(..., description="股票名称", max_length=100)
    reason: Optional[str] = Field(None, description="加入理由", max_length=500)
    monitor_status: str = Field("active", description="监控状态: active(监控中), inactive(暂停)", max_length=20)

class WatchlistCreate(WatchlistBase):
    pass

class WatchlistUpdate(BaseModel):
    reason: Optional[str] = None
    monitor_status: Optional[str] = None

class WatchlistOut(WatchlistBase):
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


# --- V2.0 Trade Transaction B/S Schemas ---
class TradeTransactionBase(BaseModel):
    action: str = Field(..., description="操作类型: buy 或 sell")
    price: float = Field(..., description="交易价格", gt=0)
    quantity: int = Field(..., description="交易数量", gt=0)

class TradeTransactionCreate(TradeTransactionBase):
    pass

class TradeTransactionOut(TradeTransactionBase):
    id: int
    position_id: int
    fee: float
    transaction_time: datetime
    created_at: datetime
    
    class Config:
        from_attributes = True

# --- V2.0 Sector Rotation Schemas ---
class SectorBoardItem(BaseModel):
    rank: int = Field(..., description="排名")
    name: str = Field(..., description="板块名称")
    code: str = Field(..., description="板块代码")
    latest_price: float = Field(..., description="最新价")
    pct_change: float = Field(..., description="涨跌幅")
    turnover_rate: float = Field(..., description="换手率")
    advance_count: int = Field(..., description="上涨家数")
    decline_count: int = Field(..., description="下跌家数")
    top_stock_name: str = Field(..., description="领涨股票")
    top_stock_pct: float = Field(..., description="领涨股票-涨跌幅")

