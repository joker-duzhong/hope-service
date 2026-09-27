"""统一后台准入、角色目录和管理路由隔离；仅使用内存 Redis 与模拟数据库。"""
import asyncio
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.aurakey import router as aurakey_router
from core import auth_scope, dependencies, security
from core.admin.services import RoleService
from core.apps_config import AppConfig
from core.database import get_db
from core.dependencies import bind_admin_app, bind_app_key
from core.exceptions import register_exception_handlers
from core.users import scan_service, services
from core.users.dependencies import get_current_user, require_roles
from core.users.scan_router import router as scan_router
from core.users.services import UserService

auth_router = import_module("core.users.router")
admin_router = import_module("core.admin.router")


def make_role(**changes):
    values = dict(id=uuid4(), scope="hope_aurakey", code="aurakey_admin", name="业务管理员",
                  is_active=True, is_deleted=False)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.fixture
def user():
    return SimpleNamespace(id=uuid4(), phone="13800138000", nickname="后台用户", avatar=None, username=None,
                           email=None, openid=None, source="wechat", roles=[make_role()],
                           is_active=True, is_deleted=False, is_superuser=False, token_version=0)


@pytest.fixture
async def api(monkeypatch, user):
    apps = {name: AppConfig(key=name, name=name, created_at="2026-09-27")
            for name in ("admin_web", "hope_aurakey", "hope_just_right")}
    apps["hope_time_library"] = AppConfig(key="hope_time_library", name="停用应用", created_at="2026-09-27", is_active=False)
    for module in (auth_scope, dependencies, scan_service):
        monkeypatch.setattr(module, "REGISTERED_APPS", apps)
    redis = FakeRedis(decode_responses=True)
    monkeypatch.setattr(scan_service, "redis_client", redis)
    monkeypatch.setattr(security, "redis_client", redis)
    monkeypatch.setattr(security.settings, "SECRET_KEY", "isolated-admin-tests-only-signing-key")
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(UserService, "get_by_phone", AsyncMock(return_value=user))
    monkeypatch.setattr(services, "verify_sms_code", AsyncMock(return_value=True))
    monkeypatch.setattr(RoleService, "get_all", AsyncMock(return_value=[]))
    db = AsyncMock(spec=AsyncSession)
    app = FastAPI()
    app.dependency_overrides[get_db] = lambda: db
    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(scan_router, prefix="/api/v1")
    app.include_router(aurakey_router.router, prefix="/api/v1/aurakey", dependencies=[Depends(bind_app_key("hope_aurakey"))])
    app.include_router(admin_router.router, prefix="/api/v1", dependencies=[Depends(bind_app_key("admin_web"))])

    async def second_management(current_user=Depends(require_roles("other_manager"))):
        await asyncio.sleep(0)
        return {"scope": auth_scope.current_admin_app.get(), "user": str(current_user.id)}

    async def second_business(current_user=Depends(get_current_user)):
        await asyncio.sleep(0)
        return {"scope": auth_scope.current_admin_app.get(), "user": str(current_user.id)}

    app.add_api_route("/other/admin", second_management, dependencies=[
        Depends(bind_app_key("hope_just_right")), Depends(bind_admin_app("hope_just_right")),
    ])
    app.add_api_route("/other/user", second_business, dependencies=[Depends(bind_app_key("hope_just_right"))])
    register_exception_handlers(app)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client, redis
    finally:
        await redis.aclose()
    assert auth_scope.current_app_key.get() is None
    assert auth_scope.current_admin_app.get() is None


def headers(user, scope="admin_web"):
    return {"Authorization": "Bearer " + security.create_access_token(user.id, user.token_version, app_scope=scope)}


async def phone_login(client, user):
    return await client.post("/api/v1/auth/phone/login", json={"phone": user.phone, "code": "0123", "app_key": "admin_web"})


@pytest.mark.parametrize("superuser", [False, True])
async def test_sms_admin_login_returns_management_roles_and_supports_refresh_and_me(api, user, superuser):
    user.is_superuser = superuser
    if superuser:
        user.roles = []
    response = await phone_login(api[0], user)
    assert response.status_code == 200
    login = response.json()["data"]
    assert login["app_scope"] == "admin_web"
    assert security.decode_token(login["access_token"])["app_scope"] == "admin_web"
    assert [(r["scope"], r["code"]) for r in login["user"]["roles"]] == ([] if superuser else [("hope_aurakey", "aurakey_admin")])
    refreshed = await api[0].post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})
    assert refreshed.status_code == 200
    access = refreshed.json()["data"]["access_token"]
    assert security.decode_token(access)["app_scope"] == "admin_web"
    me = await api[0].get("/api/v1/auth/me", headers={"Authorization": "Bearer " + access})
    assert me.status_code == 200
    assert me.json()["data"]["roles"] == login["user"]["roles"]
    services.verify_sms_code.assert_awaited_once_with(user.phone, "0123")


@pytest.mark.parametrize("changes", [None, {"scope": "admin_web"}, {"scope": "hope_just_right"},
    {"scope": "global"}, {"scope": "hope_time_library", "code": "admin"}, {"code": "vip"},
    {"code": "invented_admin"}, {"is_active": False}, {"is_deleted": True}])
async def test_sms_login_rejects_non_management_and_forged_or_inactive_roles(api, user, changes):
    user.roles = [] if changes is None else [make_role(**changes)]
    response = await phone_login(api[0], user)
    assert response.status_code == 403
    assert not await api[1].keys("auth:refresh:*")


async def test_management_profile_includes_multiple_registered_roles_but_no_membership_roles(api, user, monkeypatch):
    monkeypatch.setattr(auth_scope, "ADMIN_ROLE_CODES", {
        **auth_scope.ADMIN_ROLE_CODES, "hope_just_right": frozenset({"other_manager"}),
        "hope_time_library": frozenset({"admin"}),
    })
    user.roles.extend([make_role(scope="hope_just_right", code="other_manager"), make_role(code="vip"),
                       make_role(is_active=False), make_role(is_deleted=True),
                       make_role(scope="hope_time_library", code="admin")])
    login = (await phone_login(api[0], user)).json()["data"]
    expected = [("hope_aurakey", "aurakey_admin"), ("hope_just_right", "other_manager")]
    assert [(role["scope"], role["code"]) for role in login["user"]["roles"]] == expected
    me = await api[0].get("/api/v1/auth/me", headers=headers(user))
    assert [(role["scope"], role["code"]) for role in me.json()["data"]["roles"]] == expected
    assert (await api[0].get("/other/admin", headers=headers(user))).status_code == 200
    business = await UserService.build_scoped_user_response(AsyncMock(), user, "hope_aurakey")
    assert [(role.scope, role.code) for role in business.roles] == [("hope_aurakey", "aurakey_admin"), ("hope_aurakey", "vip")]


async def begin_scan(client):
    response = await client.post("/api/v1/auth/scan/sessions", json={"app_key": "admin_web"})
    assert response.status_code == 200
    created = response.json()["data"]
    base = "/api/v1/auth/scan/sessions/" + created["transaction_id"]
    assert (await client.post(base + "/scanned")).status_code == 200
    return created, base


async def test_ordinary_manager_can_confirm_and_exchange_unified_scan(api, user):
    client = api[0]
    created, base = await begin_scan(client)
    assert (await client.post(base + "/confirm", headers=headers(user, "passport"))).status_code == 200
    scan_headers = {"X-Scan-Token": created["poll_token"]}
    poll = (await client.get(base, headers=scan_headers)).json()["data"]
    response = await client.post("/api/v1/auth/scan/exchange", headers=scan_headers, json={
        "transaction_id": created["transaction_id"], "exchange_code": poll["exchange_code"],
    })
    assert response.status_code == 200
    login = response.json()["data"]
    assert login["app_scope"] == "admin_web"
    assert login["user"]["roles"][0]["scope"] == "hope_aurakey"
    assert (await client.get("/api/v1/aurakey/admin/session", headers={"Authorization": "Bearer " + login["access_token"]})).status_code == 200


@pytest.mark.parametrize("revoked_at", ["confirm", "exchange"])
async def test_scan_revalidates_management_permission(api, user, revoked_at):
    client = api[0]
    created, base = await begin_scan(client)
    if revoked_at == "confirm":
        user.roles[0].is_active = False
        assert (await client.post(base + "/confirm", headers=headers(user, "passport"))).status_code == 403
    else:
        assert (await client.post(base + "/confirm", headers=headers(user, "passport"))).status_code == 200
        scan_headers = {"X-Scan-Token": created["poll_token"]}
        poll = (await client.get(base, headers=scan_headers)).json()["data"]
        user.roles[0].is_deleted = True
        response = await client.post("/api/v1/auth/scan/exchange", headers=scan_headers, json={
            "transaction_id": created["transaction_id"], "exchange_code": poll["exchange_code"],
        })
        assert response.status_code == 403
    assert not await api[1].keys("auth:refresh:*")


@pytest.mark.parametrize("scope,target,superuser,status", [
    ("admin_web", "/api/v1/aurakey/admin/session", False, 200),
    ("admin_web", "/api/v1/aurakey/admin/session", True, 200),
    ("admin_web", "/api/v1/aurakey/user/profile", False, 403),
    ("admin_web", "/api/v1/aurakey/user/profile", True, 403),
    ("admin_web", "/api/v1/admin/roles", False, 403),
    ("admin_web", "/api/v1/admin/roles", True, 200),
    ("admin_web", "/api/v1/admin/apps", False, 200),
    ("admin_web", "/api/v1/admin/apps", True, 200),
    ("hope_aurakey", "/api/v1/admin/apps", True, 403),
    ("passport", "/api/v1/admin/apps", True, 403),
    ("admin_web", "/other/admin", False, 403),
    ("admin_web", "/other/admin", True, 200),
    ("admin_web", "/other/user", True, 403),
    ("hope_aurakey", "/other/admin", True, 403),
    ("hope_aurakey", "/api/v1/admin/roles", True, 403),
    ("hope_just_right", "/api/v1/aurakey/admin/session", True, 403),
    ("passport", "/api/v1/aurakey/admin/session", True, 403),
])
async def test_only_management_routes_accept_admin_scope_and_enforce_target_role(api, user, scope, target, superuser, status):
    user.is_superuser = superuser
    assert (await api[0].get(target, headers={**headers(user, scope), "app": "admin_web"})).status_code == status


@pytest.mark.parametrize("change", ["disable_role", "delete_role", "remove_role", "disable_app"])
async def test_existing_admin_access_and_refresh_are_rejected_after_permission_revocation(api, user, change):
    login = (await phone_login(api[0], user)).json()["data"]
    if change == "disable_role":
        user.roles[0].is_active = False
    elif change == "delete_role":
        user.roles[0].is_deleted = True
    elif change == "remove_role":
        user.roles = []
    else:
        auth_scope.REGISTERED_APPS["hope_aurakey"].is_active = False
    for route in ("/api/v1/auth/me", "/api/v1/aurakey/admin/session"):
        assert (await api[0].get(route, headers={"Authorization": "Bearer " + login["access_token"]})).status_code == 403
    assert (await api[0].post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})).status_code == 403


async def test_remaining_management_role_does_not_keep_revoked_target_permission(api, user, monkeypatch):
    monkeypatch.setattr(auth_scope, "ADMIN_ROLE_CODES", {**auth_scope.ADMIN_ROLE_CODES, "hope_just_right": frozenset({"other_manager"})})
    user.roles.append(make_role(scope="hope_just_right", code="other_manager"))
    login = (await phone_login(api[0], user)).json()["data"]
    user.roles[0].is_active = False
    auth_headers = {"Authorization": "Bearer " + login["access_token"]}
    assert (await api[0].get("/api/v1/aurakey/admin/session", headers=auth_headers)).status_code == 403
    assert (await api[0].get("/other/admin", headers=auth_headers)).status_code == 200
    assert (await api[0].post("/api/v1/auth/refresh", json={"refresh_token": login["refresh_token"]})).status_code == 200


async def test_admin_context_is_isolated_between_concurrent_requests_and_reset_on_denial(api, user):
    user.is_superuser = True
    results = await asyncio.gather(
        api[0].get("/other/admin", headers=headers(user)),
        api[0].get("/other/user", headers=headers(user, "hope_just_right")),
        api[0].get("/other/user", headers=headers(user)),
        api[0].get("/api/v1/aurakey/admin/session", headers=headers(user, "hope_just_right")),
    )
    assert [response.status_code for response in results] == [200, 200, 403, 403]
    assert results[0].json()["scope"] == "hope_just_right"
    assert results[1].json()["scope"] is None
    assert auth_scope.current_app_key.get() is None
    assert auth_scope.current_admin_app.get() is None
    assert (await api[0].get("/other/user", headers=headers(user))).status_code == 403


async def test_misbound_management_context_fails_closed(api):
    dependency = bind_admin_app("hope_aurakey")()
    with pytest.raises(HTTPException) as error:
        await anext(dependency)
    assert error.value.status_code == 500
    assert auth_scope.current_admin_app.get() is None


async def test_admin_app_catalog_returns_registered_business_apps(api, user):
    response = await api[0].get("/api/v1/admin/apps", headers=headers(user))
    assert response.status_code == 200
    payload = response.json()
    assert payload["code"] == 200
    apps = payload["data"]
    assert all(set(item) == {"key", "name", "is_active"} for item in apps)
    assert all(item["key"] != "admin_web" for item in apps)
    ledger_mate = next(item for item in apps if item["key"] == "hope_ledger_mate")
    assert ledger_mate["name"] == "Hope 账伴"
    assert ledger_mate["is_active"] is True
    time_library = next(item for item in apps if item["key"] == "hope_time_library")
    assert time_library["is_active"] is False
