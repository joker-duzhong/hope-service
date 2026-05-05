"""
JustRight Models
表名前缀: just_right_
"""
import uuid
from datetime import date, datetime
from typing import Optional, List

from sqlalchemy import Boolean, DateTime, String, Float, Date, Text, ForeignKey, JSON
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import CoreModel


class Couple(CoreModel):
    """情侣关系表"""
    __tablename__ = "just_right_couples"

    user1_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, comment="用户1 ID")
    user2_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, comment="用户2 ID (邀请后填入)")
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, comment="邀请码")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending(等待邀请), active(已配对), inactive(已解散)")

    # 关系纪念日
    anniversary_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="关系开始日期")


# ==================== 模块一：清单与备忘 ====================

class TodoItem(CoreModel):
    """情侣待办事项表"""
    __tablename__ = "just_right_todo_items"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), comment="创建者用户ID")
    content: Mapped[str] = mapped_column(String(500), comment="待办内容")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending(待办), completed(已完成)")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")
    completed_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, comment="完成者用户ID")


class Memo(CoreModel):
    """情侣备忘录表"""
    __tablename__ = "just_right_memos"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), comment="创建者用户ID")
    content: Mapped[str] = mapped_column(Text, comment="备忘录内容")
    resource_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, comment="关联资源ID列表")
    likes: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, comment="点赞用户ID列表")
    comments: Mapped[Optional[list]] = mapped_column(JSON, nullable=True, comment="评论列表")

    # 置顶功能
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否置顶")
    pinned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="置顶时间")


# ==================== 模块二：Ta的说明书 ====================

class UserManual(CoreModel):
    """用户说明书表"""
    __tablename__ = "just_right_user_manuals"

    uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), unique=True, index=True, comment="用户ID")
    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")

    # 尺码档案
    shoe_size: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="鞋码")
    clothes_size: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="衣服尺码")
    pants_size: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="裤子尺码")
    ring_size: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="戒指尺码")

    # 饮食偏好 (JSON 存储，方便扩展)
    diet_preferences: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="饮食偏好")
    # 示例: {"likes": ["火锅", "日料"], "dislikes": ["香菜"], "allergies": ["花生"]}

    # 情绪指南 (JSON 存储)
    emotional_guide: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="情绪指南")
    # 示例: {"cheer_up": ["奶茶", "看电影"], "avoid": ["冷战"], "love_language": "肯定的言辞"}

    # 其他自定义字段
    extra_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="其他扩展信息")


# ==================== 模块三：日常决策与礼物池 ====================

class RouletteOption(CoreModel):
    """转盘选项表"""
    __tablename__ = "just_right_roulette_options"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    title: Mapped[str] = mapped_column(String(100), comment="选项内容")
    category: Mapped[str] = mapped_column(String(50), default="food", comment="分类: food(吃啥), place(去哪), other(其他)")
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="选项颜色 (前端展示用)")
    weight: Mapped[int] = mapped_column(default=1, comment="权重 (用于加权随机)")


class WishlistItem(CoreModel):
    """心愿单表"""
    __tablename__ = "just_right_wishlist"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), comment="创建者用户ID (许愿人)")
    title: Mapped[str] = mapped_column(String(200), comment="心愿标题")
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="商品链接")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="价格")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="图片URL")
    status: Mapped[str] = mapped_column(String(20), default="unclaimed", comment="状态: unclaimed(未认领), claimed(已认领/准备中), fulfilled(已实现)")
    claimer_uid: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, comment="认领者用户ID (另一方)")

    # 实现记录
    fulfilled_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="实现备注")
    fulfilled_resource_ids: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, comment="实现照片资源ID列表")
    fulfilled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="实现时间")


# ==================== 模块四：纪念日与首页互动 ====================

class Anniversary(CoreModel):
    """纪念日表"""
    __tablename__ = "just_right_anniversaries"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    title: Mapped[str] = mapped_column(String(100), comment="纪念日标题")
    target_date: Mapped[date] = mapped_column(Date, comment="目标日期")
    is_lunar: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否农历")
    repeat_type: Mapped[str] = mapped_column(String(20), default="yearly", comment="重复类型: yearly(每年), monthly(每月), once(仅一次)")
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="图标 (emoji 或图标名)")


class CoupleState(CoreModel):
    """情侣首页状态表 (高频更新)"""
    __tablename__ = "just_right_couple_states"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), unique=True, index=True, comment="情侣ID")

    # 用户1的状态
    user1_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), comment="用户1 ID")
    user1_mood: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="用户1心情")
    user1_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用户1留言")
    user1_white_flag: Mapped[bool] = mapped_column(Boolean, default=False, comment="用户1举白旗状态")
    user1_white_flag_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="用户1举白旗时间")

    # 用户2的状态
    user2_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, comment="用户2 ID")
    user2_mood: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="用户2心情")
    user2_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用户2留言")
    user2_white_flag: Mapped[bool] = mapped_column(Boolean, default=False, comment="用户2举白旗状态")
    user2_white_flag_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="用户2举白旗时间")

    # 冰箱贴 (共享留言板)
    fridge_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="冰箱贴内容")
    fridge_note_by: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, comment="冰箱贴最后修改者")
    fridge_note_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="冰箱贴最后修改时间")


# ==================== 模块五：心情日记 ====================

class MoodLog(CoreModel):
    """心情日记表"""
    __tablename__ = "just_right_mood_logs"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, comment="用户ID")
    mood: Mapped[str] = mapped_column(String(50), comment="心情状态")
    note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="心情备注")
    tags: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, comment="标签列表")


# ==================== 模块六：通知系统 ====================

class Notification(CoreModel):
    """通知记录表"""
    __tablename__ = "just_right_notifications"

    couple_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    recipient_uid: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True, comment="接收者用户ID")
    type: Mapped[str] = mapped_column(String(50), comment="通知类型: anniversary_reminder, state_update, wishlist_fulfilled, etc.")
    title: Mapped[str] = mapped_column(String(200), comment="通知标题")
    content: Mapped[str] = mapped_column(Text, comment="通知内容")
    data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True, comment="附加数据")
    is_read: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已读")
    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已发送 (微信推送)")
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="发送时间")
