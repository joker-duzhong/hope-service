"""
用户中心 —— 核心业务逻辑（不含 HTTP 请求处理）
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.security import get_password_hash, verify_password
from core.users.models import User
from core.users.schemas import UserCreate


class UserService:
    """用户服务：CRUD 与认证逻辑"""

    # ==================== 查询 ====================

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_openid(db: AsyncSession, openid: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.openid == openid))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_unionid(db: AsyncSession, unionid: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.unionid == unionid))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.phone == phone))
        return result.scalar_one_or_none()

    # ==================== 创建 ====================

    @staticmethod
    async def create_by_username(
        db: AsyncSession,
        username: str,
        password: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        nickname: Optional[str] = None,
        source: str = "default",
    ) -> User:
        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            phone=phone,
            email=email,
            nickname=nickname,
            source=source,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def create_by_wechat(
        db: AsyncSession,
        openid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
        source: str = "wechat",
    ) -> User:
        user = User(
            openid=openid,
            unionid=unionid,
            nickname=nickname,
            avatar=avatar,
            source=source,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    # ==================== 认证 ====================

    @staticmethod
    async def authenticate(
        db: AsyncSession, username: str, password: str
    ) -> Optional[User]:
        user = await UserService.get_by_username(db, username)
        if not user or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user

    @staticmethod
    async def wechat_login(
        db: AsyncSession,
        openid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        """微信登录，自动注册新用户"""
        user = await UserService.get_by_openid(db, openid)

        if not user and unionid:
            user = await UserService.get_by_unionid(db, unionid)
            if user:
                user.openid = openid
                await db.commit()
                await db.refresh(user)

        if not user:
            user = await UserService.create_by_wechat(
                db, openid=openid, unionid=unionid,
                nickname=nickname, avatar=avatar,
            )

        return user

    # ==================== 更新 ====================

    @staticmethod
    async def update_user_info(
        db: AsyncSession,
        user: User,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        if nickname is not None:
            user.nickname = nickname
        if avatar is not None:
            user.avatar = avatar
        user.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        return user
