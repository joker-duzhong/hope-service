"""
JustRight Models
表名前缀: just_right_
"""
from datetime import date, datetime
from typing import Optional, List

from sqlalchemy import Boolean, DateTime, Integer, String, Float, Date, Text, ForeignKey, JSON
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import CoreModel


class Couple(CoreModel):
    """情侣关系表"""
    __tablename__ = "just_right_couples"

    user1_id: Mapped[int] = mapped_column(Integer, index=True, comment="用户1 ID")
    user2_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="用户2 ID (邀请后填入)")
    invite_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, comment="邀请码")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending(等待邀请), active(已配对), inactive(已解散)")

    # 关系纪念日
    anniversary_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True, comment="关系开始日期")


# ==================== 模块一：清单与备忘 ====================

class TodoItem(CoreModel):
    """情侣待办事项表"""
    __tablename__ = "just_right_todo_items"

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[int] = mapped_column(Integer, comment="创建者用户ID")
    content: Mapped[str] = mapped_column(String(500), comment="待办内容")
    status: Mapped[str] = mapped_column(String(20), default="pending", comment="状态: pending(待办), completed(已完成)")
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="完成时间")
    completed_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="完成者用户ID")


class Memo(CoreModel):
    """情侣备忘录表"""
    __tablename__ = "just_right_memos"

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[int] = mapped_column(Integer, comment="创建者用户ID")
    content: Mapped[str] = mapped_column(Text, comment="备忘录内容")
    image_urls: Mapped[Optional[List[str]]] = mapped_column(JSON, nullable=True, comment="图片URL列表")


# ==================== 模块二：Ta的说明书 ====================

class UserManual(CoreModel):
    """用户说明书表"""
    __tablename__ = "just_right_user_manuals"

    uid: Mapped[int] = mapped_column(Integer, unique=True, index=True, comment="用户ID")
    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")

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

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    title: Mapped[str] = mapped_column(String(100), comment="选项内容")
    category: Mapped[str] = mapped_column(String(50), default="food", comment="分类: food(吃啥), place(去哪), other(其他)")
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="选项颜色 (前端展示用)")
    weight: Mapped[int] = mapped_column(Integer, default=1, comment="权重 (用于加权随机)")


class WishlistItem(CoreModel):
    """心愿单表"""
    __tablename__ = "just_right_wishlist"

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    creator_uid: Mapped[int] = mapped_column(Integer, comment="创建者用户ID (许愿人)")
    title: Mapped[str] = mapped_column(String(200), comment="心愿标题")
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="商品链接")
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="价格")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="图片URL")
    status: Mapped[str] = mapped_column(String(20), default="unclaimed", comment="状态: unclaimed(未认领), claimed(已认领/准备中), fulfilled(已实现)")
    claimer_uid: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="认领者用户ID (另一方)")


# ==================== 模块四：纪念日与首页互动 ====================

class Anniversary(CoreModel):
    """纪念日表"""
    __tablename__ = "just_right_anniversaries"

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), index=True, comment="情侣ID")
    title: Mapped[str] = mapped_column(String(100), comment="纪念日标题")
    target_date: Mapped[date] = mapped_column(Date, comment="目标日期")
    is_lunar: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否农历")
    repeat_type: Mapped[str] = mapped_column(String(20), default="yearly", comment="重复类型: yearly(每年), monthly(每月), once(仅一次)")
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="图标 (emoji 或图标名)")


class CoupleState(CoreModel):
    """情侣首页状态表 (高频更新)"""
    __tablename__ = "just_right_couple_states"

    couple_id: Mapped[int] = mapped_column(Integer, ForeignKey("just_right_couples.id"), unique=True, index=True, comment="情侣ID")

    # 用户1的状态
    user1_id: Mapped[int] = mapped_column(Integer, comment="用户1 ID")
    user1_mood: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="用户1心情")
    user1_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用户1留言")
    user1_white_flag: Mapped[bool] = mapped_column(Boolean, default=False, comment="用户1举白旗状态")
    user1_white_flag_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="用户1举白旗时间")

    # 用户2的状态
    user2_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="用户2 ID")
    user2_mood: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="用户2心情")
    user2_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="用户2留言")
    user2_white_flag: Mapped[bool] = mapped_column(Boolean, default=False, comment="用户2举白旗状态")
    user2_white_flag_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="用户2举白旗时间")

    # 冰箱贴 (共享留言板)
    fridge_note: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="冰箱贴内容")
    fridge_note_by: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="冰箱贴最后修改者")
    fridge_note_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True, comment="冰箱贴最后修改时间")
