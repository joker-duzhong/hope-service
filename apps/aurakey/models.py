import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Integer, Boolean, DateTime, ForeignKey, Float, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.postgresql import UUID as PG_UUID

from core.database import CoreModel


class AurakeyGallery(CoreModel):
    """画廊作品表"""
    __tablename__ = "aurakey_gallery"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    author_nickname: Mapped[str] = mapped_column(String, nullable=True)
    author_avatar: Mapped[str] = mapped_column(String, nullable=True)
    thumb_url: Mapped[str] = mapped_column(String, nullable=True)
    image_url: Mapped[str] = mapped_column(String)
    prompt: Mapped[str] = mapped_column(Text)
    model_name: Mapped[str] = mapped_column(String, nullable=True)
    aspect_ratio: Mapped[str] = mapped_column(String, nullable=True)
    like_count: Mapped[int] = mapped_column(Integer, default=0)
    view_count: Mapped[int] = mapped_column(Integer, default=0)
    
    # 冗余一下原始任务ID方便追溯
    task_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AurakeyGalleryLike(CoreModel):
    """作品点赞记录"""
    __tablename__ = "aurakey_gallery_likes"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    gallery_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)


class AurakeyTask(CoreModel):
    """生图任务表"""
    __tablename__ = "aurakey_tasks"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending, processing, success, failed
    remote_task_id: Mapped[str] = mapped_column(String, nullable=True) # 记录上游大模型/生图接口的任务ID
    progress: Mapped[int] = mapped_column(Integer, default=0)
    image_url: Mapped[str] = mapped_column(String, nullable=True)
    image_resource_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    prompt: Mapped[str] = mapped_column(Text)
    show_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    template_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_name: Mapped[str] = mapped_column(String)
    aspect_ratio: Mapped[str] = mapped_column(String)
    frozen_points: Mapped[int] = mapped_column(Integer, default=0)
    failed_reason: Mapped[str] = mapped_column(String, nullable=True)
    cost: Mapped[int] = mapped_column(Integer, default=0)
    point_deductions: Mapped[list] = mapped_column(JSON, default=list)
    reference_image_ids: Mapped[list] = mapped_column(JSON, default=list)
    like_count: Mapped[int] = mapped_column(Integer, default=0)

    is_published: Mapped[bool] = mapped_column(Boolean, default=False)
    publish_status: Mapped[str] = mapped_column(String, default="approved", index=True)  # approved, blocked
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    view_count: Mapped[int] = mapped_column(Integer, default=0)


class AurakeyGalleryCategory(CoreModel):
    """画廊分类"""
    __tablename__ = "aurakey_gallery_categories"

    name: Mapped[str] = mapped_column(String)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class AurakeyModelOption(CoreModel):
    """生图模型可用选项"""
    __tablename__ = "aurakey_model_options"

    model_id: Mapped[str] = mapped_column(String, unique=True, index=True) # pro_1
    name: Mapped[str] = mapped_column(String)
    cost: Mapped[int] = mapped_column(Integer, default=10)
    is_vip_only: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="on") # on/off


class AurakeyAspectRatioOption(CoreModel):
    """生图比例可用选项"""
    __tablename__ = "aurakey_aspect_ratio_options"

    ratio: Mapped[str] = mapped_column(String, unique=True, index=True) # 1:1, 16:9
    sort: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="on")


class AurakeyUserAsset(CoreModel):
    """用户资产信息"""
    __tablename__ = "aurakey_user_assets"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, index=True)
    balance: Mapped[int] = mapped_column(Integer, default=0)
    is_vip: Mapped[bool] = mapped_column(Boolean, default=False)
    vip_type: Mapped[str] = mapped_column(String, nullable=True)
    vip_expire_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    invite_code: Mapped[str] = mapped_column(String, unique=True, index=True)
    invited_count: Mapped[int] = mapped_column(Integer, default=0)
    total_reward_points: Mapped[int] = mapped_column(Integer, default=0)

    invited_by_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=True)


class AurakeyAssetLog(CoreModel):
    """算力变动流水"""
    __tablename__ = "aurakey_asset_logs"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    type: Mapped[int] = mapped_column(Integer) # 1:充值, 2:生图消耗, 3:生图失败退回, 4:签到, 5:邀请奖励
    amount: Mapped[int] = mapped_column(Integer)
    balance_after: Mapped[int] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String)


class AurakeyPointGrant(CoreModel):
    """用户算力发放批次，用于处理有效期和剩余量"""
    __tablename__ = "aurakey_point_grants"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    source_type: Mapped[str] = mapped_column(String)  # order, sign_in, invite, admin, signup, refund
    source_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0)
    remaining_amount: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    description: Mapped[str] = mapped_column(String, nullable=True)


class AurakeyProduct(CoreModel):
    """商品配置表"""
    __tablename__ = "aurakey_products"

    type: Mapped[str] = mapped_column(String)  # point_pack, vip
    name: Mapped[str] = mapped_column(String)
    price: Mapped[int] = mapped_column(Integer) # 分
    original_price: Mapped[int] = mapped_column(Integer, nullable=True)
    point_amount: Mapped[int] = mapped_column(Integer, default=0)
    bonus_amount: Mapped[int] = mapped_column(Integer, default=0)
    tag: Mapped[str] = mapped_column(String, nullable=True)
    vip_type: Mapped[str] = mapped_column(String, nullable=True)
    vip_level: Mapped[int] = mapped_column(Integer, default=0)
    valid_days: Mapped[int] = mapped_column(Integer, nullable=True)


class AurakeyOrder(CoreModel):
    """支付订单表"""
    __tablename__ = "aurakey_orders"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    order_no: Mapped[str] = mapped_column(String, unique=True, index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True))
    amount: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="waiting") # waiting, success, failed
    pay_method: Mapped[str] = mapped_column(String, default="wechat_mini")
    paid_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlement_start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    entitlement_expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)
    third_trade_no: Mapped[str] = mapped_column(String, nullable=True)
    product_name: Mapped[str] = mapped_column(String, nullable=True)
    product_type: Mapped[str] = mapped_column(String, nullable=True)
    vip_type: Mapped[str] = mapped_column(String, nullable=True)
    vip_level: Mapped[int] = mapped_column(Integer, default=0)
    point_amount: Mapped[int] = mapped_column(Integer, default=0)
    bonus_amount: Mapped[int] = mapped_column(Integer, default=0)
    valid_days: Mapped[int] = mapped_column(Integer, nullable=True)
    granted_points: Mapped[int] = mapped_column(Integer, default=0)


class AurakeySystemConfig(CoreModel):
    """AuraKey 运行时配置"""
    __tablename__ = "aurakey_system_configs"

    key: Mapped[str] = mapped_column(String, unique=True, index=True)
    value: Mapped[dict] = mapped_column(JSON, default=dict)
