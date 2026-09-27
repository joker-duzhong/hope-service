from contextvars import ContextVar
from typing import TYPE_CHECKING

from fastapi import HTTPException

from core.apps_config import REGISTERED_APPS

if TYPE_CHECKING:
    from core.roles.models import Role
    from core.users.models import User

PASSPORT_SCOPE = "passport"
ADMIN_SCOPE = "admin_web"
# 只登记已有管理接口的业务角色，不根据名称或后缀推断后台权限。
ADMIN_ROLE_CODES: dict[str, frozenset[str]] = {
    "hope_aurakey": frozenset({"aurakey_admin"}),
    "hope_ledger_mate": frozenset({"ledger_mate_admin"}),
}
current_app_key: ContextVar[str | None] = ContextVar("current_app_key", default=None)
current_admin_app: ContextVar[str | None] = ContextVar("current_admin_app", default=None)


def is_admin_role(role: "Role") -> bool:
    app = REGISTERED_APPS.get(role.scope)
    return bool(
        role.is_active and not role.is_deleted
        and app is not None and app.is_active
        and role.code in ADMIN_ROLE_CODES.get(role.scope, frozenset())
    )


def validate_scope(scope: str, user: "User | None" = None) -> None:
    if scope != PASSPORT_SCOPE:
        app = REGISTERED_APPS.get(scope)
        if app is None or not app.is_active:
            raise HTTPException(403, "应用不存在或已停用")
    if user is not None and scope == ADMIN_SCOPE:
        if not user.is_superuser and not any(is_admin_role(role) for role in user.roles):
            raise HTTPException(403, "当前账号没有管理后台访问权限")


def validate_access_scope(payload: dict) -> str:
    scope = payload.get("app_scope")
    if not isinstance(scope, str) or not scope:
        raise HTTPException(401, "登录版本已更新，请重新登录")
    validate_scope(scope)
    expected = current_app_key.get()
    if expected is not None and expected != scope:
        # 仅服务器显式标记的业务管理路由接受统一后台凭据。
        if scope != ADMIN_SCOPE or current_admin_app.get() != expected:
            raise HTTPException(403, "当前登录凭据不适用于此应用，请在对应应用重新登录")
        validate_scope(expected)
    return scope
