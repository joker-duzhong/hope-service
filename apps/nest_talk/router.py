"""
Nest Talk Router - 语筑智能房产顾问
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel, PaginatedResponse, PaginatedData
from core.users.models import User
from core.users.dependencies import get_current_user
from core.dependencies import get_app_key

from apps.nest_talk.schemas import (
    HouseSearchRequest,
    HouseOut,
    HouseDetailOut,
    BargainHouseOut,
    UserPreferenceCreate,
    UserPreferenceUpdate,
    UserPreferenceOut,
    ChatRequest,
    ChatResponse,
    ChatClearResponse,
    DailyReportOut,
    ReportListRequest,
    RegionOut,
    HouseStatistics,
    PriceDistribution,
)
from apps.nest_talk.services import (
    HouseService,
    UserPreferenceService,
    ChatService,
    ReportService,
    RegionService,
)

# 路由级别依赖：所有接口都必须传入有效的 app header
router = APIRouter(dependencies=[Depends(get_app_key)])


# ==================== AI 对话 ====================

@router.post("/chat", response_model=ResponseModel[ChatResponse])
async def chat(
    data: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    AI 智能对话

    与用户进行购房咨询对话，自动提取需求并推荐房源。

    - **session_id**: 会话ID（首次对话不传，后续对话传入以保持上下文）
    - **message**: 用户消息
    """
    response = await ChatService.process_chat(db, current_user.id, data)
    return ResponseModel(data=response)


@router.post("/chat/clear", response_model=ResponseModel[ChatClearResponse])
async def clear_chat(
    session_id: str = Query(..., description="要清除的会话ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    清除对话会话

    清除指定的会话，开始新的对话。
    """
    success = await ChatService.clear_session(db, current_user.id, session_id)
    if not success:
        return ResponseModel(
            code=404,
            message="会话不存在或无权限",
            data=None
        )
    return ResponseModel(data=ChatClearResponse(session_id=session_id))


# ==================== 房源管理 ====================

@router.post("/houses/search", response_model=PaginatedResponse[HouseOut])
async def search_houses(
    data: HouseSearchRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    搜索房源

    根据多条件搜索房源，支持分页。

    - **budget_min/budget_max**: 预算范围（万元）
    - **area_min/area_max**: 面积范围（㎡）
    - **rooms**: 居室数量
    - **regions**: 目标区域列表
    - **floor_min/floor_max**: 楼层范围
    - **exclude_top_floor**: 排除顶楼
    - **exclude_ground_floor**: 排除底楼
    - **orientations**: 朝向偏好
    """
    houses, total = await HouseService.search_houses(db, data)

    total_pages = (total + data.page_size - 1) // data.page_size

    return PaginatedResponse(
        data=PaginatedData(
            items=[HouseOut.model_validate(h) for h in houses],
            total=total,
            page=data.page,
            page_size=data.page_size,
            total_pages=total_pages
        )
    )


@router.get("/houses/{house_id}", response_model=ResponseModel[HouseDetailOut])
async def get_house_detail(
    house_id: int = Path(..., description="房源ID"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取房源详情

    根据房源ID获取详细信息，包括小区均价对比。
    """
    house = await HouseService.get_house_by_id(db, house_id)
    if not house:
        return ResponseModel(
            code=404,
            message="房源不存在",
            data=None
        )
    return ResponseModel(data=HouseDetailOut.model_validate(house))


@router.get("/houses/bargain/list", response_model=PaginatedResponse[BargainHouseOut])
async def list_bargain_houses(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取捡漏房源列表

    获取当前标记为捡漏的高性价比房源。
    """
    houses, total = await HouseService.search_houses(
        db,
        HouseSearchRequest(page=page, page_size=page_size)
    )
    # 过滤捡漏房源
    bargain_houses = [h for h in houses if h.is_bargain]

    total_pages = (len(bargain_houses) + page_size - 1) // page_size

    # 计算节省金额
    result_houses = []
    for h in bargain_houses:
        house_out = BargainHouseOut.model_validate(h)
        if h.community_avg_price and h.unit_price:
            # 节省金额 = (小区均价 - 当前单价) * 面积 / 10000 (转换为万元)
            save_amount = (h.community_avg_price - h.unit_price) * h.area / 10000
            house_out.save_amount = round(save_amount, 2)
        result_houses.append(house_out)

    return PaginatedResponse(
        data=PaginatedData(
            items=result_houses,
            total=len(bargain_houses),
            page=page,
            page_size=page_size,
            total_pages=total_pages or 1
        )
    )


# ==================== 用户偏好 ====================

@router.post("/user/preferences", response_model=ResponseModel[UserPreferenceOut])
async def create_user_preferences(
    data: UserPreferenceCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建用户购房偏好

    首次设置用户的购房偏好。
    """
    try:
        preference = await UserPreferenceService.create_preference(
            db, current_user.id, data
        )
        return ResponseModel(data=UserPreferenceOut.model_validate(preference))
    except ValueError as e:
        return ResponseModel(code=400, message=str(e), data=None)


@router.get("/user/preferences", response_model=ResponseModel[UserPreferenceOut])
async def get_user_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    获取用户购房偏好

    获取当前用户的购房偏好设置。
    """
    preference = await UserPreferenceService.get_preference(db, current_user.id)
    if not preference:
        return ResponseModel(
            code=404,
            message="用户偏好不存在",
            data=None
        )
    return ResponseModel(data=UserPreferenceOut.model_validate(preference))


@router.put("/user/preferences", response_model=ResponseModel[UserPreferenceOut])
async def update_user_preferences(
    data: UserPreferenceUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    更新用户购房偏好

    更新当前用户的购房偏好设置。
    """
    preference = await UserPreferenceService.update_preference(
        db, current_user.id, data
    )
    if not preference:
        return ResponseModel(
            code=404,
            message="用户偏好不存在",
            data=None
        )
    return ResponseModel(data=UserPreferenceOut.model_validate(preference))


@router.delete("/user/preferences", response_model=ResponseModel[bool])
async def delete_user_preferences(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    删除用户购房偏好

    删除当前用户的购房偏好设置（软删除）。
    """
    success = await UserPreferenceService.delete_preference(db, current_user.id)
    if not success:
        return ResponseModel(
            code=404,
            message="用户偏好不存在",
            data=False
        )
    return ResponseModel(data=True)


# ==================== 报表服务 ====================

@router.get("/reports/daily", response_model=ResponseModel[DailyReportOut])
async def get_daily_report(
    region: Optional[str] = Query(None, description="区域名称（空表示全局报表）"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取每日行情报表

    获取当日或最近的市场行情报表图片。
    """
    report = await ReportService.get_daily_report(db, region)
    if not report:
        return ResponseModel(
            code=404,
            message="暂无报表数据",
            data=None
        )
    return ResponseModel(data=DailyReportOut.model_validate(report))


@router.get("/reports/list", response_model=ResponseModel[List[DailyReportOut]])
async def list_reports(
    region: Optional[str] = Query(None, description="区域名称"),
    days: int = Query(7, ge=1, le=30, description="查询天数"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取报表列表

    获取历史报表列表。
    """
    reports = await ReportService.list_reports(db, region, days)
    return ResponseModel(
        data=[DailyReportOut.model_validate(r) for r in reports]
    )


# ==================== 区域服务 ====================

@router.get("/regions", response_model=ResponseModel[List[RegionOut]])
async def list_regions(
    active_only: bool = Query(True, description="仅返回启用的区域"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取区域列表

    获取所有支持的区域列表。
    """
    regions = await RegionService.list_regions(db, active_only)
    return ResponseModel(
        data=[RegionOut.model_validate(r) for r in regions]
    )


# ==================== 统计服务 ====================

@router.get("/statistics/houses", response_model=ResponseModel[HouseStatistics])
async def get_house_statistics(
    region: Optional[str] = Query(None, description="区域名称（空表示全局统计）"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取房源统计

    获取房源总数、捡漏数、平均价格等统计数据。
    """
    stats = await HouseService.get_house_statistics(db, region)
    return ResponseModel(data=stats)


@router.get("/statistics/price-distribution", response_model=ResponseModel[List[PriceDistribution]])
async def get_price_distribution(
    region: Optional[str] = Query(None, description="区域名称（空表示全局分布）"),
    db: AsyncSession = Depends(get_db)
):
    """
    获取价格区间分布

    获取房源价格区间的分布统计。
    """
    distribution = await HouseService.get_price_distribution(db, region)
    return ResponseModel(data=distribution)
