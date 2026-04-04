"""
Nest Talk Schemas - 语筑智能房产顾问
"""
from typing import Optional, List
from datetime import date, datetime
from pydantic import BaseModel, Field


# ==================== 房源 Schemas ====================
class HouseSearchRequest(BaseModel):
    """房源搜索请求"""
    budget_min: Optional[float] = Field(None, description="最低预算(万元)")
    budget_max: Optional[float] = Field(None, description="最高预算(万元)")
    area_min: Optional[float] = Field(None, description="最小面积(㎡)")
    area_max: Optional[float] = Field(None, description="最大面积(㎡)")
    rooms: Optional[int] = Field(None, description="居室数量")
    regions: Optional[List[str]] = Field(None, description="目标区域列表")
    floor_min: Optional[int] = Field(None, description="最低楼层")
    floor_max: Optional[int] = Field(None, description="最高楼层")
    exclude_top_floor: Optional[bool] = Field(None, description="排除顶楼")
    exclude_ground_floor: Optional[bool] = Field(None, description="排除底楼")
    orientations: Optional[List[str]] = Field(None, description="朝向偏好")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")


class HouseOut(BaseModel):
    """房源输出"""
    id: int
    house_id: str
    title: str
    total_price: float
    unit_price: float
    area: float
    layout: Optional[str] = None
    rooms: Optional[int] = None
    floor: Optional[int] = None
    total_floors: Optional[int] = None
    orientation: Optional[str] = None
    decoration: Optional[str] = None
    region_name: Optional[str] = None
    community_name: Optional[str] = None
    source: Optional[str] = None
    url: Optional[str] = None
    image_url: Optional[str] = None
    is_bargain: bool = False
    bargain_reason: Optional[str] = None
    discount_rate: Optional[float] = None
    community_avg_price: Optional[float] = None
    status: str = "active"
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class HouseDetailOut(HouseOut):
    """房源详情输出"""
    community_avg_price: Optional[float] = Field(None, description="小区均价")


class BargainHouseOut(HouseOut):
    """捡漏房源输出"""
    save_amount: Optional[float] = Field(None, description="节省金额(万元)")


# ==================== 用户偏好 Schemas ====================
class UserPreferenceBase(BaseModel):
    """用户偏好基础"""
    # 预算
    budget_min: Optional[float] = Field(None, description="最低预算(万元)")
    budget_max: Optional[float] = Field(None, description="最高预算(万元)")
    # 面积
    area_min: Optional[float] = Field(None, description="最小面积(㎡)")
    area_max: Optional[float] = Field(None, description="最大面积(㎡)")
    # 居室
    rooms_min: Optional[int] = Field(None, description="最少居室数")
    rooms_max: Optional[int] = Field(None, description="最多居室数")
    # 区域偏好
    preferred_regions: Optional[str] = Field(None, description="偏好区域(逗号分隔)")
    # 楼层偏好
    exclude_top_floor: bool = Field(False, description="排除顶楼")
    exclude_ground_floor: bool = Field(False, description="排除底楼")
    floor_min: Optional[int] = Field(None, description="最低楼层")
    floor_max: Optional[int] = Field(None, description="最高楼层")
    # 朝向偏好
    preferred_orientations: Optional[str] = Field(None, description="偏好朝向(逗号分隔)")
    # 捡漏设置
    bargain_enabled: bool = Field(False, description="启用捡漏推送")
    bargain_threshold: float = Field(0.9, description="捡漏折扣阈值")
    # 通知设置
    notify_endpoint: Optional[str] = Field(None, description="推送接收地址")


class UserPreferenceCreate(UserPreferenceBase):
    """创建用户偏好"""
    pass


class UserPreferenceUpdate(BaseModel):
    """更新用户偏好"""
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    rooms_min: Optional[int] = None
    rooms_max: Optional[int] = None
    preferred_regions: Optional[str] = None
    exclude_top_floor: Optional[bool] = None
    exclude_ground_floor: Optional[bool] = None
    floor_min: Optional[int] = None
    floor_max: Optional[int] = None
    preferred_orientations: Optional[str] = None
    bargain_enabled: Optional[bool] = None
    bargain_threshold: Optional[float] = None
    notify_endpoint: Optional[str] = None


class UserPreferenceOut(UserPreferenceBase):
    """用户偏好输出"""
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== AI 对话 Schemas ====================
class ChatRequest(BaseModel):
    """对话请求"""
    session_id: Optional[str] = Field(None, description="会话ID(首次对话不传)")
    message: str = Field(..., description="用户消息", min_length=1, max_length=2000)


class ExtractedRequirements(BaseModel):
    """提取的购房需求"""
    budget_min: Optional[float] = None
    budget_max: Optional[float] = None
    area_min: Optional[float] = None
    area_max: Optional[float] = None
    rooms: Optional[int] = None
    regions: Optional[List[str]] = None
    exclude_top_floor: Optional[bool] = None
    exclude_ground_floor: Optional[bool] = None
    floor_min: Optional[int] = None
    floor_max: Optional[int] = None
    orientations: Optional[List[str]] = None


class ChatResponse(BaseModel):
    """对话响应"""
    session_id: str = Field(..., description="会话ID")
    response_type: str = Field(..., description="响应类型: clarification(追问) / results(结果)")
    message: str = Field(..., description="AI回复消息")
    houses: Optional[List[HouseOut]] = Field(None, description="匹配的房源列表(仅results类型)")
    requirements: Optional[ExtractedRequirements] = Field(None, description="当前提取的需求")


class ChatClearResponse(BaseModel):
    """清除会话响应"""
    session_id: str
    message: str = "会话已清除"


# ==================== 报表 Schemas ====================
class DailyReportOut(BaseModel):
    """每日报表输出"""
    id: int
    report_date: date
    region_name: Optional[str] = None
    report_type: str
    image_url: str
    summary: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ReportListRequest(BaseModel):
    """报表列表请求"""
    region: Optional[str] = Field(None, description="区域名称")
    days: int = Field(7, ge=1, le=30, description="查询天数")


# ==================== 区域 Schemas ====================
class RegionOut(BaseModel):
    """区域输出"""
    id: int
    name: str
    code: Optional[str] = None
    is_active: bool

    class Config:
        from_attributes = True


class RegionPriceLogOut(BaseModel):
    """区域均价历史输出"""
    id: int
    region_name: str
    record_date: date
    average_price: float
    change_rate: Optional[float] = None

    class Config:
        from_attributes = True


# ==================== 统计 Schemas ====================
class HouseStatistics(BaseModel):
    """房源统计"""
    total_count: int = Field(..., description="房源总数")
    bargain_count: int = Field(..., description="捡漏房源数")
    avg_price: float = Field(..., description="平均单价")
    avg_area: float = Field(..., description="平均面积")


class PriceDistribution(BaseModel):
    """价格区间分布"""
    price_range: str = Field(..., description="价格区间(如: 100-150)")
    count: int = Field(..., description="房源数量")
    percentage: float = Field(..., description="占比")
