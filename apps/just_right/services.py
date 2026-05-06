"""
JustRight Services
核心业务逻辑层
"""
import logging
import random
import secrets
from calendar import monthrange
from datetime import datetime, timedelta, date, timezone
from typing import List, Optional, Tuple
from uuid import UUID, uuid4

from sqlalchemy import select, or_, and_, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import AppException, NotFoundException, BadRequestException
from apps.just_right.models import (
    Couple, TodoItem, Memo, UserManual,
    RouletteOption, WishlistItem, Anniversary, CoupleState
)
from apps.just_right.schemas import (
    TodoItemCreate, TodoItemUpdate,
    MemoCreate, MemoUpdate, MemoOut,
    UserManualCreate, UserManualUpdate,
    RouletteOptionCreate, RouletteOptionUpdate,
    WishlistItemCreate, WishlistItemUpdate,
    AnniversaryCreate, AnniversaryUpdate,
    CoupleStateUpdate, FridgeNoteUpdate,
    CoupleManualsOut, UserManualOut
)
from core.storage.models import Resource
from core.storage.services import StorageService

logger = logging.getLogger(__name__)


def _coerce_uuid_list(raw_values: Optional[List[object]], *, context: str) -> List[UUID]:
    """Best-effort UUID parsing for JSON columns with historical dirty data."""
    values: List[UUID] = []
    for raw in raw_values or []:
        try:
            values.append(raw if isinstance(raw, UUID) else UUID(str(raw)))
        except (TypeError, ValueError, AttributeError):
            logger.warning("Skip invalid UUID in %s: %r", context, raw)
    return values


def _parse_comment_payload(raw_comments: Optional[list], *, memo_id: UUID) -> list[dict]:
    """Normalize memo comments to avoid response serialization crashes."""
    comments: list[dict] = []
    for index, raw in enumerate(raw_comments or []):
        if not isinstance(raw, dict):
            logger.warning("Skip invalid memo comment in %s at index %s: %r", memo_id, index, raw)
            continue

        uid_raw = raw.get("uid")
        content = raw.get("content")
        try:
            uid = UUID(str(uid_raw))
        except (TypeError, ValueError, AttributeError):
            logger.warning(
                "Skip memo comment with invalid uid in %s at index %s: %r",
                memo_id,
                index,
                uid_raw,
            )
            continue

        if content is None:
            logger.warning("Skip memo comment without content in %s at index %s", memo_id, index)
            continue

        created_at_raw = raw.get("created_at")
        updated_at_raw = raw.get("updated_at")
        try:
            created_at = (
                created_at_raw
                if isinstance(created_at_raw, datetime)
                else datetime.fromisoformat(str(created_at_raw))
            )
        except (TypeError, ValueError):
            created_at = datetime.now(timezone.utc)

        try:
            updated_at = (
                updated_at_raw
                if isinstance(updated_at_raw, datetime)
                else datetime.fromisoformat(str(updated_at_raw))
            )
        except (TypeError, ValueError):
            updated_at = None

        comments.append(
            {
                "id": str(raw.get("id") or uuid4()),
                "uid": uid,
                "content": str(content),
                "created_at": created_at,
                "updated_at": updated_at,
            }
        )
    return comments


# ==================== 情侣服务 ====================

class CoupleService:
    """情侣关系管理"""

    @classmethod
    def generate_invite_code(cls) -> str:
        """生成6位邀请码"""
        return secrets.token_urlsafe(4)[:6].upper()

    @classmethod
    async def create_couple(cls, session: AsyncSession, user_id: UUID) -> Couple:
        """创建情侣关系 (生成邀请码)"""
        # 检查用户是否已有情侣关系
        existing = await cls.get_couple_by_user(session, user_id)
        if existing and existing.status == "active":
            raise BadRequestException("您已有伴侣，无法创建新的情侣关系")

        # 如果有 pending 状态的，复用它
        if existing and existing.status == "pending":
            return existing

        for _ in range(5):
            invite_code = cls.generate_invite_code()
            couple = Couple(
                user1_id=user_id,
                invite_code=invite_code,
                status="pending"
            )
            session.add(couple)
            try:
                await session.commit()
                await session.refresh(couple)
                return couple
            except IntegrityError:
                await session.rollback()

        raise AppException(code=500, message="邀请码生成失败，请稍后重试")

    @classmethod
    async def join_couple(cls, session: AsyncSession, user_id: UUID, invite_code: str) -> Couple:
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

        if couple.user2_id and couple.user2_id != user_id:
            raise BadRequestException("该邀请码已被其他用户使用")

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
    async def get_couple_by_user(cls, session: AsyncSession, user_id: UUID) -> Optional[Couple]:
        """根据用户ID获取情侣关系"""
        stmt = select(Couple).where(
            or_(Couple.user1_id == user_id, Couple.user2_id == user_id),
            Couple.is_deleted == False
        )
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def get_couple_by_id(cls, session: AsyncSession, couple_id: UUID) -> Optional[Couple]:
        """根据ID获取情侣关系"""
        stmt = select(Couple).where(
            Couple.id == couple_id,
            Couple.is_deleted == False
        )
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def get_partner_id(cls, session: AsyncSession, user_id: UUID) -> Optional[UUID]:
        """获取伴侣的用户ID"""
        couple = await cls.get_couple_by_user(session, user_id)
        if not couple or couple.status != "active":
            return None
        if couple.user1_id == user_id:
            return couple.user2_id
        return couple.user1_id

    @classmethod
    async def update_couple(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, anniversary_date: Optional[date]
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

    @classmethod
    async def dissolve_couple(cls, session: AsyncSession, couple_id: UUID, user_id: UUID) -> bool:
        """解除情侣关系"""
        couple = await cls.get_couple_by_id(session, couple_id)
        if not couple:
            raise NotFoundException("情侣关系不存在")
        if couple.user1_id != user_id and couple.user2_id != user_id:
            raise BadRequestException("无权限操作")

        couple.status = "inactive"
        couple.is_deleted = True
        await session.commit()
        return True


# ==================== 模块一：清单与备忘 ====================

class TodoService:
    """待办事项服务"""

    @classmethod
    async def create_todo(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, data: TodoItemCreate
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
        cls, session: AsyncSession, couple_id: UUID, status: Optional[str] = None
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
        cls, session: AsyncSession, couple_id: UUID, todo_id: UUID,
        data: TodoItemUpdate, user_id: UUID
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
                todo.completed_at = datetime.now(timezone.utc)
                todo.completed_by = user_id
            elif data.status == "pending" and todo.status != "pending":
                todo.completed_at = None
                todo.completed_by = None
            todo.status = data.status

        await session.commit()
        await session.refresh(todo)
        return todo

    @classmethod
    async def delete_todo(cls, session: AsyncSession, couple_id: UUID, todo_id: UUID) -> bool:
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
    async def _build_memo_out(cls, session: AsyncSession, memo: Memo) -> MemoOut:
        """将 ORM Memo 转为 MemoOut，批量签发资源预签名 URL"""
        resources = []
        resource_ids = memo.resource_ids or []
        if resource_ids:
            parsed_ids = _coerce_uuid_list(resource_ids, context=f"memo {memo.id} resource_ids")

            if parsed_ids:
                result = await session.execute(
                    select(Resource).where(
                        Resource.id.in_(parsed_ids),
                        Resource.is_deleted == False,
                    )
                )
                for r in result.scalars().all():
                    resources.append(await StorageService._build_response(r))

        likes = _coerce_uuid_list(memo.likes, context=f"memo {memo.id} likes")
        comments = _parse_comment_payload(memo.comments, memo_id=memo.id)

        return MemoOut(
            id=memo.id,
            couple_id=memo.couple_id,
            creator_uid=memo.creator_uid,
            content=memo.content,
            resources=resources or None,
            likes=likes,
            comments=comments,
            is_pinned=getattr(memo, "is_pinned", False),
            pinned_at=getattr(memo, "pinned_at", None),
            created_at=memo.created_at,
            updated_at=memo.updated_at,
            is_deleted=memo.is_deleted,
        )

    @classmethod
    async def create_memo(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, data: MemoCreate
    ) -> MemoOut:
        """创建备忘录"""
        memo = Memo(
            couple_id=couple_id,
            creator_uid=user_id,
            content=data.content,
            resource_ids=[str(rid) for rid in data.resource_ids] if data.resource_ids else None,
            likes=[],
            comments=[],
        )
        session.add(memo)
        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def list_memos(
        cls, session: AsyncSession, couple_id: UUID, page: int = 1, page_size: int = 20
    ) -> Tuple[List[MemoOut], int]:
        """获取备忘录列表 (分页)"""
        offset = (page - 1) * page_size

        # 查询总数
        count_stmt = select(func.count(Memo.id)).where(
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        )
        total = (await session.execute(count_stmt)).scalar() or 0

        # 查询列表 - 置顶的排在前面
        stmt = select(Memo).where(
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        ).order_by(
            Memo.is_pinned.desc(),
            Memo.pinned_at.desc().nullslast(),
            Memo.created_at.desc()
        ).offset(offset).limit(page_size)
        result = await session.execute(stmt)
        memos = list(result.scalars().all())

        # 批量构建 MemoOut（含资源预签名 URL）
        memo_outs = [await cls._build_memo_out(session, m) for m in memos]
        return memo_outs, total

    @classmethod
    async def search_memos(
        cls, session: AsyncSession, couple_id: UUID, keyword: Optional[str] = None,
        start_date: Optional[date] = None, end_date: Optional[date] = None,
        page: int = 1, page_size: int = 20
    ) -> Tuple[List[MemoOut], int]:
        """搜索备忘录"""
        offset = (page - 1) * page_size

        # 构建查询条件
        conditions = [
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        ]

        # 关键词搜索
        if keyword:
            conditions.append(Memo.content.ilike(f"%{keyword}%"))

        # 日期范围
        if start_date:
            conditions.append(func.date(Memo.created_at) >= start_date)
        if end_date:
            conditions.append(func.date(Memo.created_at) <= end_date)

        # 查询总数
        count_stmt = select(func.count(Memo.id)).where(*conditions)
        total = (await session.execute(count_stmt)).scalar() or 0

        # 查询列表 - 置顶的排在前面
        stmt = select(Memo).where(*conditions).order_by(
            Memo.is_pinned.desc(),
            Memo.pinned_at.desc().nullslast(),
            Memo.created_at.desc()
        ).offset(offset).limit(page_size)
        result = await session.execute(stmt)
        memos = list(result.scalars().all())

        memo_outs = [await cls._build_memo_out(session, m) for m in memos]
        return memo_outs, total

    @classmethod
    async def toggle_pin(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID
    ) -> Optional[MemoOut]:
        """切换备忘录置顶状态"""
        stmt = select(Memo).where(
            Memo.id == memo_id,
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        )
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None

        memo.is_pinned = not memo.is_pinned
        memo.pinned_at = datetime.now(timezone.utc) if memo.is_pinned else None

        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def delete_memo(cls, session: AsyncSession, couple_id: UUID, memo_id: UUID) -> bool:
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

    @classmethod
    async def update_memo(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID, user_id: UUID, data: MemoUpdate
    ) -> Optional[MemoOut]:
        """更新备忘录"""
        stmt = select(Memo).where(
            Memo.id == memo_id,
            Memo.couple_id == couple_id,
            Memo.creator_uid == user_id,
            Memo.is_deleted == False
        )
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None
        if data.content is not None:
            memo.content = data.content
        if data.resource_ids is not None:
            memo.resource_ids = [str(rid) for rid in data.resource_ids]
        if data.is_pinned is not None:
            memo.is_pinned = data.is_pinned
            memo.pinned_at = datetime.now(timezone.utc) if data.is_pinned else None
        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def toggle_like(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID, user_id: UUID
    ) -> Optional[MemoOut]:
        """点赞/取消点赞备忘录"""
        stmt = select(Memo).where(Memo.id == memo_id, Memo.couple_id == couple_id, Memo.is_deleted == False)
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None

        likes = memo.likes or []
        uid_str = str(user_id)
        if uid_str in likes:
            likes.remove(uid_str)
        else:
            likes.append(uid_str)

        memo.likes = likes
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(memo, "likes")

        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def add_comment(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID, user_id: UUID, content: str
    ) -> Optional[MemoOut]:
        """评论备忘录"""
        stmt = select(Memo).where(Memo.id == memo_id, Memo.couple_id == couple_id, Memo.is_deleted == False)
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None

        comments = [dict(item) for item in (memo.comments or []) if isinstance(item, dict)]
        comment_id = str(uuid4())
        now = datetime.now(timezone.utc)
        comments.append({
            "id": comment_id,
            "uid": str(user_id),
            "content": content,
            "created_at": now.isoformat(),
            "updated_at": now.isoformat()
        })
        memo.comments = comments
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(memo, "comments")

        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def update_comment(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID, comment_id: str, user_id: UUID, content: str
    ) -> Optional[MemoOut]:
        """修改评论（只能修改自己的）"""
        stmt = select(Memo).where(Memo.id == memo_id, Memo.couple_id == couple_id, Memo.is_deleted == False)
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None

        comments = [dict(item) for item in (memo.comments or []) if isinstance(item, dict)]
        for comment in comments:
            if comment.get("id") == comment_id and comment.get("uid") == str(user_id):
                comment["content"] = content
                comment["updated_at"] = datetime.now(timezone.utc).isoformat()
                break
        else:
            return None  # 评论不存在或无权限

        memo.comments = comments
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(memo, "comments")

        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)

    @classmethod
    async def delete_comment(
        cls, session: AsyncSession, couple_id: UUID, memo_id: UUID, comment_id: str, user_id: UUID
    ) -> Optional[MemoOut]:
        """删除评论（只能删除自己的）"""
        stmt = select(Memo).where(Memo.id == memo_id, Memo.couple_id == couple_id, Memo.is_deleted == False)
        memo = (await session.execute(stmt)).scalars().first()
        if not memo:
            return None

        comments = [dict(item) for item in (memo.comments or []) if isinstance(item, dict)]
        new_comments = [c for c in comments if not (c.get("id") == comment_id and c.get("uid") == str(user_id))]

        if len(new_comments) == len(comments):
            return None  # 评论不存在或无权限

        memo.comments = new_comments
        from sqlalchemy.orm.attributes import flag_modified
        flag_modified(memo, "comments")

        await session.commit()
        await session.refresh(memo)
        return await cls._build_memo_out(session, memo)


# ==================== 模块二：Ta的说明书 ====================

class UserManualService:
    """用户说明书服务"""

    @classmethod
    async def get_or_create_manual(
        cls, session: AsyncSession, user_id: UUID, couple_id: UUID
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
        cls, session: AsyncSession, user_id: UUID, data: UserManualUpdate
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
        cls, session: AsyncSession, user_id: UUID, couple_id: UUID
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
        cls, session: AsyncSession, couple_id: UUID, data: RouletteOptionCreate
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
        cls, session: AsyncSession, couple_id: UUID, category: Optional[str] = None
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
        cls, session: AsyncSession, couple_id: UUID, option_id: UUID, data: RouletteOptionUpdate
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
    async def delete_option(cls, session: AsyncSession, couple_id: UUID, option_id: UUID) -> bool:
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
        cls, session: AsyncSession, couple_id: UUID, category: Optional[str] = None
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
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, data: WishlistItemCreate
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
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, status: Optional[str] = None
    ) -> List[WishlistItem]:
        """获取心愿单列表"""
        stmt = select(WishlistItem).where(
            WishlistItem.couple_id == couple_id,
            WishlistItem.is_deleted == False
        )
        if status:
            stmt = stmt.where(WishlistItem.status == status)
        stmt = stmt.order_by(WishlistItem.created_at.desc())
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def update_item(
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID,
        user_id: UUID, data: WishlistItemUpdate
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
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID, user_id: UUID
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
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID, user_id: UUID
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

        if item.claimer_uid and item.claimer_uid != user_id:
            raise BadRequestException("该心愿已被其他人认领")

        if item.status != "unclaimed":
            raise BadRequestException("该心愿已被认领")

        item.status = "claimed"
        item.claimer_uid = user_id
        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def fulfill_item(
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID, user_id: UUID
    ) -> WishlistItem:
        """标记心愿已实现 (只能由创建者操作)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.creator_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()

        if not item:
            raise NotFoundException("心愿不存在或只能由创建者标记完成")

        if item.status == "fulfilled":
            return item

        if item.status == "claimed" and item.claimer_uid is None:
            raise BadRequestException("心愿认领状态异常，请先取消认领后重试")

        item.status = "fulfilled"
        item.fulfilled_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def fulfill_item_with_record(
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID, user_id: UUID,
        note: Optional[str] = None, resource_ids: Optional[List[UUID]] = None
    ) -> WishlistItem:
        """标记心愿已实现（带照片记录）"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.creator_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()

        if not item:
            raise NotFoundException("心愿不存在或只能由创建者标记完成")

        if item.status == "fulfilled":
            return item

        if item.status == "claimed" and item.claimer_uid is None:
            raise BadRequestException("心愿认领状态异常，请先取消认领后重试")

        item.status = "fulfilled"
        item.fulfilled_note = note
        item.fulfilled_resource_ids = [str(rid) for rid in resource_ids] if resource_ids else None
        item.fulfilled_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(item)
        return item

    @classmethod
    async def unclaim_item(
        cls, session: AsyncSession, couple_id: UUID, item_id: UUID, user_id: UUID
    ) -> WishlistItem:
        """取消认领心愿 (只有认领者可以操作)"""
        stmt = select(WishlistItem).where(
            WishlistItem.id == item_id,
            WishlistItem.couple_id == couple_id,
            WishlistItem.claimer_uid == user_id,
            WishlistItem.is_deleted == False
        )
        item = (await session.execute(stmt)).scalars().first()

        if not item:
            raise NotFoundException("心愿不存在或您未认领此心愿")

        if item.status != "claimed":
            raise BadRequestException("当前心愿不处于已认领状态")

        item.status = "unclaimed"
        item.claimer_uid = None
        await session.commit()
        await session.refresh(item)
        return item


# ==================== 模块四：纪念日与首页互动 ====================

class AnniversaryService:
    """纪念日服务"""

    @classmethod
    async def create_anniversary(
        cls, session: AsyncSession, couple_id: UUID, data: AnniversaryCreate
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
        cls, session: AsyncSession, couple_id: UUID
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
        cls, session: AsyncSession, couple_id: UUID, anniversary_id: UUID, data: AnniversaryUpdate
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
        cls, session: AsyncSession, couple_id: UUID, anniversary_id: UUID
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
            # 对 2 月 29 日等日期做兜底，避免直接 ValueError
            current_day = min(target.day, monthrange(from_date.year, target.month)[1])
            this_year = date(from_date.year, target.month, current_day)
            if this_year >= from_date:
                return this_year
            next_year = from_date.year + 1
            next_day = min(target.day, monthrange(next_year, target.month)[1])
            return date(next_year, target.month, next_day)

        if anniversary.repeat_type == "monthly":
            current_day = min(target.day, monthrange(from_date.year, from_date.month)[1])
            this_month = date(from_date.year, from_date.month, current_day)
            if this_month >= from_date:
                return this_month

            next_month = from_date.month + 1
            next_year = from_date.year
            if next_month > 12:
                next_month = 1
                next_year += 1
            next_day = min(target.day, monthrange(next_year, next_month)[1])
            return date(next_year, next_month, next_day)

        return target

    @classmethod
    async def get_upcoming_anniversaries(
        cls, session: AsyncSession, couple_id: UUID, limit: int = 5
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

        # 未来优先，随后按天数升序；过去的则按离今天最近的优先
        results.sort(
            key=lambda x: (
                x["days_until"] < 0,
                x["days_until"] if x["days_until"] >= 0 else abs(x["days_until"]),
            )
        )
        return results[:limit]


class CoupleStateService:
    """情侣首页状态服务"""

    @classmethod
    async def get_or_create_state(
        cls, session: AsyncSession, couple_id: UUID, user1_id: UUID, user2_id: Optional[UUID]
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
    async def get_state(cls, session: AsyncSession, couple_id: UUID) -> Optional[CoupleState]:
        """获取情侣状态"""
        stmt = select(CoupleState).where(CoupleState.couple_id == couple_id)
        return (await session.execute(stmt)).scalars().first()

    @classmethod
    async def update_user_state(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, data: CoupleStateUpdate
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
            setattr(state, f"{prefix}_mood_updated_at", datetime.now(timezone.utc))
        if data.note is not None:
            setattr(state, f"{prefix}_note", data.note)
            setattr(state, f"{prefix}_note_updated_at", datetime.now(timezone.utc))
        if data.white_flag is not None:
            setattr(state, f"{prefix}_white_flag", data.white_flag)
            if data.white_flag:
                setattr(state, f"{prefix}_white_flag_at", datetime.now(timezone.utc))
            else:
                setattr(state, f"{prefix}_white_flag_at", None)

        await session.commit()
        await session.refresh(state)
        return state

    @classmethod
    async def update_fridge_note(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, data: FridgeNoteUpdate
    ) -> CoupleState:
        """更新冰箱贴"""
        state = await cls.get_state(session, couple_id)
        if not state:
            raise NotFoundException("情侣状态不存在")

        state.fridge_note = data.fridge_note
        state.fridge_note_by = user_id
        state.fridge_note_at = datetime.now(timezone.utc)

        await session.commit()
        await session.refresh(state)
        return state

    @classmethod
    async def check_white_flag(cls, session: AsyncSession, couple_id: UUID, user_id: UUID) -> dict:
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
            time_diff = datetime.now(timezone.utc) - partner_white_flag_at
            if time_diff < timedelta(minutes=1):
                show_animation = True

        return {
            "show_animation": show_animation,
            "partner_id": partner_id,
            "partner_white_flag": partner_white_flag
        }


# ==================== 模块五：心情日记服务 ====================

class MoodLogService:
    """心情日记服务"""

    @classmethod
    async def create_log(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID,
        mood: str, note: Optional[str] = None, tags: Optional[List[str]] = None
    ):
        """创建心情日记"""
        from apps.just_right.models import MoodLog

        log = MoodLog(
            couple_id=couple_id,
            uid=user_id,
            mood=mood,
            note=note,
            tags=tags
        )
        session.add(log)
        await session.commit()
        await session.refresh(log)
        return log

    @classmethod
    async def list_logs(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, days: int = 30
    ):
        """获取心情历史记录"""
        from apps.just_right.models import MoodLog

        start_date = datetime.now(timezone.utc) - timedelta(days=days)
        stmt = select(MoodLog).where(
            MoodLog.couple_id == couple_id,
            MoodLog.uid == user_id,
            MoodLog.is_deleted == False,
            MoodLog.created_at >= start_date
        ).order_by(MoodLog.created_at.desc())

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_stats(
        cls, session: AsyncSession, couple_id: UUID, user_id: UUID, days: int = 30
    ) -> dict:
        """获取心情统计分析"""
        from apps.just_right.models import MoodLog

        logs = await cls.list_logs(session, couple_id, user_id, days)

        # 统计心情分布
        mood_distribution = {}
        for log in logs:
            mood_distribution[log.mood] = mood_distribution.get(log.mood, 0) + 1

        # 最常见心情
        most_common_mood = max(mood_distribution, key=mood_distribution.get) if mood_distribution else None

        # 最近7天趋势
        recent_start = datetime.now(timezone.utc) - timedelta(days=7)
        recent_trend = [log for log in logs if log.created_at >= recent_start]

        return {
            "total_logs": len(logs),
            "mood_distribution": mood_distribution,
            "recent_trend": recent_trend[:10],  # 最多返回10条
            "most_common_mood": most_common_mood
        }


# ==================== 模块六：通知服务 ====================

class NotificationService:
    """通知服务"""

    @classmethod
    async def create_notification(
        cls, session: AsyncSession, couple_id: UUID, recipient_uid: UUID,
        type: str, title: str, content: str, data: Optional[dict] = None
    ):
        """创建通知记录"""
        from apps.just_right.models import Notification

        dedupe_key = data.get("dedupe_key") if data else None
        if dedupe_key:
            existing_stmt = select(Notification).where(
                Notification.recipient_uid == recipient_uid,
                Notification.type == type,
                Notification.is_deleted == False,
                Notification.dedupe_key == dedupe_key,
            )
            existing = (await session.execute(existing_stmt)).scalars().first()
            if existing:
                return existing

        notification = Notification(
            couple_id=couple_id,
            recipient_uid=recipient_uid,
            type=type,
            title=title,
            content=content,
            data=data,
            dedupe_key=dedupe_key,
            is_read=False,
            is_sent=False
        )
        session.add(notification)
        await session.commit()
        await session.refresh(notification)
        return notification

    @classmethod
    async def list_notifications(
        cls, session: AsyncSession, user_id: UUID, unread_only: bool = False, limit: int = 50
    ):
        """获取通知列表"""
        from apps.just_right.models import Notification

        stmt = select(Notification).where(
            Notification.recipient_uid == user_id,
            Notification.is_deleted == False
        )

        if unread_only:
            stmt = stmt.where(Notification.is_read == False)

        stmt = stmt.order_by(Notification.created_at.desc()).limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def mark_as_read(cls, session: AsyncSession, notification_id: UUID, user_id: UUID) -> bool:
        """标记通知为已读"""
        from apps.just_right.models import Notification

        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.recipient_uid == user_id,
            Notification.is_deleted == False
        )
        notification = (await session.execute(stmt)).scalars().first()

        if not notification:
            return False

        notification.is_read = True
        await session.commit()
        return True

    @classmethod
    async def send_wechat_notification(cls, session: AsyncSession, notification_id: UUID):
        """通过微信客户服务消息发送通知"""
        from apps.just_right.models import Notification
        from core.config import settings
        from core.wechat.services import WeChatService
        from core.users.models import User

        stmt = select(Notification).where(
            Notification.id == notification_id,
            Notification.is_sent == False
        )
        notification = (await session.execute(stmt)).scalars().first()

        if not notification:
            return False

        # 获取用户信息
        user_stmt = select(User).where(User.id == notification.recipient_uid)
        user = (await session.execute(user_stmt)).scalars().first()

        if not user or not user.openid:
            logger.warning(f"User {notification.recipient_uid} has no openid, skip notification")
            return False

        wechat_apps = [
            pair.split(":")[0].strip()
            for pair in settings.WECHAT_APPS.split(",")
            if pair.strip() and pair.split(":")[0].strip()
        ]
        if not wechat_apps:
            logger.warning("No wechat app configured, skip notification %s", notification.id)
            return False

        # 发送微信消息
        try:
            message = f"{notification.title}\n\n{notification.content}"
            sent = await WeChatService.send_customer_message(
                appid=wechat_apps[0],
                openid=user.openid,
                content=message
            )
            if not sent:
                logger.warning("Wechat notification send failed for %s", notification.id)
                return False

            notification.is_sent = True
            notification.sent_at = datetime.now(timezone.utc)
            await session.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to send wechat notification: {e}")
            return False


# ==================== 统计服务 ====================

class StatsService:
    """统计服务"""

    @classmethod
    async def get_home_stats(cls, session: AsyncSession, couple_id: UUID) -> dict:
        """获取首页统计数据"""
        # 统计已完成的待办数量
        completed_todos_stmt = select(func.count(TodoItem.id)).where(
            TodoItem.couple_id == couple_id,
            TodoItem.status == "completed",
            TodoItem.is_deleted == False
        )
        completed_todos = (await session.execute(completed_todos_stmt)).scalar() or 0

        # 统计备忘录总数
        total_memos_stmt = select(func.count(Memo.id)).where(
            Memo.couple_id == couple_id,
            Memo.is_deleted == False
        )
        total_memos = (await session.execute(total_memos_stmt)).scalar() or 0

        # 统计已实现的心愿数量
        fulfilled_wishes_stmt = select(func.count(WishlistItem.id)).where(
            WishlistItem.couple_id == couple_id,
            WishlistItem.status == "fulfilled",
            WishlistItem.is_deleted == False
        )
        fulfilled_wishes = (await session.execute(fulfilled_wishes_stmt)).scalar() or 0

        # 统计心情日记数量
        from apps.just_right.models import MoodLog
        mood_logs_stmt = select(func.count(MoodLog.id)).where(
            MoodLog.couple_id == couple_id,
            MoodLog.is_deleted == False
        )
        mood_logs_count = (await session.execute(mood_logs_stmt)).scalar() or 0

        return {
            "completed_todos": completed_todos,
            "total_memos": total_memos,
            "fulfilled_wishes": fulfilled_wishes,
            "mood_logs_count": mood_logs_count
        }


# ==================== 搜索服务 ====================

class SearchService:
    """全局搜索服务"""

    @classmethod
    async def global_search(
        cls, session: AsyncSession, couple_id: UUID, keyword: str,
        search_type: Optional[str] = None, limit: int = 20
    ) -> dict:
        """全局搜索（备忘录+待办）"""
        results = {
            "memos": [],
            "todos": [],
            "total": 0
        }

        # 搜索备忘录
        if search_type in [None, "memo", "all"]:
            memo_stmt = select(Memo).where(
                Memo.couple_id == couple_id,
                Memo.content.ilike(f"%{keyword}%"),
                Memo.is_deleted == False
            ).order_by(
                Memo.is_pinned.desc(),
                Memo.created_at.desc()
            ).limit(limit)

            memo_result = await session.execute(memo_stmt)
            memos = list(memo_result.scalars().all())

            # 构建 MemoOut
            memo_outs = [await MemoService._build_memo_out(session, m) for m in memos]
            results["memos"] = memo_outs

        # 搜索待办
        if search_type in [None, "todo", "all"]:
            todo_stmt = select(TodoItem).where(
                TodoItem.couple_id == couple_id,
                TodoItem.content.ilike(f"%{keyword}%"),
                TodoItem.is_deleted == False
            ).order_by(TodoItem.created_at.desc()).limit(limit)

            todo_result = await session.execute(todo_stmt)
            results["todos"] = list(todo_result.scalars().all())

        results["total"] = len(results["memos"]) + len(results["todos"])
        return results
