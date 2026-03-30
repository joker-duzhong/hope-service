"""
认证服务
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import get_password_hash, verify_password
from app.models.user import User
from app.schemas.user import UserCreate


class AuthService:
    """认证服务类"""

    # ==================== 用户查询 ====================

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: int) -> Optional[User]:
        """根据ID获取用户"""
        result = await db.execute(
            select(User).where(User.id == user_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_openid(db: AsyncSession, openid: str) -> Optional[User]:
        """根据微信openid获取用户"""
        result = await db.execute(
            select(User).where(User.openid == openid)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_unionid(db: AsyncSession, unionid: str) -> Optional[User]:
        """根据微信unionid获取用户"""
        result = await db.execute(
            select(User).where(User.unionid == unionid)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_username(db: AsyncSession, username: str) -> Optional[User]:
        """根据用户名获取用户"""
        result = await db.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
        """根据手机号获取用户"""
        result = await db.execute(
            select(User).where(User.phone == phone)
        )
        return result.scalar_one_or_none()

    # ==================== 用户创建 ====================

    @staticmethod
    async def create_by_wechat(
        db: AsyncSession,
        openid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
        source: str = "wechat",
    ) -> User:
        """通过微信创建用户"""
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
        """通过用户名创建用户"""
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

    # ==================== 微信登录 ====================

    @staticmethod
    async def wechat_login(
        db: AsyncSession,
        openid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        """
        微信登录，自动注册新用户

        Args:
            db: 数据库会话
            openid: 微信openid
            unionid: 微信unionid（可选）
            nickname: 用户昵称
            avatar: 用户头像

        Returns:
            User对象
        """
        # 先通过openid查找
        user = await AuthService.get_by_openid(db, openid)

        if not user:
            # 通过unionid查找（用户可能在其他公众号授权过）
            if unionid:
                user = await AuthService.get_by_unionid(db, unionid)
                if user:
                    # 更新openid
                    user.openid = openid
                    await db.commit()
                    await db.refresh(user)

        if not user:
            # 创建新用户
            user = await AuthService.create_by_wechat(
                db,
                openid=openid,
                unionid=unionid,
                nickname=nickname,
                avatar=avatar,
            )

        return user

    # ==================== 密码登录 ====================

    @staticmethod
    async def authenticate(
        db: AsyncSession, username: str, password: str
    ) -> Optional[User]:
        """验证用户名密码登录"""
        user = await AuthService.get_by_username(db, username)
        if not user:
            return None
        if not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user

    # ==================== 手机号登录（预留） ====================

    @staticmethod
    async def login_by_phone(
        db: AsyncSession,
        phone: str,
        sms_code: str,
    ) -> Optional[User]:
        """
        手机号验证码登录（预留）

        Args:
            db: 数据库会话
            phone: 手机号
            sms_code: 短信验证码

        Returns:
            User对象
        """
        # TODO: 验证短信验证码
        # 1. 从Redis获取验证码
        # 2. 验证验证码是否正确
        # 3. 验证通过后删除验证码

        user = await AuthService.get_by_phone(db, phone)
        if not user:
            # 自动注册
            user = User(
                phone=phone,
                source="sms",
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)

        return user

    # ==================== 用户更新 ====================

    @staticmethod
    async def update_user_info(
        db: AsyncSession,
        user: User,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        """更新用户信息"""
        if nickname:
            user.nickname = nickname
        if avatar:
            user.avatar = avatar
        user.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def bind_phone(
        db: AsyncSession,
        user: User,
        phone: str,
    ) -> User:
        """绑定手机号"""
        user.phone = phone
        user.updated_at = datetime.utcnow()
        await db.commit()
        await db.refresh(user)
        return user
