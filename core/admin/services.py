"""
管理后台业务逻辑
"""
from typing import List, Optional, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.roles.models import Role
from core.users.models import User


class AdminUserService:
    """管理后台 —— 用户管理"""

    @staticmethod
    async def list_users(
        db: AsyncSession,
        keyword: Optional[str] = None,
        is_active: Optional[bool] = None,
        role_code: Optional[str] = None,
        source: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> Tuple[List[User], int]:
        """分页查询用户列表，返回 (users, total)"""
        stmt = (
            select(User)
            .where(User.is_deleted == False)  # noqa: E712
            .options(selectinload(User.roles))
        )

        # 关键词搜索
        if keyword:
            like = f"%{keyword}%"
            stmt = stmt.where(
                or_(
                    User.nickname.ilike(like),
                    User.username.ilike(like),
                    User.phone.ilike(like),
                    User.openid.ilike(like),
                )
            )

        # 状态筛选
        if is_active is not None:
            stmt = stmt.where(User.is_active == is_active)

        # 来源筛选
        if source:
            stmt = stmt.where(User.source == source)

        # 角色筛选（联接查询）
        if role_code:
            stmt = stmt.join(User.roles).where(Role.code == role_code)

        # 计算总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total: int = (await db.execute(count_stmt)).scalar_one()

        # 分页
        offset = (page - 1) * page_size
        stmt = stmt.order_by(User.created_at.desc()).offset(offset).limit(page_size)
        result = await db.execute(stmt)
        users = list(result.scalars().all())

        return users, total

    @staticmethod
    async def get_user_detail(db: AsyncSession, user_id: int) -> Optional[User]:
        """获取用户详情（含角色）"""
        result = await db.execute(
            select(User)
            .where(User.id == user_id, User.is_deleted == False)  # noqa: E712
            .options(selectinload(User.roles))
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def set_active(db: AsyncSession, user: User, is_active: bool) -> User:
        """冻结 / 解冻用户"""
        user.is_active = is_active
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def assign_roles(
        db: AsyncSession, user: User, role_ids: List[int]
    ) -> User:
        """覆盖式分配角色：以传入的 role_ids 为准，替换用户当前所有角色"""
        if role_ids:
            result = await db.execute(
                select(Role).where(Role.id.in_(role_ids), Role.is_active == True)  # noqa: E712
            )
            new_roles = list(result.scalars().all())
        else:
            new_roles = []

        user.roles = new_roles
        await db.commit()
        await db.refresh(user)
        return user


class RoleService:
    """角色 CRUD"""

    @staticmethod
    async def get_all(
        db: AsyncSession, scope: Optional[str] = None
    ) -> List[Role]:
        stmt = select(Role)
        if scope:
            stmt = stmt.where(Role.scope == scope)
        stmt = stmt.order_by(Role.scope, Role.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def get_by_id(db: AsyncSession, role_id: int) -> Optional[Role]:
        result = await db.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_scope_code(
        db: AsyncSession, scope: str, code: str
    ) -> Optional[Role]:
        """scope + code 联合查找（唯一约束基于此）"""
        result = await db.execute(
            select(Role).where(Role.scope == scope, Role.code == code)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def create(
        db: AsyncSession,
        name: str,
        code: str,
        scope: str = "global",
        description: Optional[str] = None,
    ) -> Role:
        role = Role(name=name, code=code, scope=scope, description=description)
        db.add(role)
        await db.commit()
        await db.refresh(role)
        return role

    @staticmethod
    async def update(
        db: AsyncSession,
        role: Role,
        name: Optional[str] = None,
        description: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Role:
        if name is not None:
            role.name = name
        if description is not None:
            role.description = description
        if is_active is not None:
            role.is_active = is_active
        await db.commit()
        await db.refresh(role)
        return role

    @staticmethod
    async def delete(db: AsyncSession, role: Role) -> None:
        await db.delete(role)
        await db.commit()
