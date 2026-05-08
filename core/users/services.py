"""
用户中心 —— 核心业务逻辑（不含 HTTP 请求处理）
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequestException
from core.security import get_password_hash, verify_password
from core.sms import verify_sms_code
from core.storage.services import StorageService
from core.users.models import User
from core.users.schemas import UserAvatarResponse, UserResponse

class UserService:
    """用户服务：CRUD 与认证逻辑"""

    # ==================== 查询 ====================

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: UUID) -> Optional[User]:
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
    async def get_by_email(db: AsyncSession, email: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.email == email))
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
        await UserService.ensure_unique_profile_fields(
            db,
            username=username,
            email=email,
            phone=phone,
        )

        user = User(
            username=username,
            hashed_password=get_password_hash(password),
            phone=phone,
            email=email,
            nickname=nickname,
            source=source,
            roles=[],
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
            roles=[],
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def register_with_phone(
        db: AsyncSession,
        phone: str,
        code: str,
        password: Optional[str] = None,
        nickname: Optional[str] = None,
        source: str = "phone",
    ) -> Optional[User]:
        # 验证码检查
        is_valid = await verify_sms_code(phone, "register", code)
        if not is_valid:
            return None

        # 检查是否已注册
        user = await UserService.get_by_phone(db, phone)
        if user:
            return None

        # 默认用户名：基于手机号生成，若冲突则追加后缀
        username = f"user_{phone}"
        suffix = 1
        while await UserService.get_by_username(db, username):
            username = f"user_{phone}_{suffix}"
            suffix += 1

        hashed_pw = get_password_hash(password) if password else None
        
        new_user = User(
            username=username,
            hashed_password=hashed_pw,
            phone=phone,
            nickname=nickname or f"手机用户{phone[-4:]}",
            source=source,
            roles=[],
        )
        db.add(new_user)
        await db.commit()
        await db.refresh(new_user)
        return new_user

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
        username: Optional[str] = None,
        email: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        await UserService.ensure_unique_profile_fields(
            db,
            current_user_id=user.id,
            username=username,
            email=email,
        )

        if username is not None:
            user.username = username
        if email is not None:
            user.email = email
        if nickname is not None:
            user.nickname = nickname
        if avatar is not None:
            user.avatar = avatar
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    async def ensure_unique_profile_fields(
        db: AsyncSession,
        current_user_id: Optional[UUID] = None,
        username: Optional[str] = None,
        email: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> None:
        """校验用户资料字段唯一性，允许当前用户保留原值"""
        if username is not None:
            existing = await UserService.get_by_username(db, username)
            if existing and existing.id != current_user_id:
                raise BadRequestException(message="用户名已存在")

        if email is not None:
            existing = await UserService.get_by_email(db, email)
            if existing and existing.id != current_user_id:
                raise BadRequestException(message="邮箱已存在")

        if phone is not None:
            existing = await UserService.get_by_phone(db, phone)
            if existing and existing.id != current_user_id:
                raise BadRequestException(message="手机号已被注册")

    @staticmethod
    async def build_user_response(db: AsyncSession, user: User) -> UserResponse:
        data = UserResponse.model_validate(user)
        data.avatar = await UserService.resolve_avatar(db, user.avatar)
        return data

    @staticmethod
    async def resolve_avatar(
        db: AsyncSession,
        avatar: Optional[str],
    ) -> Optional[UserAvatarResponse]:
        if not avatar:
            return None

        try:
            resource_id = UUID(avatar)
        except (ValueError, TypeError):
            return UserAvatarResponse(url=avatar)

        resource = await StorageService.get_resource_response_or_none(db, resource_id)
        if not resource:
            return UserAvatarResponse(url=avatar)

        return UserAvatarResponse.model_validate(resource.model_dump())

    @staticmethod
    async def bind_phone(
        db: AsyncSession,
        user: User,
        phone: str,
        code: str,
    ) -> Optional[User]:
        # 验证码检查
        is_valid = await verify_sms_code(phone, "bind", code)
        if not is_valid:
            return None

        # 检查手机号是否已被他人绑定
        existing = await UserService.get_by_phone(db, phone)
        if existing and existing.id != user.id:
            return None

        user.phone = phone
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)
        return user
