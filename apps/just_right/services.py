"""
JustRight Services
核心业务逻辑层
"""
import logging
import random
import secrets
from datetime import datetime, timedelta, date
from typing import List, Optional, Tuple

from sqlalchemy import select, or_, and_, func
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppException, NotFoundException, BadRequestException
from apps.just_right.models import (
    Couple, TodoItem, Memo, UserManual,
    RouletteOption, WishlistItem, Anniversary, CoupleState
)
from apps.just_right.schemas import (
    TodoItemCreate, TodoItemUpdate,
    MemoCreate, MemoUpdate,
    UserManualCreate, UserManualUpdate,
    RouletteOptionCreate, RouletteOptionUpdate,
    WishlistItemCreate, WishlistItemUpdate,
    AnniversaryCreate, AnniversaryUpdate,
    CoupleStateUpdate, FridgeNoteUpdate,
    CoupleManualsOut, UserManualOut
)

logger = logging.getLogger(__name__)


# ==================== 情侣服务 ====================

class CoupleService:
    """情侣关系管理"""

    @classmethod
    def generate_invite_code(cls) -> str:
        """生成6位邀请码"""
        return secrets.token_urlsafe(4)[:6].upper()

    @classmethod
    async def create_couple(cls, session: AsyncSession, user_id: int) -> Couple:
        """创建情侣关系 (生成邀请码)"""
        # 检查用户是否已有情侣关系
        existing = await cls.get_couple_by_user(session, user_id)
        if existing and existing.status == "active":
            raise BadRequestException("您已有伴侣，无法创建新的情侣关系")

        # 如果有 pending 状态的，复用它
        if existing and existing.status == "pending":
            return existing

        invite_code = cls.generate_invite_code()
        couple = Couple(
            user1_id=user_id,
            invite_code=invite_code,
            status="pending"
        )
        session.add(couple)
        await session.commit()
        await session.refresh(couple)
        return couple

    @classmethod
    async def join_couple(cls, session: AsyncSession, user_id: int, invite_code: str) -> Couple:
        """加入情侣关系"""
        # 检查用户是否已有情侣关系
        existing = await cls.get_couple_by_user(session, user_id)
        if existing and existing.status == "active":
            raise BadRequestException("您已有伴侣，无法加入其他情侣关系")

        # 查找待加入的情侣
        stmt = select(Couple).where(
            Couple.invite_code == invite_code,
            Couple.status == "pending",
            Couple.is_deleted == False
        )
        couple = (await session.execute(stmt)).scalars().first()

        if not couple:
            raise NotFoundException("邀请码无效或已过期")

        if couple.user1_id == user_id:
            raise BadRequestException("不能加入自己创建的情侣关系")

        # 加入情侣关系
        couple.user2_id = user_id
        couple.status = "active"
        couple.anniversary_date = date.today()

        # 如果用户之前有 pending 的关系，删除它
        if existing and existing.status == "pending":
            existing.is_deleted = True

        await session.commit()
        await session.refresh(couple)
        return couple

    @classmethod
    async def get_couple_by_user(cls, session: AsyncSession, user_id: int) -> Optional[Couple]:
        """根据用户ID获取情侣关系"""
        stmt = select(Couple).where(
            or_(Couple.user1_id == user_id, Couple.user2_id == user_id),
            Couple.is_deleted == False
        )
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def get_couple_by_id(cls, session: AsyncSession, couple_id: int) -> Optional[Couple]:
        """根据ID获取情侣关系"""
        stmt = select(Couple).where(
            Couple.id == couple_id,
            Couple.is_deleted == False
        )
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def get_partner_id(cls, session: AsyncSession, user_id: int) -> Optional[int]:
        """获取伴侣的用户ID"""
        couple = await cls.get_couple_by_user(session, user_id)
        if not couple or couple.status != "active":
            return None
        if couple.user1_id == user_id:
            return couple.user2_id
        return couple.user1_id

    @classmethod
    async def update_couple(
        cls, session: AsyncSession, couple_id: int, user_id: int, anniversary_date: Optional[date]
    ) -> Couple:
        """更新情侣信息"""
        couple = await cls.get_couple_by_id(session, couple_id)
        if not couple:
            raise NotFoundException("情侣关系不存在")
        if couple.user1_id != user_id and couple.user2_id != user_id:
            raise BadRequestException("无权限修改")

        if anniversary_date is not None:
            couple.anniversary_date = anniversary_date
        await session.commit()
        await session.refresh(couple)
        return couple


# ==================== 模块一：清单与备忘 ====================

class TodoService:
    """待办事项服务"""

    @classmethod
    async def create_todo(
        cls, session: AsyncSession, couple_id: int, user_id: int, data: TodoItemCreate
    ) -> TodoItem:
        """创建待办事项"""
        todo = TodoItem(
            couple_id=couple_id,
            creator_uid=user_id,
            content=data.content,
            status="pending"
        )
        session.add(todo)
        await session.commit()
        await session.refresh(todo)
        return todo

    @classmethod
    async def list_todos(
        cls, session: AsyncSession, couple_id: int, status: Optional[str] = None
    ) -> List[TodoItem]:
        """获取待办列表"""
        stmt = select(TodoItem).where(
            TodoItem.couple_id == couple_id,
            TodoItem.is_deleted == False
        )
        if status:
            stmt = stmt.where(TodoItem.status == status)
        stmt = stmt.order_by(TodoItem.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def update_todo(
        cls, session: AsyncSession, couple_id: int, todo_id: int,
        data: TodoItemUpdate, user_id: int
    ) -> Optional[TodoItem]:
        """更新待办事项"""
        stmt = select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.couple_id == couple_id,
            TodoItem.is_deleted == False
        )
        todo = (await session.execute(stmt)).scalars().first()
        if not todo:
            return None

        if data.content is not None:
            todo.content = data.content
        if data.status is not None:
            if data.status == "completed" and todo.status != "completed":
                todo.completed_at = datetime.now()
                todo.completed_by = user_id
            todo.status = data.status

        await session.commit()
        await session.refresh(todo)
        return todo

    @classmethod
    async def delete_todo(cls, session: AsyncSession, couple_id: int, todo_id: int) -> bool:
        """删除待办事项"""
        stmt = select(TodoItem).where(
            TodoItem.id == todo_id,
            TodoItem.couple_id == couple_id,
            TodoItem.is_deleted == False
        )
        todo = (await session.execute(stmt)).scalars().first()
        if not todo:
            return False
        todo.is_deleted = True
        await session.commit()
        return True


class MemoService:
    """备忘录服务"""

    @classmethod
    async def create_memo(
        cls, session: AsyncSession, couple_id: int, user_id: int, data: MemoCreate
    ) -> Memo:
        """创建备忘录"""
        memo = Memo(
            couple_id=couple_id,
            creator_uid=user_id,
            content=data.content,
            image_urls=data.image_urls
        )
        session.add(memo)
        await session.commit()
        await session.refresh(memo)
        return memo

    @classmethod
    async def list_memos(
        cls, session: AsyncSession, couple_id: int, page: int = 1, page_size: int = 20
    ) -> Tuple[List[Memo], int]:
        """获取备忘录列表 (分页)"""
        offset = (page - 1) * page_size

        # 查询总数
        count_stmt = select(func.count(Memo.id)).where(
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        )
        total = (await session.execute(count_stmt)).scalar() or 0

        # 查询列表
        stmt = select(Memo).where(
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        ).order_by(Memo.created_at.desc()).offset(offset).limit(page_size)
        result = await session.execute(stmt)
        return list(result.scalars().all()), total

    @classmethod
    async def delete_memo(cls, session: AsyncSession, couple_id: int, memo_id: int) -> bool:
        """删除备忘录"""
        stmt = select(Memo).where(
            Memo.id == memo_id,
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        )
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return False
        memo.is_deleted = True
        await session.commit()
        return True


# ==================== 模块二：Ta的说明书 ====================

class UserManualService:
    """用户说明书服务"""

    @classmethod
    async def get_or_create_manual(
        cls, session: AsyncSession, user_id: int, couple_id: int
    ) -> UserManual:
        """获取或创建用户说明书"""
        stmt = select(UserManual).where(UserManual.uid == user_id)
        manual = (await session.execute(stmt)).scalars().first()

        if not manual:
            manual = UserManual(uid=user_id, couple_id=couple_id)
            session.add(manual)
            await session.commit()
            await session.refresh(manual)
        return manual

    @classmethod
    async def update_manual(
        cls, session: AsyncSession, user_id: int, data: UserManualUpdate
    ) -> UserManual:
        """更新用户说明书"""
        stmt = select(UserManual).where(UserManual.uid == user_id)
        manual = (await session.execute(stmt)).scalars().first()

        if not manual:
            raise NotFoundException("说明书不存在，请先创建")

        update_fields = [
            "shoe_size", "clothes_size", "pants_size", "ring_size",
            "diet_preferences", "emotional_guide", "extra_info"
        ]
        for field in update_fields:
            value = getattr(data, field, None)
            if value is not None:
                setattr(manual, field, value)

        await session.commit()
        await session.refresh(manual)
        return manual

    @classmethod
    async def get_couple_manuals(
        cls, session: AsyncSession, user_id: int, couple_id: int
    ) -> CoupleManualsOut:
        """获取情侣双方的说明书"""
        couple = await CoupleService.get_couple_by_id(session, couple_id)
        if not couple:
            raise NotFoundException("情侣关系不存在")

        # 获取我的说明书
        my_manual = await cls.get_or_create_manual(session, user_id, couple_id)

        # 获取 Ta 的说明书
        ta_id = await CoupleService.get_partner_id(session, user_id)
        ta_manual = None
        if ta_id:
            stmt = select(UserManual).where(UserManual.uid == ta_id)
            ta_manual = (await session.execute(stmt)).scalars().first()

        return CoupleManualsOut(
            mine=UserManualOut.model_validate(my_manual),
            ta=UserManualOut.model_validate(ta_manual) if ta_manual else None,
            ta_uid=ta_id
        )


# ==================== 模块三：日常决策与礼物池 ====================

class RouletteService:
    """转盘服务"""

    @classmethod
    async def create_option(
        cls, session: AsyncSession, couple_id: int, data: RouletteOptionCreate
    ) -> RouletteOption:
        """创建转盘选项"""
        option = RouletteOption(
            couple_id=couple_id,
            title=data.title,
            category=data.category,
            color=data.color,
            weight=data.weight
        )
        session.add(option)
        await session.commit()
        await session.refresh(option)
        return option

    @classmethod
    async def list_options(
        cls, session: AsyncSession, couple_id: int, category: Optional[str] = None
    ) -> List[RouletteOption]:
        """获取转盘选项列表"""
        stmt = select(RouletteOption).where(
            RouletteOption.couple_id == couple_id,
            RouletteOption.is_deleted == False
        )
        if category:
            stmt = stmt.where(RouletteOption.category == category)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def update_option(
        cls, session: AsyncSession, couple_id: int, option_id: int, data: RouletteOptionUpdate
    ) -> Optional[RouletteOption]:
        """更新转盘选项"""
        stmt = select(RouletteOption).where(
            RouletteOption.id == option_id,
            RouletteOption.couple_id == couple_id,
            RouletteOption.is_deleted == False
        )
        option = (await session.execute(stmt)).scalars().first()
        if not option:
            return None

        if data.title is not None:
            option.title = data.title
        if data.category is not None:
            option.category = data.category
        if data.color is not None:
            option.color = data.color
        if data.weight is not None:
            option.weight = data.weight

        await session.commit()
        await session.refresh(option)
        return option

    @classmethod
    async def delete_option(cls, session: AsyncSession, couple_id: int, option_id: int) -> bool:
        """删除转盘选项"""
        stmt = select(RouletteOption).where(
            RouletteOption.id == option_id,
            RouletteOption.couple_id == couple_id,
            RouletteOption.is_deleted == False
        )
        option = (await session.execute(stmt)).scalars().first()
        if not option:
            return False
        option.is_deleted = True
        await session.commit()
        return True

    @classmethod
    async def spin(
        cls, session: AsyncSession, couple_id: int, category: Optional[str] = None
    ) -> Tuple[RouletteOption, List[RouletteOption]]:
        """转盘抽奖"""
        options = await cls.list_options(session, couple_id, category)
        if not options:
            raise BadRequestException("没有可用的转盘选项")

        # 加权随机选择
        weights = [opt.weight for opt in options]
        result = random.choices(options, weights=weights, k=1)[0]
        return result, options


class WishlistService:
    """心愿单服务"""

    @classmethod
    async def create_item(
        cls, session: AsyncSession, couple_id: int, user_id: int, data: WishlistItemCreate
    ) -> WishlistItem:
        """创建心愿"""
        item = WishlistItem(
            couple_id=couple_id,
            creator_uid=user_id,
            title=data.title,
            url=data.url,
            price=data.price,
            image_url=data.image_url,
            status="unclaimed"
        )
        session.add(item)
        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def list_items(
        cls, session: AsyncSession, couple_id: int, user_id: int
    ) -> List[WishlistItem]:
        """获取心愿单列表"""
        stmt = select(WishlistItem).where(
            WishlistItem.couple_id == couple_id,
            WishlistItem.is_deleted == False
        ).order_by(WishlistItem.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def update_item(
        cls, session: AsyncSession, couple_id: int, item_id: int,
        user_id: int, data: WishlistItemUpdate
    ) -> Optional[WishlistItem]:
        """更新心愿 (只有创建者可以更新)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.creator_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()
        if not item:
            return None

        if data.title is not None:
            item.title = data.title
        if data.url is not None:
            item.url = data.url
        if data.price is not None:
            item.price = data.price
        if data.image_url is not None:
            item.image_url = data.image_url

        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def delete_item(
        cls, session: AsyncSession, couple_id: int, item_id: int, user_id: int
    ) -> bool:
        """删除心愿 (只有创建者可以删除)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.creator_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()
        if not item:
            return False
        item.is_deleted = True
        await session.commit()
        return True

    @classmethod
    async def claim_item(
        cls, session: AsyncSession, couple_id: int, item_id: int, user_id: int
    ) -> WishlistItem:
        """认领心愿 (只有非创建者可以认领)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()

        if not item:
            raise NotFoundException("心愿不存在")

        if item.creator_uid == user_id:
            raise BadRequestException("不能认领自己的心愿")

        if item.status != "unclaimed":
            raise BadRequestException("该心愿已被认领")

        item.status = "claimed"
        item.claimer_uid = user_id
        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def fulfill_item(
        cls, session: AsyncSession, couple_id: int, item_id: int, user_id: int
    ) -> WishlistItem:
        """标记心愿已实现 (只有认领者可以操作)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.claimer_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()

        if not item:
            raise NotFoundException("心愿不存在或您不是认领者")

        item.status = "fulfilled"
        await session.commit()
        await session.refresh(item)
        return item


# ==================== 模块四：纪念日与首页互动 ====================

class AnniversaryService:
    """纪念日服务"""

    @classmethod
    async def create_anniversary(
        cls, session: AsyncSession, couple_id: int, data: AnniversaryCreate
    ) -> Anniversary:
        """创建纪念日"""
        anniversary = Anniversary(
            couple_id=couple_id,
            title=data.title,
            target_date=data.target_date,
            is_lunar=data.is_lunar,
            repeat_type=data.repeat_type,
            icon=data.icon
        )
        session.add(anniversary)
        await session.commit()
        await session.refresh(anniversary)
        return anniversary

    @classmethod
    async def list_anniversaries(
        cls, session: AsyncSession, couple_id: int
    ) -> List[Anniversary]:
        """获取纪念日列表"""
        stmt = select(Anniversary).where(
            Anniversary.couple_id == couple_id,
            Anniversary.is_deleted == False
        ).order_by(Anniversary.target_date.asc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def update_anniversary(
        cls, session: AsyncSession, couple_id: int, anniversary_id: int, data: AnniversaryUpdate
    ) -> Optional[Anniversary]:
        """更新纪念日"""
        stmt = select(Anniversary).where(
            Anniversary.id == anniversary_id,
            Anniversary.couple_id == couple_id,
            Anniversary.is_deleted == False
        )
        anniversary = (await session.execute(stmt)).scalars().first()
        if not anniversary:
            return None

        if data.title is not None:
            anniversary.title = data.title
        if data.target_date is not None:
            anniversary.target_date = data.target_date
        if data.is_lunar is not None:
            anniversary.is_lunar = data.is_lunar
        if data.repeat_type is not None:
            anniversary.repeat_type = data.repeat_type
        if data.icon is not None:
            anniversary.icon = data.icon

        await session.commit()
        await session.refresh(anniversary)
        return anniversary

    @classmethod
    async def delete_anniversary(
        cls, session: AsyncSession, couple_id: int, anniversary_id: int
    ) -> bool:
        """删除纪念日"""
        stmt = select(Anniversary).where(
            Anniversary.id == anniversary_id,
            Anniversary.couple_id == couple_id,
            Anniversary.is_deleted == False
        )
        anniversary = (await session.execute(stmt)).scalars().first()
        if not anniversary:
            return False
        anniversary.is_deleted = True
        await session.commit()
        return True

    @classmethod
    def calculate_days_until(cls, target_date: date, from_date: date = None) -> int:
        """计算距离目标日期的天数"""
        if from_date is None:
            from_date = date.today()
        delta = target_date - from_date
        return delta.days

    @classmethod
    def get_next_occurrence(cls, anniversary: Anniversary, from_date: date = None) -> date:
        """获取纪念日的下一次出现日期"""
        if from_date is None:
            from_date = date.today()

        target = anniversary.target_date

        if anniversary.repeat_type == "once":
            return target

        if anniversary.repeat_type == "yearly":
            # 尝试今年的日期
            this_year = date(from_date.year, target.month, target.day)
            if this_year >= from_date:
                return this_year
            # 明年
            return date(from_date.year + 1, target.month, target.day)

        if anniversary.repeat_type == "monthly":
            # 尝试本月
            try:
                this_month = date(from_date.year, from_date.month, target.day)
                if this_month >= from_date:
                    return this_month
            except ValueError:
                pass  # 日期不合法 (如31号在2月)

            # 下个月
            next_month = from_date.month + 1
            next_year = from_date.year
            if next_month > 12:
                next_month = 1
                next_year += 1
            try:
                return date(next_year, next_month, target.day)
            except ValueError:
                # 如果下个月也没有这天，取下个月最后一天
                if next_month == 12:
                    next_next_month = 1
                    next_year += 1
                else:
                    next_next_month = next_month + 1
                return date(next_year, next_next_month, 1) - timedelta(days=1)

        return target

    @classmethod
    async def get_upcoming_anniversaries(
        cls, session: AsyncSession, couple_id: int, limit: int = 5
    ) -> List[dict]:
        """获取即将到来的纪念日 (带倒计时)"""
        anniversaries = await cls.list_anniversaries(session, couple_id)
        today = date.today()

        results = []
        for ann in anniversaries:
            next_date = cls.get_next_occurrence(ann, today)
            days_until = cls.calculate_days_until(next_date, today)

            results.append({
                "anniversary": ann,
                "next_date": next_date,
                "days_until": days_until
            })

        # 按距离天数排序，未来的排前面
        results.sort(key=lambda x: (x["days_until"] < 0, abs(x["days_until"])))
        return results[:limit]


class CoupleStateService:
    """情侣首页状态服务"""

    @classmethod
    async def get_or_create_state(
        cls, session: AsyncSession, couple_id: int, user1_id: int, user2_id: Optional[int]
    ) -> CoupleState:
        """获取或创建情侣状态"""
        stmt = select(CoupleState).where(CoupleState.couple_id == couple_id)
        state = (await session.execute(stmt)).scalars().first()

        if not state:
            state = CoupleState(
                couple_id=couple_id,
                user1_id=user1_id,
                user2_id=user2_id
            )
            session.add(state)
            await session.commit()
            await session.refresh(state)
        return state

    @classmethod
    async def get_state(cls, session: AsyncSession, couple_id: int) -> Optional[CoupleState]:
        """获取情侣状态"""
        stmt = select(CoupleState).where(CoupleState.couple_id == couple_id)
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def update_user_state(
        cls, session: AsyncSession, couple_id: int, user_id: int, data: CoupleStateUpdate
    ) -> CoupleState:
        """更新用户状态 (心情、留言、白旗)"""
        state = await cls.get_state(session, couple_id)
        if not state:
            raise NotFoundException("情侣状态不存在")

        # 判断是 user1 还是 user2
        if state.user1_id == user_id:
            prefix = "user1"
        elif state.user2_id == user_id:
            prefix = "user2"
        else:
            raise BadRequestException("无权限更新")

        if data.mood is not None:
            setattr(state, f"{prefix}_mood", data.mood)
        if data.note is not None:
            setattr(state, f"{prefix}_note", data.note)
        if data.white_flag is not None:
            setattr(state, f"{prefix}_white_flag", data.white_flag)
            if data.white_flag:
                setattr(state, f"{prefix}_white_flag_at", datetime.now())
            else:
                setattr(state, f"{prefix}_white_flag_at", None)

        await session.commit()
        await session.refresh(state)
        return state

    @classmethod
    async def update_fridge_note(
        cls, session: AsyncSession, couple_id: int, user_id: int, data: FridgeNoteUpdate
    ) -> CoupleState:
        """更新冰箱贴"""
        state = await cls.get_state(session, couple_id)
        if not state:
            raise NotFoundException("情侣状态不存在")

        state.fridge_note = data.fridge_note
        state.fridge_note_by = user_id
        state.fridge_note_at = datetime.now()

        await session.commit()
        await session.refresh(state)
        return state

    @classmethod
    async def check_white_flag(cls, session: AsyncSession, couple_id: int, user_id: int) -> dict:
        """检查对方是否举了白旗 (用于前端弹动画)"""
        state = await cls.get_state(session, couple_id)
        if not state:
            return {"show_animation": False}

        # 判断对方是谁
        if state.user1_id == user_id:
            partner_white_flag = state.user2_white_flag
            partner_white_flag_at = state.user2_white_flag_at
            partner_id = state.user2_id
        else:
            partner_white_flag = state.user1_white_flag
            partner_white_flag_at = state.user1_white_flag_at
            partner_id = state.user1_id

        # 如果对方举了白旗，且在最近1分钟内 (避免每次刷新都弹)
        show_animation = False
        if partner_white_flag and partner_white_flag_at:
            time_diff = datetime.now() - partner_white_flag_at
            if time_diff < timedelta(minutes=1):
                show_animation = True

        return {
            "show_animation": show_animation,
            "partner_id": partner_id,
            "partner_white_flag": partner_white_flag
        }
