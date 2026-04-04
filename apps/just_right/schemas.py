"""
JustRight Schemas
Pydantic 数据验证与序列化模型
"""
from datetime import date, datetime
from typing import Optional, List, Any

from pydantic import BaseModel, Field


# ==================== 通用响应 ====================

class MessageResponse(BaseModel):
    """简单消息响应"""
    message: str


# ==================== 情侣关系 ====================

class CoupleCreate(BaseModel):
    """创建情侣关系 (生成邀请码)"""
    pass


class CoupleJoin(BaseModel):
    """加入情侣关系"""
    invite_code: str = Field(..., description="邀请码", min_length=6, max_length=32)


class CoupleOut(BaseModel):
    """情侣信息"""
    id: int
    user1_id: int
    user2_id: Optional[int]
    invite_code: str
    status: str
    anniversary_date: Optional[date]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CoupleUpdate(BaseModel):
    """更新情侣信息"""
    anniversary_date: Optional[date] = None


# ==================== 模块一：清单与备忘 ====================

# --- TODO ---

class TodoItemBase(BaseModel):
    """待办事项基础"""
    content: str = Field(..., description="待办内容", max_length=500)


class TodoItemCreate(TodoItemBase):
    """创建待办事项"""
    pass


class TodoItemUpdate(BaseModel):
    """更新待办事项"""
    content: Optional[str] = Field(None, description="待办内容", max_length=500)
    status: Optional[str] = Field(None, description="状态: pending/completed")


class TodoItemOut(TodoItemBase):
    """待办事项输出"""
    id: int
    couple_id: int
    creator_uid: int
    status: str
    completed_at: Optional[datetime]
    completed_by: Optional[int]
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


# --- Memo ---

class MemoBase(BaseModel):
    """备忘录基础"""
    content: str = Field(..., description="备忘录内容")


class MemoCreate(MemoBase):
    """创建备忘录"""
    image_urls: Optional[List[str]] = Field(None, description="图片URL列表")


class MemoUpdate(BaseModel):
    """更新备忘录"""
    content: Optional[str] = None
    image_urls: Optional[List[str]] = None


class MemoOut(MemoBase):
    """备忘录输出"""
    id: int
    couple_id: int
    creator_uid: int
    image_urls: Optional[List[str]]
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


# ==================== 模块二：Ta的说明书 ====================

class UserManualBase(BaseModel):
    """用户说明书基础"""
    shoe_size: Optional[str] = Field(None, description="鞋码", max_length=20)
    clothes_size: Optional[str] = Field(None, description="衣服尺码", max_length=20)
    pants_size: Optional[str] = Field(None, description="裤子尺码", max_length=20)
    ring_size: Optional[str] = Field(None, description="戒指尺码", max_length=20)
    diet_preferences: Optional[dict] = Field(None, description="饮食偏好")
    emotional_guide: Optional[dict] = Field(None, description="情绪指南")
    extra_info: Optional[dict] = Field(None, description="其他扩展信息")


class UserManualCreate(UserManualBase):
    """创建用户说明书"""
    pass


class UserManualUpdate(BaseModel):
    """更新用户说明书"""
    shoe_size: Optional[str] = None
    clothes_size: Optional[str] = None
    pants_size: Optional[str] = None
    ring_size: Optional[str] = None
    diet_preferences: Optional[dict] = None
    emotional_guide: Optional[dict] = None
    extra_info: Optional[dict] = None


class UserManualOut(UserManualBase):
    """用户说明书输出"""
    id: int
    uid: int
    couple_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CoupleManualsOut(BaseModel):
    """情侣双方说明书"""
    mine: Optional[UserManualOut]
    ta: Optional[UserManualOut]
    ta_uid: Optional[int]


# ==================== 模块三：日常决策与礼物池 ====================

# --- Roulette ---

class RouletteOptionBase(BaseModel):
    """转盘选项基础"""
    title: str = Field(..., description="选项内容", max_length=100)
    category: str = Field("food", description="分类: food/place/other", max_length=50)
    color: Optional[str] = Field(None, description="选项颜色", max_length=20)
    weight: int = Field(1, description="权重", ge=1, le=10)


class RouletteOptionCreate(RouletteOptionBase):
    """创建转盘选项"""
    pass


class RouletteOptionUpdate(BaseModel):
    """更新转盘选项"""
    title: Optional[str] = None
    category: Optional[str] = None
    color: Optional[str] = None
    weight: Optional[int] = None


class RouletteOptionOut(RouletteOptionBase):
    """转盘选项输出"""
    id: int
    couple_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


class RouletteSpinResult(BaseModel):
    """转盘抽奖结果"""
    result: RouletteOptionOut
    all_options: List[RouletteOptionOut]


# --- Wishlist ---

class WishlistItemBase(BaseModel):
    """心愿单基础"""
    title: str = Field(..., description="心愿标题", max_length=200)
    url: Optional[str] = Field(None, description="商品链接", max_length=500)
    price: Optional[float] = Field(None, description="价格", ge=0)
    image_url: Optional[str] = Field(None, description="图片URL", max_length=500)


class WishlistItemCreate(WishlistItemBase):
    """创建心愿"""
    pass


class WishlistItemUpdate(BaseModel):
    """更新心愿"""
    title: Optional[str] = None
    url: Optional[str] = None
    price: Optional[float] = None
    image_url: Optional[str] = None


class WishlistItemOut(WishlistItemBase):
    """心愿输出"""
    id: int
    couple_id: int
    creator_uid: int
    status: str
    claimer_uid: Optional[int]
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


class WishlistItemOutHidden(BaseModel):
    """心愿输出 (隐藏认领状态 - 给创建者看)"""
    id: int
    couple_id: int
    creator_uid: int
    title: str
    url: Optional[str] = None
    price: Optional[float] = None
    image_url: Optional[str] = None
    status: str  # unclaimed 或 "preparing" (对方已暗中准备)
    claimer_uid: Optional[int] = None  # 始终隐藏
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    @classmethod
    def from_item(cls, item: Any, is_creator: bool) -> "WishlistItemOutHidden":
        """根据是否是创建者转换输出"""
        data = {
            "id": item.id,
            "couple_id": item.couple_id,
            "creator_uid": item.creator_uid,
            "title": item.title,
            "url": item.url,
            "price": item.price,
            "image_url": item.image_url,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
            "is_deleted": item.is_deleted,
        }
        if is_creator and item.status == "claimed":
            data["status"] = "preparing"
            data["claimer_uid"] = None
        else:
            data["status"] = item.status
            data["claimer_uid"] = item.claimer_uid
        return cls(**data)


# ==================== 模块四：纪念日与首页互动 ====================

# --- Anniversary ---

class AnniversaryBase(BaseModel):
    """纪念日基础"""
    title: str = Field(..., description="纪念日标题", max_length=100)
    target_date: date = Field(..., description="目标日期")
    is_lunar: bool = Field(False, description="是否农历")
    repeat_type: str = Field("yearly", description="重复类型: yearly/monthly/once")
    icon: Optional[str] = Field(None, description="图标", max_length=50)


class AnniversaryCreate(AnniversaryBase):
    """创建纪念日"""
    pass


class AnniversaryUpdate(BaseModel):
    """更新纪念日"""
    title: Optional[str] = None
    target_date: Optional[date] = None
    is_lunar: Optional[bool] = None
    repeat_type: Optional[str] = None
    icon: Optional[str] = None


class AnniversaryOut(AnniversaryBase):
    """纪念日输出"""
    id: int
    couple_id: int
    created_at: datetime
    updated_at: datetime
    is_deleted: bool

    class Config:
        from_attributes = True


class AnniversaryCountdown(BaseModel):
    """纪念日倒计时"""
    anniversary: AnniversaryOut
    days_until: int = Field(..., description="距离纪念日的天数 (负数表示已过去)")
    is_countdown: bool = Field(..., description="是否为倒计时 (True=未到, False=已过)")
    display_text: str = Field(..., description="展示文本")


# --- Couple State ---

class UserState(BaseModel):
    """用户状态"""
    uid: int
    mood: Optional[str] = None
    note: Optional[str] = None
    white_flag: bool = False
    white_flag_at: Optional[datetime] = None


class CoupleStateUpdate(BaseModel):
    """更新情侣状态"""
    mood: Optional[str] = Field(None, description="心情", max_length=50)
    note: Optional[str] = Field(None, description="留言", max_length=500)
    white_flag: Optional[bool] = Field(None, description="举白旗")


class FridgeNoteUpdate(BaseModel):
    """更新冰箱贴"""
    fridge_note: str = Field(..., description="冰箱贴内容", max_length=500)


class CoupleStateOut(BaseModel):
    """情侣首页状态输出"""
    id: int
    couple_id: int
    user1: UserState
    user2: Optional[UserState]
    fridge_note: Optional[str]
    fridge_note_by: Optional[int]
    fridge_note_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== 首页聚合数据 ====================

class HomeDataOut(BaseModel):
    """首页聚合数据"""
    # 情侣信息
    couple: CoupleOut
    # 在一起天数
    together_days: int
    # 即将到来的纪念日
    upcoming_anniversaries: List[AnniversaryCountdown]
    # 双方状态
    state: CoupleStateOut
    # 双方说明书
    manuals: CoupleManualsOut
