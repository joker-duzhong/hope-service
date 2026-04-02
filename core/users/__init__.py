"""
统一用户中心 —— 注册、登录、权限判定
"""
from core.users.router import router
from core.users.dependencies import get_current_user, get_current_superuser, require_roles, require_role_in_scope

__all__ = ["router", "get_current_user", "get_current_superuser", "require_roles", "require_role_in_scope"]
