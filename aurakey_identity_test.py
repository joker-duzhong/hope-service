"""AuraKey 统一身份与业务资料契约，不连接外部服务。"""

from datetime import datetime, timezone
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import Depends, FastAPI
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from apps.aurakey import admin_router, router
from apps.aurakey.services import AurakeyService
from core import security
from core.database import get_db
from core.dependencies import bind_app_key
from core.exceptions import register_exception_handlers
from core.security import create_access_token
from core.users.services import UserService
from core.users import scan_service, services

auth_router = import_module("core.users.router")


@pytest.fixture
def identity_user():
    return SimpleNamespace(
        id=uuid4(), phone="13800138000", nickname="Aura 用户", username=None,
        email=None, avatar=None, openid="legacy-other-app-openid", roles=[],
        is_superuser=False, is_active=True, is_deleted=False, token_version=0,
        source="wechat", created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def api(monkeypatch, identity_user):
    db = AsyncMock(spec=AsyncSession)
    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/v1")
    app.include_router(admin_router.router, prefix="/api/v1/aurakey", dependencies=[Depends(bind_app_key("hope_aurakey"))])
    app.include_router(router.router, prefix="/api/v1/aurakey", dependencies=[Depends(bind_app_key("hope_aurakey"))])
    app.dependency_overrides[get_db] = lambda: db
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=identity_user))
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, db


@pytest.mark.parametrize(
    "scope, superuser, role_scope, active_role, expected",
    [
        ("hope_aurakey", False, "hope_aurakey", True, 200),
        ("hope_aurakey", True, None, False, 200),
        ("hope_aurakey", False, None, False, 403),
        ("hope_aurakey", False, "admin_web", True, 403),
        ("hope_aurakey", False, "hope_aurakey", False, 403),
        ("admin_web", True, None, False, 200),
        ("admin_web", False, "hope_aurakey", True, 200),
        ("passport", True, None, False, 403),
    ],
)
async def test_admin_session_requires_supported_scope_and_live_permission(api, identity_user, scope, superuser, role_scope, active_role, expected):
    identity_user.is_superuser = superuser
    if role_scope:
        identity_user.roles = [SimpleNamespace(
            id=uuid4(), code="aurakey_admin", name="AuraKey 管理员", scope=role_scope,
            is_active=active_role, is_deleted=False,
        )]
    token = create_access_token(identity_user.id, token_version=0, app_scope=scope)
    response = await api[0].get("/api/v1/aurakey/admin/session", headers={"Authorization": "Bearer " + token})
    assert response.status_code == expected
    if expected == 200:
        assert response.headers["cache-control"] == "no-store"
        data = response.json()["data"]
        assert data["id"] == str(identity_user.id)
        assert data["openid"] is None
        assert data["needs_phone_binding"] is False


@pytest.mark.parametrize("condition, expected", [("no-token", 401), ("no-phone", 401), ("disabled", 403), ("revoked", 401)])
async def test_admin_session_rejects_invalid_session(api, identity_user, condition, expected):
    identity_user.is_superuser = True
    token = create_access_token(identity_user.id, token_version=0, app_scope="hope_aurakey")
    if condition == "no-phone":
        identity_user.phone = None
    elif condition == "disabled":
        identity_user.is_active = False
    elif condition == "revoked":
        identity_user.token_version = 1
    headers = {} if condition == "no-token" else {"Authorization": "Bearer " + token}
    response = await api[0].get("/api/v1/aurakey/admin/session", headers=headers)
    assert response.status_code == expected


@pytest.mark.parametrize("has_admin_role, expected", [(True, 200), (False, 403)])
async def test_scan_exchange_then_admin_permission_check(api, identity_user, monkeypatch, has_admin_role, expected):
    if has_admin_role:
        identity_user.roles = [SimpleNamespace(
            id=uuid4(), code="aurakey_admin", name="AuraKey 管理员", scope="hope_aurakey",
            is_active=True, is_deleted=False,
        )]
    redis = FakeRedis(decode_responses=True)
    monkeypatch.setattr(scan_service, "redis_client", redis)
    monkeypatch.setattr(security, "redis_client", redis)
    try:
        created = await scan_service.create_session("hope_aurakey")
        await scan_service.transition(created.transaction_id, "scanned")
        await scan_service.transition(created.transaction_id, "confirm", identity_user)
        polled = await scan_service.poll_session(created.transaction_id, created.poll_token)
        login = await scan_service.exchange_session(created.transaction_id, polled.exchange_code, created.poll_token, api[1])
        assert login.app_scope == "hope_aurakey"
        response = await api[0].get("/api/v1/aurakey/admin/session", headers={"Authorization": "Bearer " + login.access_token})
        assert response.status_code == expected
        assert (await scan_service.poll_session(created.transaction_id, created.poll_token)).status.value == "CONSUMED"
    finally:
        await redis.aclose()


@pytest.mark.parametrize("kind, expected", [("admin", 200), ("superuser", 200), ("user", 403)])
async def test_sms_login_then_admin_permission_check(api, identity_user, monkeypatch, kind, expected):
    identity_user.is_superuser = kind == "superuser"
    if kind == "admin":
        identity_user.roles = [SimpleNamespace(
            id=uuid4(), code="aurakey_admin", name="AuraKey 管理员", scope="hope_aurakey",
            is_active=True, is_deleted=False,
        )]
    verify_sms = AsyncMock(return_value=True)
    monkeypatch.setattr(services, "verify_sms_code", verify_sms)
    monkeypatch.setattr(UserService, "get_by_phone", AsyncMock(return_value=identity_user))
    redis = FakeRedis(decode_responses=True)
    monkeypatch.setattr(security, "redis_client", redis)
    try:
        login_response = await api[0].post("/api/v1/auth/phone/login", json={
            "phone": identity_user.phone, "code": "0123", "app_key": "hope_aurakey",
        })
        assert login_response.status_code == 200
        login = login_response.json()["data"]
        assert login["app_scope"] == "hope_aurakey"
        verify_sms.assert_awaited_once_with(identity_user.phone, "0123")
        response = await api[0].get("/api/v1/aurakey/admin/session", headers={
            "Authorization": "Bearer " + login["access_token"],
        })
        assert response.status_code == expected
        api[1].add.assert_not_called()
    finally:
        await redis.aclose()


async def test_business_profile_resolves_avatar_without_legacy_openid(api, identity_user, monkeypatch):
    resource_id = uuid4()
    identity_user.avatar = str(resource_id)
    monkeypatch.setattr(AurakeyService, "get_user_entitlement", AsyncMock(return_value={
        "remaining_points": 25, "is_vip": False, "vip_type": "普通会员", "vip_expire_time": None, "vip_level": 0,
    }))
    resource = SimpleNamespace(url="https://cdn.example.test/avatar.png")
    monkeypatch.setattr(router.StorageService, "get_resources_by_ids", AsyncMock(return_value={resource_id: resource}))
    token = create_access_token(identity_user.id, token_version=0, app_scope="hope_aurakey")
    response = await api[0].get("/api/v1/aurakey/user/profile", headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["user_id"] == str(identity_user.id)
    assert data["avatar"] == resource.url
    assert data["openid"] is None
    assert data["balance"] == 25


async def test_avatar_map_handles_missing_and_shared_resources(monkeypatch):
    resource_id, missing_id = uuid4(), uuid4()
    users = [
        SimpleNamespace(id=uuid4(), avatar=str(resource_id)),
        SimpleNamespace(id=uuid4(), avatar=str(resource_id)),
        SimpleNamespace(id=uuid4(), avatar=str(missing_id)),
        SimpleNamespace(id=uuid4(), avatar="https://cdn.example.test/external.png"),
    ]
    resources = AsyncMock(return_value={resource_id: SimpleNamespace(url="https://cdn.example.test/avatar.png")})
    monkeypatch.setattr(router.StorageService, "get_resources_by_ids", resources)
    result = await AurakeyService._get_user_avatar_map(None, users)
    assert result[users[0].id] == result[users[1].id] == "https://cdn.example.test/avatar.png"
    assert result[users[2].id] is None
    assert result[users[3].id] == users[3].avatar
    resources.assert_awaited_once()
    assert set(resources.call_args.args[1]) == {resource_id, missing_id}


async def test_admin_gallery_search_uses_current_business_identity(monkeypatch):
    monkeypatch.setattr(router.settings, "MINIAPP_APP_SCOPES", {"aura-appid": "hope_aurakey", "other-appid": "hope_just_right"})
    rows = Mock()
    rows.all.return_value = []
    db = SimpleNamespace(scalar=AsyncMock(return_value=0), execute=AsyncMock(return_value=rows))
    await AurakeyService.get_admin_gallery_list(db, page=1, page_size=20, keyword="openid-fragment")
    query = db.execute.call_args.args[0].compile()
    assert "core_user_identities" in str(query)
    assert "core_users.openid" not in str(query).split("WHERE", 1)[1]
    assert ["aura-appid"] in query.params.values()
