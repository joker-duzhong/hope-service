import uuid
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field


class AuthorSchema(BaseModel):
    user_id: uuid.UUID = Field(..., description="作者用户 ID")
    nickname: Optional[str] = Field(default=None, description="作者昵称")
    avatar: Optional[str] = Field(default=None, description="作者头像 URL")

    class Config:
        from_attributes = True


class GalleryItemSchema(BaseModel):
    id: uuid.UUID = Field(..., description="作品 ID")
    thumb_url: str = Field(..., description="缩略图 URL")
    aspect_ratio: str = Field(..., description="作品宽高比")
    author: AuthorSchema = Field(..., description="作者信息")
    like_count: int = Field(..., description="点赞数")
    is_liked: bool = Field(default=False, description="当前登录用户是否已点赞，未登录时为 false")
    view_count: int = Field(..., description="浏览量")

    class Config:
        from_attributes = True


class GalleryDetailSchema(GalleryItemSchema):
    image_url: str = Field(..., description="原图 URL")
    prompt: str = Field(..., description="生成作品使用的提示词")
    model_name: str = Field(..., description="生成作品使用的模型名称")


class GalleryCategorySchema(BaseModel):
    id: uuid.UUID = Field(..., description="分类 ID，可用于 gallery/list 的 categoryId 筛选")
    name: str = Field(..., description="分类名称")
    sort: int = Field(..., description="排序权重，数值越大越靠前")

    class Config:
        from_attributes = True


class TaskGenerateRequest(BaseModel):
    prompt: str = Field(..., description="生图提示词")
    model_name: str = Field(..., description="模型 ID 或模型名称")
    aspect_ratio: str = Field(..., description="图片宽高比，如 1:1、16:9")


class TaskGenerateResponse(BaseModel):
    task_id: uuid.UUID
    frozen_points: int = 0
    balance_after: int


class TaskStatusResponse(BaseModel):
    task_id: uuid.UUID
    status: str
    progress: int
    image_url: Optional[str] = None
    failed_reason: Optional[str] = None


class DictModelOption(BaseModel):
    model_id: str = Field(..., description="模型 ID")
    name: str = Field(..., description="模型展示名称")
    cost: int = Field(..., description="单次生成消耗算力")
    is_vip_only: bool = Field(default=False, description="是否仅 VIP 可用")

    class Config:
        from_attributes = True


class TaskOptionsResponse(BaseModel):
    models: List[DictModelOption]
    aspect_ratios: List[str]


class UserProfileResponse(BaseModel):
    user_id: uuid.UUID
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    balance: int
    is_vip: bool = False
    type: str = "普通会员"
    vip_expire_time: Optional[int] = None # timestamp


class AssetLogItem(BaseModel):
    id: uuid.UUID
    type: int
    amount: int
    balance_after: int
    description: str

    class Config:
        from_attributes = True


class ProductItem(BaseModel):
    id: uuid.UUID
    type: str
    name: str
    price: int
    original_price: Optional[int] = None
    point_amount: int = 0
    bonus_amount: int = 0
    tag: Optional[str] = None

    class Config:
        from_attributes = True


class OrderCreateRequest(BaseModel):
    product_id: uuid.UUID
    openid: str


class OrderCreateResponse(BaseModel):
    order_no: str
    pay_params: dict


class OrderStatusResponse(BaseModel):
    order_no: str
    status: str


class TaskHistoryItem(BaseModel):
    task_id: uuid.UUID
    image_url: Optional[str] = None
    prompt: str
    status: str
    cost: int


class InviteInfoResponse(BaseModel):
    invite_code: str
    invited_count: int
    total_reward_points: int
    rule_text: str


class BindInviteRequest(BaseModel):
    invite_code: str


class BindInviteResponse(BaseModel):
    is_success: bool
    reward_points: int


class SignInResponse(BaseModel):
    reward_points: int
    continuous_days: int


# ================= Admin Schemas =================


class AdminStatsResponse(BaseModel):
    today_new_users: int
    today_active_users: int
    today_generations: int
    today_revenue: int
    revenue_growth_rate: float


class AdminAdjustBalanceRequest(BaseModel):
    user_id: uuid.UUID
    amount: int
    remark: Optional[str] = None


class AdminAdjustBalanceResponse(BaseModel):
    is_success: bool
    balance_after: int


class AdminUserStatusUpdate(BaseModel):
    status: str


class AdminUserListItem(BaseModel):
    user_id: uuid.UUID
    username: str
    email: Optional[str] = None
    is_active: bool
    created_at: datetime


class AdminUserDetail(BaseModel):
    user_id: uuid.UUID
    username: str
    email: Optional[str] = None
    phone: Optional[str] = None
    balance: int
    is_active: bool
    is_vip: bool
    vip_type: Optional[str] = None
    vip_expire_time: Optional[int] = None
    invite_code: str
    invited_count: int
    total_reward_points: int
    created_at: datetime


class AdminRefundRequest(BaseModel):
    remark: Optional[str] = None


class AdminRefundResponse(BaseModel):
    is_success: bool
    refund_id: Optional[str] = None
    deducted_points: int


class AdminHistoryListItem(BaseModel):
    task_id: uuid.UUID
    user_id: uuid.UUID
    image_url: Optional[str] = None
    prompt: str
    status: str
    cost: int
    created_at: datetime


class AdminGalleryCategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    sort: int

    class Config:
        from_attributes = True


class AdminGalleryCategoryCreate(BaseModel):
    name: str
    sort: int = 0


class AdminOptionModelResponse(BaseModel):
    id: uuid.UUID
    model_id: str
    name: str
    cost: int
    is_vip_only: bool
    status: str

    class Config:
        from_attributes = True


class AdminOptionModelCreate(BaseModel):
    model_id: str
    name: str
    cost: int
    is_vip_only: bool = False
    status: str = "on"


class AdminOptionRatioResponse(BaseModel):
    id: uuid.UUID
    ratio: str
    sort: int
    status: str

    class Config:
        from_attributes = True


class AdminOptionRatioCreate(BaseModel):
    ratio: str
    sort: int = 0
    status: str = "on"


class AdminProductBase(BaseModel):
    type: str = Field(..., description="point_pack 或 vip")
    name: str
    price: int = Field(..., description="价格(分)")
    original_price: Optional[int] = None
    point_amount: int = 0
    bonus_amount: int = 0
    tag: Optional[str] = None


class AdminProductCreate(AdminProductBase):
    pass


class AdminProductUpdate(BaseModel):
    type: Optional[str] = None
    name: Optional[str] = None
    price: Optional[int] = None
    original_price: Optional[int] = None
    point_amount: Optional[int] = None
    bonus_amount: Optional[int] = None
    tag: Optional[str] = None


class AdminProductResponse(AdminProductBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True

