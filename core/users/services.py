"""
用户中心 —— 核心业务逻辑（不含 HTTP 请求处理）
"""
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequestException, ForbiddenException
from core.security import create_token_pair, get_password_hash, verify_password
from core.sms import normalize_phone, verify_sms_code
from core.storage.services import StorageService
from core.users.models import User, UserIdentity
from core.users.schemas import LoginResponse, UserAvatarResponse, UserResponse
from core.auth_scope import ADMIN_SCOPE, PASSPORT_SCOPE, is_admin_role, validate_scope

class UserService:
    """用户服务：CRUD 与认证逻辑"""

    # ==================== 查询 ====================

    @staticmethod
    async def get_by_id(db: AsyncSession, user_id: UUID) -> Optional[User]:
        result = await db.execute(select(User).where(User.id == user_id, User.is_deleted == False))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_openid(db: AsyncSession, openid: str) -> Optional[User]:
        result = await db.execute(select(User).where(User.openid == openid))
        return result.scalar_one_or_none()

    @staticmethod
    async def get_by_wechat_identity(
        db: AsyncSession, appid: str, openid: str
    ) -> Optional[User]:
        result = await db.execute(
            select(User).join(UserIdentity, UserIdentity.user_id == User.id).where(
                UserIdentity.provider == "wechat",
                UserIdentity.provider_app_id == appid,
                UserIdentity.subject == openid,
                UserIdentity.is_deleted == False,
            )
        )
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
        appid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
        source: str = "wechat",
    ) -> User:
        user = User(
            nickname=nickname,
            avatar=avatar,
            source=source,
            roles=[],
        )
        db.add(user)
        await db.flush()
        db.add(UserIdentity(
            user_id=user.id,
            provider="wechat",
            provider_app_id=appid,
            subject=openid,
            unionid=unionid,
            verified_at=datetime.now(timezone.utc),
        ))
        await db.commit()
        await db.refresh(user)
        return user

    @staticmethod
    def ensure_login_allowed(user: User) -> None:
        if user.is_deleted or not user.is_active:
            raise ForbiddenException(message="账号已停用或注销，无法登录")

    @staticmethod
    async def login_with_phone(db: AsyncSession, phone: str, code: str) -> User:
        phone = normalize_phone(phone)
        if not await verify_sms_code(phone, code):
            raise BadRequestException(message="验证码错误或已过期，请重新获取")
        user = await UserService.get_by_phone(db, phone)
        if user:
            UserService.ensure_login_allowed(user)
            return user
        user = User(phone=phone, nickname="手机用户", source="phone", roles=[])
        db.add(user)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            user = await UserService.get_by_phone(db, phone)
            if user is None:
                raise
        else:
            await db.refresh(user)
        UserService.ensure_login_allowed(user)
        return user

    @staticmethod
    async def build_login_response(db: AsyncSession, user: User, app_scope: str = PASSPORT_SCOPE) -> LoginResponse:
        UserService.ensure_login_allowed(user)
        validate_scope(app_scope, user)
        if not user.phone and not (app_scope == "admin_web" and user.is_superuser):
            raise BadRequestException(message="请通过两阶段身份登录接口验证手机号")
        profile = await UserService.build_scoped_user_response(db, user, app_scope)
        access_token, refresh_token = await create_token_pair(user.id, user.token_version, app_scope)
        return LoginResponse(
            access_token=access_token, refresh_token=refresh_token, user=profile, app_scope=app_scope,
        )

    # ==================== 认证 ====================

    @staticmethod
    async def authenticate(
        db: AsyncSession, username: str, password: str
    ) -> Optional[User]:
        user = await UserService.get_by_username(db, username)
        if not user or user.is_deleted or not user.is_active or not user.is_superuser or not user.hashed_password:
            return None
        if not verify_password(password, user.hashed_password):
            return None
        if not user.is_active:
            return None
        return user

    @staticmethod
    async def _get_wechat_login_user(
        db: AsyncSession, appid: str, openid: str,
    ) -> Optional[User]:
        result = await db.execute(select(UserIdentity).where(
            UserIdentity.provider == "wechat",
            UserIdentity.provider_app_id == appid,
            UserIdentity.subject == openid,
        ))
        identity = result.scalar_one_or_none()
        if identity is None:
            return None
        if identity.is_deleted:
            raise ForbiddenException(message="微信登录身份已停用")
        user = await UserService.get_by_id(db, identity.user_id)
        if user is None:
            raise ForbiddenException(message="账号已停用或注销，无法登录")
        UserService.ensure_login_allowed(user)
        return user

    @staticmethod
    async def wechat_login(
        db: AsyncSession,
        openid: str,
        appid: str,
        unionid: Optional[str] = None,
        nickname: Optional[str] = None,
        avatar: Optional[str] = None,
    ) -> User:
        """Legacy login only accepts an already linked account with a verified phone."""
        if not appid or not openid:
            raise BadRequestException(message="微信登录身份无效")
        user = await UserService._get_wechat_login_user(db, appid, openid)
        if user is not None and user.phone:
            return user
        raise BadRequestException(message="该身份尚未完成手机号验证，请使用 /auth/identity 两阶段登录")

    @staticmethod
    async def get_wechat_openid(
        db: AsyncSession, user_id: UUID, appid: str
    ) -> Optional[str]:
        result = await db.execute(
            select(UserIdentity.subject).where(
                UserIdentity.user_id == user_id,
                UserIdentity.provider == "wechat",
                UserIdentity.provider_app_id == appid,
                UserIdentity.is_deleted == False,
            )
        )
        subjects = result.scalars().all()
        if len(subjects) > 1:
            raise BadRequestException(message="当前应用关联了多个微信身份，请通过新的微信验证明确操作身份")
        return subjects[0] if subjects else None

    @staticmethod
    async def revoke_tokens(db: AsyncSession, user: User) -> None:
        user.token_version += 1
        user.updated_at = datetime.now(timezone.utc)
        await db.commit()
        await db.refresh(user)

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
    async def build_scoped_user_response(db: AsyncSession, user: User, app_scope: str) -> UserResponse:
        data = await UserService.build_user_response(db, user)
        data.openid = None
        if app_scope == ADMIN_SCOPE:
            active_role_ids = {role.id for role in user.roles if is_admin_role(role)}
        else:
            active_role_ids = {role.id for role in user.roles
                               if role.is_active and not role.is_deleted and role.scope == app_scope}
        data.roles = [role for role in data.roles if role.id in active_role_ids]
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
        db: AsyncSession, user: User, phone: str, code: str,
    ) -> User:
        phone = normalize_phone(phone)
        UserService.ensure_login_allowed(user)
        if user.phone == phone:
            return await UserService.bind_verified_phone(db, user, phone)
        if user.phone and user.phone != phone:
            raise BadRequestException(message="暂不支持更换已绑定的手机号")
        if not await verify_sms_code(phone, code):
            raise BadRequestException(message="验证码错误或已过期，请重新获取")
        return await UserService.bind_verified_phone(db, user, phone)

    @staticmethod
    async def bind_verified_phone(db: AsyncSession, user: User, phone: str) -> User:
        """仅供已完成短信或微信手机号验证的后端调用。"""
        try:
            phone = normalize_phone(phone)
        except ValueError:
            raise BadRequestException(message="仅支持绑定中国大陆手机号") from None
        result = await db.execute(
            select(User).where(User.id == user.id).with_for_update()
            .execution_options(populate_existing=True)
        )
        locked_user = result.scalar_one_or_none()
        if locked_user is None:
            await db.rollback()
            raise ForbiddenException(message="账号不可用")
        try:
            UserService.ensure_login_allowed(locked_user)
            if locked_user.phone == phone:
                await db.commit()
                return locked_user
            if locked_user.phone:
                raise BadRequestException(message="暂不支持更换已绑定的手机号")
            existing = await UserService.get_by_phone(db, phone)
            if existing and existing.id != locked_user.id:
                raise BadRequestException(message="手机号已属于其他账号，请使用原账号登录，暂不支持合并")
            locked_user.phone = phone
            locked_user.updated_at = datetime.now(timezone.utc)
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise BadRequestException(message="手机号已被占用，请使用原账号登录") from None
        except (BadRequestException, ForbiddenException):
            await db.rollback()
            raise
        await db.refresh(locked_user)
        return locked_user
