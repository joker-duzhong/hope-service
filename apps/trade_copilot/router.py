from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel
from core.users.models import User
from core.users.dependencies import get_current_user

from apps.trade_copilot.schemas import (
    PositionCreate, PositionUpdate, PositionOut,
    MarketStatusOut, STListOut, MarketThermometerOut,
    WatchlistCreate, WatchlistUpdate, WatchlistOut,
    TradeStrategyCreate, TradeStrategyUpdate, TradeStrategyOut,
    TradingJournalCreate, TradingJournalUpdate, TradingJournalOut,
    UserTradeSettingsOut, UserTradeSettingsUpdate,
    TradeTransactionCreate, TradeTransactionOut
)
from apps.trade_copilot.services import PositionService, MarketService, WatchlistService, TradeStrategyService, TradingJournalService, UserTradeSettingsService, TradeTransactionService, send_feishu_alert

router = APIRouter()

@router.post("/feishu-test", response_model=ResponseModel[bool])
async def test_feishu_webhook(title: str = Query("测试标题", description="标题"), msg: str = Query("测试内容", description="内容")):
    """Task 1.1 飞书 Webhook 联调接口"""
    success = await send_feishu_alert(title, msg)
    if success:
        return ResponseModel(data=True, message="推送成功")
    return ResponseModel(code=500, data=False, message="推送失败")


@router.post("/positions", response_model=ResponseModel[PositionOut])
async def create_position(
    data: PositionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """新增持仓"""
    position = await PositionService.create_position(db, current_user.id, data)
    return ResponseModel(data=position)


@router.get("/positions", response_model=ResponseModel[List[PositionOut]])
async def list_positions(
    status: Optional[str] = Query(None, description="状态筛选 (holding/closed)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """查询持仓列表"""
    positions = await PositionService.list_positions(db, current_user.id, status)
    return ResponseModel(data=positions)


@router.put("/positions/{position_id}", response_model=ResponseModel[PositionOut])
async def update_position(
    data: PositionUpdate,
    position_id: int = Path(..., description="持仓ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新持仓（如标记卖出，更新高水位）"""
    position = await PositionService.update_position(db, current_user.id, position_id, data)
    if not position:
        return ResponseModel(code=404, message="持仓不存在或无权限", data=None)
    return ResponseModel(data=position)


@router.delete("/positions/{position_id}", response_model=ResponseModel[bool])
async def delete_position(
    position_id: int = Path(..., description="持仓ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """逻辑删除持仓"""
    success = await PositionService.delete_position(db, current_user.id, position_id)
    if not success:
        return ResponseModel(code=404, message="持仓不存在或无权限", data=False)
    return ResponseModel(data=True)

@router.get("/market/status", response_model=ResponseModel[MarketStatusOut])
async def get_market_status():
    """获取大盘红绿灯状态 (取缓存/实时)"""
    data = await MarketService.get_market_status()
    return ResponseModel(data=data)

@router.get("/market/thermometer", response_model=ResponseModel[MarketThermometerOut])
async def get_market_thermometer():
    """获取全市场赚钱效应打分、情绪冰点高潮周期及板块轮动温度 (取缓存/实时)"""
    data = await MarketService.get_market_thermometer()
    return ResponseModel(data=data)

@router.get("/market/st-list", response_model=ResponseModel[STListOut])
async def get_market_st_list():
    """获取全市场 ST 股票黑名单 (用于排雷)"""
    data = await MarketService.get_st_list()
    return ResponseModel(data=data)

# ================= 观察池 =================

@router.post("/watchlist", response_model=ResponseModel[WatchlistOut])
async def create_watchlist(
    data: WatchlistCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """新增观察池股票"""
    try:
        watchlist = await WatchlistService.create_watchlist(db, current_user.id, data)
        return ResponseModel(data=watchlist)
    except ValueError as e:
        return ResponseModel(code=400, message=str(e), data=None)

@router.get("/watchlist", response_model=ResponseModel[List[WatchlistOut]])
async def list_watchlist(
    status: Optional[str] = Query(None, description="状态筛选 (active/inactive)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """查询观察池列表"""
    watchlists = await WatchlistService.list_watchlist(db, current_user.id, status)
    return ResponseModel(data=watchlists)

@router.put("/watchlist/{watchlist_id}", response_model=ResponseModel[WatchlistOut])
async def update_watchlist(
    data: WatchlistUpdate,
    watchlist_id: int = Path(..., description="观察记录ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新观察池记录"""
    watchlist = await WatchlistService.update_watchlist(db, current_user.id, watchlist_id, data)
    if not watchlist:
        return ResponseModel(code=404, message="记录不存在或无权限", data=None)
    return ResponseModel(data=watchlist)

@router.delete("/watchlist/{watchlist_id}", response_model=ResponseModel[bool])
async def delete_watchlist(
    watchlist_id: int = Path(..., description="观察记录ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """逻辑删除观察池记录"""
    success = await WatchlistService.delete_watchlist(db, current_user.id, watchlist_id)
    if not success:
        return ResponseModel(code=404, message="记录不存在或无权限", data=False)
    return ResponseModel(data=True)
# ================= 交易策略 V2.0 =================

@router.post("/strategies", response_model=ResponseModel[TradeStrategyOut])
async def create_strategy(
    data: TradeStrategyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建策略"""
    strategy = await TradeStrategyService.create_strategy(db, current_user.id, data)
    return ResponseModel(data=strategy)

@router.get("/strategies", response_model=ResponseModel[List[TradeStrategyOut]])
async def list_strategies(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取策略列表"""
    strategies = await TradeStrategyService.list_strategies(db, current_user.id)
    return ResponseModel(data=strategies)

@router.put("/strategies/{strategy_id}", response_model=ResponseModel[TradeStrategyOut])
async def update_strategy(
    data: TradeStrategyUpdate,
    strategy_id: int = Path(..., description="策略ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新交易策略"""
    strategy = await TradeStrategyService.update_strategy(db, current_user.id, strategy_id, data)
    if not strategy:
        return ResponseModel(code=404, message="策略不存在或无权限", data=None)
    return ResponseModel(data=strategy)

@router.delete("/strategies/{strategy_id}", response_model=ResponseModel[bool])
async def delete_strategy(
    strategy_id: int = Path(..., description="策略ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除交易策略"""
    success = await TradeStrategyService.delete_strategy(db, current_user.id, strategy_id)
    if not success:
        return ResponseModel(code=404, message="策略不存在或无权限", data=False)
    return ResponseModel(data=True)

# ================= 交易日记 V2.0 =================

@router.post("/journals", response_model=ResponseModel[TradingJournalOut])
async def create_journal(
    data: TradingJournalCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建交易日记"""
    try:
        journal = await TradingJournalService.create_journal(db, current_user.id, data)
        return ResponseModel(data=journal)
    except ValueError as e:
        return ResponseModel(code=400, message=str(e), data=None)

@router.get("/journals", response_model=ResponseModel[List[TradingJournalOut]])
async def list_journals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取交易日记列表"""
    journals = await TradingJournalService.list_journals(db, current_user.id)
    return ResponseModel(data=journals)

@router.put("/journals/{journal_id}", response_model=ResponseModel[TradingJournalOut])
async def update_journal(
    data: TradingJournalUpdate,
    journal_id: int = Path(..., description="日记ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新交易日记"""
    journal = await TradingJournalService.update_journal(db, current_user.id, journal_id, data)
    if not journal:
        return ResponseModel(code=404, message="日记不存在或无权限", data=None)
    return ResponseModel(data=journal)

# ================= 仓位管理 V2.0 =================

@router.get("/settings/capital", response_model=ResponseModel[UserTradeSettingsOut])
async def get_user_capital_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取用户资金设置"""
    settings = await UserTradeSettingsService.get_settings(db, current_user.id)
    return ResponseModel(data=settings)

@router.put("/settings/capital", response_model=ResponseModel[UserTradeSettingsOut])
async def update_user_capital_settings(
    data: UserTradeSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新用户资金设置"""
    settings = await UserTradeSettingsService.update_settings(db, current_user.id, data)
    return ResponseModel(data=settings)

# ================= 交易流水 (BS 记录) V2.0 =================

@router.post("/positions/{position_id}/transactions", response_model=ResponseModel[TradeTransactionOut])
async def add_trade_transaction(
    data: TradeTransactionCreate,
    position_id: int = Path(..., description="日记ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    添加交易流水
    """
    try:
        txn = await TradeTransactionService.add_transaction(db, current_user.id, position_id, data)
        return ResponseModel(data=txn)
    except ValueError as e:
        return ResponseModel(code=400, message=str(e), data=None)

@router.get("/positions/{position_id}/transactions", response_model=ResponseModel[List[TradeTransactionOut]])
async def list_trade_transactions(
    position_id: int = Path(..., description="持仓ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取交易流水列表"""
    txns = await TradeTransactionService.get_transactions(db, current_user.id, position_id)
    return ResponseModel(data=txns)

