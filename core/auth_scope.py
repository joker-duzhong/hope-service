from contextvars import ContextVar

from fastapi import HTTPException

from core.apps_config import REGISTERED_APPS

PASSPORT_SCOPE = "passport"
current_app_key: ContextVar[str | None] = ContextVar("current_app_key", default=None)


def validate_scope(scope: str, user=None) -> None:
    if scope != PASSPORT_SCOPE:
        app = REGISTERED_APPS.get(scope)
        if app is None or not app.is_active:
            raise HTTPException(403, "应用不存在或已停用")
    if user is not None and scope == "admin_web" and not user.is_superuser:
        raise HTTPException(403, "该应用仅允许超级管理员登录")


def validate_access_scope(payload: dict) -> str:
    scope = payload.get("app_scope")
    if not isinstance(scope, str) or not scope:
        raise HTTPException(401, "登录版本已更新，请重新登录")
    validate_scope(scope)
    expected = current_app_key.get()
    if expected is not None and expected != scope:
        raise HTTPException(403, "当前登录凭据不适用于此应用，请在对应应用重新登录")
    return scope
