"""
用户认证 FastAPI Depends
"""
from typing import Callable, List, Optional
from uuid import UUID

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import decode_token
from core.users.models import User
from core.users.services import UserService
from core.auth_scope import validate_access_scope, validate_scope

security = HTTPBearer()
security_optional = HTTPBearer(auto_error=False)


async def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security_optional),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """获取当前登录用户（可选），未登录时返回 None"""
    if not credentials:
        return None
    try:
        payload = decode_token(credentials.credentials)
        if payload is None or payload.get("type") != "access":
            return None
        scope = validate_access_scope(payload)
        user_id_str: Optional[str] = payload.get("sub")
        if not user_id_str:
            return None
        user_id = UUID(user_id_str)
        user = await UserService.get_by_id(db, user_id)
        if not user or not user.is_active or payload.get("token_version") != user.token_version:
            return None
        validate_scope(scope, user)
        if not user.phone and not (scope == "admin_web" and user.is_superuser):
            return None
        return user
    except Exception:
        return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> User:
    """获取当前登录用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )

    payload = decode_token(credentials.credentials)
    if payload is None or payload.get("type") != "access":
        raise credentials_exception
    scope = validate_access_scope(payload)
    request.state.auth_scope = scope

    user_id: Optional[str] = payload.get("sub")
    if user_id is None:
        raise credentials_exception
    try:
        user_id = UUID(user_id)
    except (ValueError, TypeError):
        raise credentials_exception

    user = await UserService.get_by_id(db, user_id)
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用"
        )

    if payload.get("token_version") != user.token_version:
        raise credentials_exception
    validate_scope(scope, user)
    if not user.phone and not (scope == "admin_web" and user.is_superuser):
        raise HTTPException(401, "请重新验证微信身份并完成手机号验证")

    return user


async def get_current_superuser(
    current_user: User = Depends(get_current_user),
) -> User:
    """获取当前超级管理员"""
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="需要超级管理员权限"
        )
    return current_user


def require_roles(*role_codes: str) -> Callable:
    """
    角色权限依赖工厂，用于需要特定角色才能访问的接口。
    同时匹配当前凭据的应用 scope 和角色 code。

    用法::

        @router.get("/vip-only")
        async def vip_only(user: User = Depends(require_roles("vip", "admin"))):
            ...
    """

    async def _checker(request: Request, current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superuser:
            return current_user
        user_role_codes = {role.code for role in current_user.roles
                           if role.is_active and not role.is_deleted and role.scope == request.state.auth_scope}
        if not user_role_codes.intersection(set(role_codes)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(role_codes)}",
            )
        return current_user

    return _checker


def require_role_in_scope(scope: str, *role_codes: str) -> Callable:
    """
    作用域角色权限依赖工厂。同时检查 scope 和 code，
    适用于多业务产线各自有自己的 vip 等角色的场景。

    用法::

        # hope_care 产线路由
        @router.get("/premium")
        async def premium(
            user: User = Depends(require_role_in_scope("hope_care", "vip"))
        ):
            ...

        # hope_trade 产线路由
        @router.get("/trading")
        async def trading(
            user: User = Depends(require_role_in_scope("hope_trade", "vip", "pro"))
        ):
            ...
    """

    async def _checker(request: Request, current_user: User = Depends(get_current_user)) -> User:
        if request.state.auth_scope != scope:
            raise HTTPException(403, "当前登录凭据不适用于所需应用")
        if current_user.is_superuser:
            return current_user
        # 将用户角色按照 (scope, code) 组合映射
        user_scope_codes = {(role.scope, role.code) for role in current_user.roles if role.is_active and not role.is_deleted}
        # 确认指定 scope 下至少有一个 code 命中
        matched = any(
            (scope, code) in user_scope_codes for code in role_codes
        )
        if not matched:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"[{scope}] 需要以下角色之一: {', '.join(role_codes)}",
            )
        return current_user

    return _checker
