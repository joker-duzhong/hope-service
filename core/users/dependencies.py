"""
用户认证 FastAPI Depends
"""
from typing import Callable, List, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.security import decode_token
from core.users.models import User
from core.users.services import UserService

security = HTTPBearer()


async def get_current_user(
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

    user_id: Optional[int] = payload.get("sub")
    if user_id is None:
        raise credentials_exception

    user = await UserService.get_by_id(db, user_id)
    if user is None:
        raise credentials_exception

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="用户已被禁用"
        )

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
    匹配的是角色 code，不区分 scope。

    用法::

        @router.get("/vip-only")
        async def vip_only(user: User = Depends(require_roles("vip", "admin"))):
            ...
    """

    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superuser:
            return current_user
        user_role_codes = {r.code for r in current_user.roles}
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

    async def _checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.is_superuser:
            return current_user
        # 将用户角色按照 (scope, code) 组合映射
        user_scope_codes = {(r.scope, r.code) for r in current_user.roles}
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
