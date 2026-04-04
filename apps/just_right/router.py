"""
JustRight Router
API 路由层
"""
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel, PaginatedResponse, PaginatedData
from core.users.models import User
from core.users.dependencies import get_current_user
from core.dependencies import get_app_key
from core.exceptions import BadRequestException

from apps.just_right.models import Couple
from apps.just_right.schemas import (
    CoupleOut, CoupleJoin, CoupleUpdate,
    TodoItemCreate, TodoItemUpdate, TodoItemOut,
    MemoCreate, MemoOut,
    UserManualOut, UserManualUpdate, CoupleManualsOut,
    RouletteOptionCreate, RouletteOptionUpdate, RouletteOptionOut, RouletteSpinResult,
    WishlistItemCreate, WishlistItemUpdate, WishlistItemOut, WishlistItemOutHidden,
    AnniversaryCreate, AnniversaryUpdate, AnniversaryOut, AnniversaryCountdown,
    CoupleStateUpdate, FridgeNoteUpdate, CoupleStateOut, UserState,
    HomeDataOut
)
from apps.just_right.services import (
    CoupleService, TodoService, MemoService, UserManualService,
    RouletteService, WishlistService, AnniversaryService, CoupleStateService
)


# 用于构建分页数据
class PaginatedDataWrapper:
    """分页数据包装器"""
    def __init__(self, items: list, total: int, page: int, page_size: int):
        self.items = items
        self.total = total
        self.page = page
        self.page_size = page_size
        self.total_pages = (total + page_size - 1) // page_size if total > 0 else 0

# 路由级别依赖：所有接口都必须传入有效的 app header
router = APIRouter(dependencies=[Depends(get_app_key)])


# ==================== 辅助函数 ====================

async def get_couple_or_raise(session: AsyncSession, user_id: int) -> Couple:
    """获取当前用户的情侣关系，如果不存在则抛出异常"""
    couple = await CoupleService.get_couple_by_user(session, user_id)
    if not couple:
        raise BadRequestException("您还没有伴侣，请先创建/加入情侣关系")
    return couple


def build_couple_state_out(state) -> CoupleStateOut:
    """构建 CoupleStateOut 输出"""
    user1 = UserState(
        uid=state.user1_id,
        mood=state.user1_mood,
        note=state.user1_note,
        white_flag=state.user1_white_flag,
        white_flag_at=state.user1_white_flag_at
    )
    user2 = None
    if state.user2_id:
        user2 = UserState(
            uid=state.user2_id,
            mood=state.user2_mood,
            note=state.user2_note,
            white_flag=state.user2_white_flag,
            white_flag_at=state.user2_white_flag_at
        )
    return CoupleStateOut(
        id=state.id,
        couple_id=state.couple_id,
        user1=user1,
        user2=user2,
        fridge_note=state.fridge_note,
        fridge_note_by=state.fridge_note_by,
        fridge_note_at=state.fridge_note_at,
        created_at=state.created_at,
        updated_at=state.updated_at
    )


# ==================== 情侣关系 ====================

@router.post("/couples", response_model=ResponseModel[CoupleOut])
async def create_couple(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建情侣关系 (生成邀请码)"""
    couple = await CoupleService.create_couple(db, current_user.id)
    return ResponseModel(data=couple, message="邀请码已生成，快分享给你的另一半吧~")


@router.post("/couples/join", response_model=ResponseModel[CoupleOut])
async def join_couple(
    data: CoupleJoin,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """加入情侣关系 (使用邀请码)"""
    couple = await CoupleService.join_couple(db, current_user.id, data.invite_code)
    return ResponseModel(data=couple, message="配对成功！祝你们幸福~")


@router.get("/couples/me", response_model=ResponseModel[CoupleOut])
async def get_my_couple(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取当前用户的情侣关系"""
    couple = await get_couple_or_raise(db, current_user.id)
    return ResponseModel(data=couple)


@router.put("/couples/me", response_model=ResponseModel[CoupleOut])
async def update_my_couple(
    data: CoupleUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新情侣关系信息"""
    couple = await get_couple_or_raise(db, current_user.id)
    updated = await CoupleService.update_couple(
        db, couple.id, current_user.id, data.anniversary_date
    )
    return ResponseModel(data=updated)


# ==================== 模块一：清单与备忘 ====================

# --- TODO ---

@router.post("/todos", response_model=ResponseModel[TodoItemOut])
async def create_todo(
    data: TodoItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建待办事项"""
    couple = await get_couple_or_raise(db, current_user.id)
    todo = await TodoService.create_todo(db, couple.id, current_user.id, data)
    return ResponseModel(data=todo, message="待办创建成功")


@router.get("/todos", response_model=ResponseModel[List[TodoItemOut]])
async def list_todos(
    status: Optional[str] = Query(None, description="状态筛选: pending/completed"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取待办列表"""
    couple = await get_couple_or_raise(db, current_user.id)
    todos = await TodoService.list_todos(db, couple.id, status)
    return ResponseModel(data=todos)


@router.put("/todos/{todo_id}", response_model=ResponseModel[TodoItemOut])
async def update_todo(
    data: TodoItemUpdate,
    todo_id: int = Path(..., description="待办ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新待办事项"""
    couple = await get_couple_or_raise(db, current_user.id)
    todo = await TodoService.update_todo(db, couple.id, todo_id, data, current_user.id)
    if not todo:
        return ResponseModel(code=404, message="待办不存在", data=None)

    # 勾选完成时返回特殊消息
    if data.status == "completed" and todo.status == "completed":
        return ResponseModel(data=todo, message="太棒了，又完成一项！🎉")
    return ResponseModel(data=todo)


@router.delete("/todos/{todo_id}", response_model=ResponseModel[bool])
async def delete_todo(
    todo_id: int = Path(..., description="待办ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除待办事项"""
    couple = await get_couple_or_raise(db, current_user.id)
    success = await TodoService.delete_todo(db, couple.id, todo_id)
    if not success:
        return ResponseModel(code=404, message="待办不存在", data=False)
    return ResponseModel(data=True, message="删除成功")


# --- Memo ---

@router.post("/memos", response_model=ResponseModel[MemoOut])
async def create_memo(
    data: MemoCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建备忘录"""
    couple = await get_couple_or_raise(db, current_user.id)
    memo = await MemoService.create_memo(db, couple.id, current_user.id, data)
    return ResponseModel(data=memo, message="备忘录创建成功")


@router.get("/memos", response_model=PaginatedResponse[MemoOut])
async def list_memos(
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取备忘录列表 (分页)"""
    couple = await get_couple_or_raise(db, current_user.id)
    memos, total = await MemoService.list_memos(db, couple.id, page, page_size)
    total_pages = (total + page_size - 1) // page_size if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(
            items=memos,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        )
    )


@router.delete("/memos/{memo_id}", response_model=ResponseModel[bool])
async def delete_memo(
    memo_id: int = Path(..., description="备忘录ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除备忘录"""
    couple = await get_couple_or_raise(db, current_user.id)
    success = await MemoService.delete_memo(db, couple.id, memo_id)
    if not success:
        return ResponseModel(code=404, message="备忘录不存在", data=False)
    return ResponseModel(data=True, message="删除成功")


# ==================== 模块二：Ta的说明书 ====================

@router.get("/manuals", response_model=ResponseModel[CoupleManualsOut])
async def get_manuals(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取情侣双方的说明书"""
    couple = await get_couple_or_raise(db, current_user.id)
    manuals = await UserManualService.get_couple_manuals(db, current_user.id, couple.id)
    return ResponseModel(data=manuals)


@router.put("/manuals/me", response_model=ResponseModel[UserManualOut])
async def update_my_manual(
    data: UserManualUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新我的说明书"""
    couple = await get_couple_or_raise(db, current_user.id)
    # 确保说明书存在
    await UserManualService.get_or_create_manual(db, current_user.id, couple.id)
    manual = await UserManualService.update_manual(db, current_user.id, data)
    return ResponseModel(data=manual, message="说明书更新成功")


# ==================== 模块三：日常决策与礼物池 ====================

# --- Roulette ---

@router.post("/roulette/options", response_model=ResponseModel[RouletteOptionOut])
async def create_roulette_option(
    data: RouletteOptionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建转盘选项"""
    couple = await get_couple_or_raise(db, current_user.id)
    option = await RouletteService.create_option(db, couple.id, data)
    return ResponseModel(data=option, message="选项添加成功")


@router.get("/roulette/options", response_model=ResponseModel[List[RouletteOptionOut]])
async def list_roulette_options(
    category: Optional[str] = Query(None, description="分类: food/place/other"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取转盘选项列表"""
    couple = await get_couple_or_raise(db, current_user.id)
    options = await RouletteService.list_options(db, couple.id, category)
    return ResponseModel(data=options)


@router.put("/roulette/options/{option_id}", response_model=ResponseModel[RouletteOptionOut])
async def update_roulette_option(
    data: RouletteOptionUpdate,
    option_id: int = Path(..., description="选项ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新转盘选项"""
    couple = await get_couple_or_raise(db, current_user.id)
    option = await RouletteService.update_option(db, couple.id, option_id, data)
    if not option:
        return ResponseModel(code=404, message="选项不存在", data=None)
    return ResponseModel(data=option)


@router.delete("/roulette/options/{option_id}", response_model=ResponseModel[bool])
async def delete_roulette_option(
    option_id: int = Path(..., description="选项ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除转盘选项"""
    couple = await get_couple_or_raise(db, current_user.id)
    success = await RouletteService.delete_option(db, couple.id, option_id)
    if not success:
        return ResponseModel(code=404, message="选项不存在", data=False)
    return ResponseModel(data=True, message="删除成功")


@router.post("/roulette/spin", response_model=ResponseModel[RouletteSpinResult])
async def spin_roulette(
    category: Optional[str] = Query(None, description="分类: food/place/other"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """转盘抽奖"""
    couple = await get_couple_or_raise(db, current_user.id)
    result, all_options = await RouletteService.spin(db, couple.id, category)
    return ResponseModel(
        data=RouletteSpinResult(result=result, all_options=all_options),
        message=f"🎊 抽中了: {result.title}"
    )


# --- Wishlist ---

@router.post("/wishlist", response_model=ResponseModel[WishlistItemOut])
async def create_wishlist_item(
    data: WishlistItemCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建心愿"""
    couple = await get_couple_or_raise(db, current_user.id)
    item = await WishlistService.create_item(db, couple.id, current_user.id, data)
    return ResponseModel(data=item, message="心愿添加成功")


@router.get("/wishlist", response_model=ResponseModel[List[WishlistItemOutHidden]])
async def list_wishlist(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取心愿单列表"""
    couple = await get_couple_or_raise(db, current_user.id)
    items = await WishlistService.list_items(db, couple.id, current_user.id)
    # 根据是否是创建者转换输出 (隐藏认领状态保持惊喜)
    hidden_items = [
        WishlistItemOutHidden.from_item(item, item.creator_uid == current_user.id)
        for item in items
    ]
    return ResponseModel(data=hidden_items)


@router.put("/wishlist/{item_id}", response_model=ResponseModel[WishlistItemOut])
async def update_wishlist_item(
    data: WishlistItemUpdate,
    item_id: int = Path(..., description="心愿ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新心愿 (只有创建者可以)"""
    couple = await get_couple_or_raise(db, current_user.id)
    item = await WishlistService.update_item(db, couple.id, item_id, current_user.id, data)
    if not item:
        return ResponseModel(code=404, message="心愿不存在或无权限", data=None)
    return ResponseModel(data=item)


@router.delete("/wishlist/{item_id}", response_model=ResponseModel[bool])
async def delete_wishlist_item(
    item_id: int = Path(..., description="心愿ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除心愿 (只有创建者可以)"""
    couple = await get_couple_or_raise(db, current_user.id)
    success = await WishlistService.delete_item(db, couple.id, item_id, current_user.id)
    if not success:
        return ResponseModel(code=404, message="心愿不存在或无权限", data=False)
    return ResponseModel(data=True, message="删除成功")


@router.post("/wishlist/{item_id}/claim", response_model=ResponseModel[WishlistItemOut])
async def claim_wishlist_item(
    item_id: int = Path(..., description="心愿ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """认领心愿 (只有非创建者可以)"""
    couple = await get_couple_or_raise(db, current_user.id)
    try:
        item = await WishlistService.claim_item(db, couple.id, item_id, current_user.id)
        return ResponseModel(data=item, message="已暗中认领，准备好惊喜吧~ 🎁")
    except BadRequestException as e:
        return ResponseModel(code=400, message=str(e), data=None)


@router.post("/wishlist/{item_id}/fulfill", response_model=ResponseModel[WishlistItemOut])
async def fulfill_wishlist_item(
    item_id: int = Path(..., description="心愿ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """标记心愿已实现"""
    couple = await get_couple_or_raise(db, current_user.id)
    try:
        item = await WishlistService.fulfill_item(db, couple.id, item_id, current_user.id)
        return ResponseModel(data=item, message="心愿实现！太浪漫了~ 💕")
    except BadRequestException as e:
        return ResponseModel(code=400, message=str(e), data=None)


# ==================== 模块四：纪念日与首页互动 ====================

# --- Anniversary ---

@router.post("/anniversaries", response_model=ResponseModel[AnniversaryOut])
async def create_anniversary(
    data: AnniversaryCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建纪念日"""
    couple = await get_couple_or_raise(db, current_user.id)
    anniversary = await AnniversaryService.create_anniversary(db, couple.id, data)
    return ResponseModel(data=anniversary, message="纪念日创建成功")


@router.get("/anniversaries", response_model=ResponseModel[List[AnniversaryOut]])
async def list_anniversaries(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取纪念日列表"""
    couple = await get_couple_or_raise(db, current_user.id)
    anniversaries = await AnniversaryService.list_anniversaries(db, couple.id)
    return ResponseModel(data=anniversaries)


@router.get("/anniversaries/upcoming", response_model=ResponseModel[List[AnniversaryCountdown]])
async def get_upcoming_anniversaries(
    limit: int = Query(5, ge=1, le=20, description="返回数量"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取即将到来的纪念日 (带倒计时)"""
    couple = await get_couple_or_raise(db, current_user.id)
    results = await AnniversaryService.get_upcoming_anniversaries(db, couple.id, limit)

    countdowns = []
    for r in results:
        ann = r["anniversary"]
        days = r["days_until"]
        is_countdown = days >= 0

        if is_countdown:
            display = f"距离 {ann.title} 还有 {days} 天"
        else:
            display = f"{ann.title} 已过去 {-days} 天"

        countdowns.append(AnniversaryCountdown(
            anniversary=ann,
            days_until=days,
            is_countdown=is_countdown,
            display_text=display
        ))

    return ResponseModel(data=countdowns)


@router.put("/anniversaries/{anniversary_id}", response_model=ResponseModel[AnniversaryOut])
async def update_anniversary(
    data: AnniversaryUpdate,
    anniversary_id: int = Path(..., description="纪念日ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新纪念日"""
    couple = await get_couple_or_raise(db, current_user.id)
    anniversary = await AnniversaryService.update_anniversary(db, couple.id, anniversary_id, data)
    if not anniversary:
        return ResponseModel(code=404, message="纪念日不存在", data=None)
    return ResponseModel(data=anniversary)


@router.delete("/anniversaries/{anniversary_id}", response_model=ResponseModel[bool])
async def delete_anniversary(
    anniversary_id: int = Path(..., description="纪念日ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除纪念日"""
    couple = await get_couple_or_raise(db, current_user.id)
    success = await AnniversaryService.delete_anniversary(db, couple.id, anniversary_id)
    if not success:
        return ResponseModel(code=404, message="纪念日不存在", data=False)
    return ResponseModel(data=True, message="删除成功")


# --- Couple State ---

@router.get("/state", response_model=ResponseModel[CoupleStateOut])
async def get_couple_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取情侣首页状态"""
    couple = await get_couple_or_raise(db, current_user.id)

    # 确保状态记录存在
    state = await CoupleStateService.get_or_create_state(
        db, couple.id, couple.user1_id, couple.user2_id
    )

    return ResponseModel(data=build_couple_state_out(state))


@router.put("/state", response_model=ResponseModel[CoupleStateOut])
async def update_my_state(
    data: CoupleStateUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新我的状态 (心情、留言、白旗)"""
    couple = await get_couple_or_raise(db, current_user.id)
    state = await CoupleStateService.update_user_state(db, couple.id, current_user.id, data)
    return ResponseModel(data=build_couple_state_out(state))


@router.put("/state/fridge", response_model=ResponseModel[CoupleStateOut])
async def update_fridge_note(
    data: FridgeNoteUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新冰箱贴 (共享留言板)"""
    couple = await get_couple_or_raise(db, current_user.id)
    state = await CoupleStateService.update_fridge_note(db, couple.id, current_user.id, data)
    return ResponseModel(data=build_couple_state_out(state))


@router.get("/state/white-flag-check", response_model=ResponseModel[dict])
async def check_white_flag(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """检查对方是否举了白旗 (前端用于弹出求和动画)"""
    couple = await get_couple_or_raise(db, current_user.id)
    result = await CoupleStateService.check_white_flag(db, couple.id, current_user.id)
    return ResponseModel(data=result)


# ==================== 首页聚合数据 ====================

@router.get("/home", response_model=ResponseModel[HomeDataOut])
async def get_home_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取首页聚合数据"""
    couple = await get_couple_or_raise(db, current_user.id)

    # 在一起天数
    together_days = 0
    if couple.anniversary_date:
        delta = date.today() - couple.anniversary_date
        together_days = delta.days

    # 即将到来的纪念日
    upcoming_raw = await AnniversaryService.get_upcoming_anniversaries(db, couple.id, limit=3)
    upcoming = []
    for r in upcoming_raw:
        ann = r["anniversary"]
        days = r["days_until"]
        is_countdown = days >= 0
        display = f"距离 {ann.title} 还有 {days} 天" if is_countdown else f"{ann.title} 已过去 {-days} 天"
        upcoming.append(AnniversaryCountdown(
            anniversary=ann,
            days_until=days,
            is_countdown=is_countdown,
            display_text=display
        ))

    # 情侣状态
    state = await CoupleStateService.get_or_create_state(
        db, couple.id, couple.user1_id, couple.user2_id
    )
    state_out = build_couple_state_out(state)

    # 双方说明书
    manuals = await UserManualService.get_couple_manuals(db, current_user.id, couple.id)

    return ResponseModel(data=HomeDataOut(
        couple=couple,
        together_days=together_days,
        upcoming_anniversaries=upcoming,
        state=state_out,
        manuals=manuals
    ))
