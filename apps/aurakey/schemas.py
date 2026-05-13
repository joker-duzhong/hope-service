import uuid
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field, field_validator


VALID_PRODUCT_TYPES = {"point_pack", "vip"}
VALID_PUBLISH_STATUSES = {"approved", "blocked"}


def _validate_product_type(value: str) -> str:
    if value not in VALID_PRODUCT_TYPES:
        raise ValueError("商品类型必须是 point_pack 或 vip")
    return value


def _validate_publish_status(value: str) -> str:
    if value not in VALID_PUBLISH_STATUSES:
        raise ValueError("发布审核状态必须是 approved 或 blocked")
    return value


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
    prompt: str = Field(..., description="生成作品使用的提示词")

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


class TaskStreamGenerateRequest(TaskGenerateRequest):
    is_public: bool = Field(default=False, description="是否公开到画廊，true 时生成成功后自动发布")
    category_id: Optional[uuid.UUID] = Field(default=None, description="公开到画廊时使用的分类 ID")


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
    openid: Optional[str] = None
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    phone: Optional[str] = None
    balance: int
    is_vip: bool = False
    type: str = "普通会员"
    vip_expire_time: Optional[int] = None # timestamp
    vip_level: int = 0


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
    vip_type: Optional[str] = None
    vip_level: int = 0
    valid_days: Optional[int] = None

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


class UserEntitlementResponse(BaseModel):
    vip_expire_time: Optional[int] = None
    remaining_points: int
    is_vip: bool
    vip_type: str = "普通会员"
    vip_level: int = 0


class PurchaseOrderItem(BaseModel):
    order_no: str
    status: str
    amount: int
    pay_method: str
    product_id: uuid.UUID
    product_name: str
    product_type: str
    point_amount: int = 0
    bonus_amount: int = 0
    granted_points: int = 0
    remaining_points: int = 0
    vip_type: Optional[str] = None
    vip_level: int = 0
    valid_days: Optional[int] = None
    entitlement_start_at: Optional[int] = None
    entitlement_expire_at: Optional[int] = None
    created_at: int
    paid_at: Optional[int] = None
    is_effective: bool = False


class AurakeySystemConfigResponse(BaseModel):
    register_reward_points: int = 10
    daily_sign_in_reward_points: int = 10
    invite_reward_points: int = 50
    default_vip_valid_days: int = 30
    default_point_pack_valid_days: Optional[int] = None
    daily_free_points_reset_hour: int = 12
    custom: dict[str, Any] = Field(default_factory=dict)


class AurakeySystemConfigUpdate(BaseModel):
    register_reward_points: Optional[int] = None
    daily_sign_in_reward_points: Optional[int] = None
    invite_reward_points: Optional[int] = None
    default_vip_valid_days: Optional[int] = None
    default_point_pack_valid_days: Optional[int] = None
    daily_free_points_reset_hour: Optional[int] = None
    custom: Optional[dict[str, Any]] = None


class TaskHistoryItem(BaseModel):
    task_id: uuid.UUID
    image_url: Optional[str] = None
    prompt: str
    status: str
    cost: int
    is_published: bool = False
    publish_status: str = "approved"
    category_id: Optional[uuid.UUID] = None


class TaskPublishUpdateRequest(BaseModel):
    is_published: bool = Field(..., description="是否公开作品")
    category_id: Optional[uuid.UUID] = Field(default=None, description="公开作品时可指定画廊分类 ID")


class TaskPublishStateResponse(BaseModel):
    task_id: uuid.UUID
    is_published: bool
    publish_status: str
    category_id: Optional[uuid.UUID] = None
    published_at: Optional[int] = None


class AdminTaskPublishUpdateRequest(TaskPublishUpdateRequest):
    pass


class AdminTaskPublishBatchUpdateRequest(BaseModel):
    task_ids: List[uuid.UUID] = Field(..., min_length=1, description="需要批量变更的任务 ID 列表")
    is_published: bool = Field(..., description="是否公开作品")
    category_id: Optional[uuid.UUID] = Field(default=None, description="批量公开时可指定画廊分类 ID")


class AdminTaskPublishBatchFailedItem(BaseModel):
    task_id: uuid.UUID
    reason: str


class AdminTaskPublishBatchResponse(BaseModel):
    updated_count: int
    failed_count: int
    items: List[TaskPublishStateResponse]
    failed_items: List[AdminTaskPublishBatchFailedItem] = Field(default_factory=list)


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


class AdminTaskPublishStatusUpdate(BaseModel):
    publish_status: str

    @field_validator("publish_status")
    @classmethod
    def validate_publish_status(cls, value: str) -> str:
        return _validate_publish_status(value)


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


class AdminGalleryUserSchema(BaseModel):
    user_id: uuid.UUID
    username: Optional[str] = None
    nickname: Optional[str] = None
    avatar: Optional[str] = None


class AdminGalleryListItem(BaseModel):
    task_id: uuid.UUID
    user: AdminGalleryUserSchema
    image_url: Optional[str] = None
    thumb_url: Optional[str] = None
    prompt: str
    model_name: Optional[str] = None
    aspect_ratio: Optional[str] = None
    status: str
    cost: int
    is_published: bool
    publish_status: str
    category_id: Optional[uuid.UUID] = None
    like_count: int = 0
    view_count: int = 0
    published_at: Optional[int] = None
    created_at: Optional[int] = None


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
    vip_type: Optional[str] = None
    vip_level: int = 0
    valid_days: Optional[int] = Field(default=None, description="权益有效期（天），不传则使用商品类型默认值")

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: str) -> str:
        return _validate_product_type(value)


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
    vip_type: Optional[str] = None
    vip_level: Optional[int] = None
    valid_days: Optional[int] = None

    @field_validator("type")
    @classmethod
    def validate_type(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return _validate_product_type(value)


class AdminProductResponse(AdminProductBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True

