"""
基础全局依赖 (Dependencies)
如请求头解析等通用组件，与具体业务解耦
"""
from contextvars import ContextVar
from typing import AsyncGenerator, Callable
from fastapi import HTTPException, Depends, status

from core.apps_config import REGISTERED_APPS
from core.users.models import User
from core.users.dependencies import get_current_user

_current_app_key: ContextVar[str | None] = ContextVar("current_app_key", default=None)

def bind_app_key(app_key: str) -> Callable[[], AsyncGenerator[None, None]]:
    """Create a router dependency that fixes the trusted application scope."""
    if app_key not in REGISTERED_APPS:
        raise ValueError(f"未知应用配置: {app_key}")

    async def _bind() -> AsyncGenerator[None, None]:
        token = _current_app_key.set(app_key)
        try:
            yield
        finally:
            _current_app_key.reset(token)

    return _bind


async def get_app_key() -> str:
    """
    返回当前后端路由注册时绑定的应用标识，绝不信任客户端 Header。
    """
    app_key = _current_app_key.get()
    if not app_key or app_key not in REGISTERED_APPS:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="当前接口未绑定应用上下文",
        )

    app_config = REGISTERED_APPS[app_key]
    if not app_config.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"当前应用 [{app_config.name}] 已停用"
        )

    return app_key


# ==================== 2. 结合请求头的动态角色权限工厂 ====================

def require_app_roles(*role_codes: str) -> Callable:
    """
    自动读取请求头 app (作为scope) 的角色权限工厂。

    用法::

        @router.get("/my-vip-feature")
        async def premium_feature(
            app_key: str = Depends(get_app_key),
            user: User = Depends(require_app_roles("vip", "svip"))
        ):
            ...
    等价于: 要求该用户在请求来源的 app 产线下拥有 vip 或 svip 角色。
    """
    
    async def _checker(
        current_app: str = Depends(get_app_key),
        current_user: User = Depends(get_current_user)
    ) -> User:
        if current_user.is_superuser:
            return current_user
            
        # 根据请求头传过来的 current_app(scope)，检查用户在这个 scope 下的角色 code
        user_scope_codes = {(r.scope, r.code) for r in current_user.roles}
        
        matched = any(
            (current_app, code) in user_scope_codes for code in role_codes
        )
        
        if not matched:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"在应用入口 ({current_app}) 中，缺乏所需的角色权限: {', '.join(role_codes)}",
            )
            
        return current_user

    return _checker
