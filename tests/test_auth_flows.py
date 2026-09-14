from datetime import datetime, timezone
from importlib import import_module
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException, Request
from fakeredis.aioredis import FakeRedis
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import core.roles.models
from core import sms
from core.database import get_db
from core.exceptions import BadRequestException, ForbiddenException, register_exception_handlers
from core.users import miniapp_router, services
from core.users.dependencies import get_current_user
from core.users.miniapp_schemas import MiniappLoginRequest, MiniappPhoneRequest
from core.users.models import User, UserIdentity
from core.users.schemas import BindPhoneRequest, PhoneLoginRequest, SendSmsRequest, WechatLogin
from core.users.services import UserService

router = import_module("core.users.router")

def make_user(**overrides):
    fields = dict(
        id=uuid4(), nickname="用户", source="wechat", roles=[], phone=None,
        is_active=True, is_deleted=False, is_superuser=False, token_version=0,
        created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc),
    )
    fields.update(overrides)
    return User(**fields)


def result_for(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
def database():
    database = AsyncMock(spec=AsyncSession)
    database.execute.return_value = result_for(None)

    async def hydrate(user):
        defaults = make_user()
        for name in ("id", "is_active", "is_deleted", "is_superuser", "token_version", "created_at", "updated_at"):
            if getattr(user, name) is None:
                setattr(user, name, getattr(defaults, name))

    async def flush():
        for call in database.add.call_args_list:
            if isinstance(call.args[0], User):
                await hydrate(call.args[0])

    database.refresh.side_effect = hydrate
    database.flush.side_effect = flush
    return database


@pytest.fixture
def sms_check(monkeypatch):
    check = AsyncMock(return_value=True)
    monkeypatch.setattr(services, "verify_sms_code", check)
    return check


@pytest.fixture
def phone_lookup(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(UserService, "get_by_phone", lookup)
    return lookup


@pytest.fixture
def tokens(monkeypatch):
    factory = AsyncMock(return_value=("test-access", "test-refresh"))
    monkeypatch.setattr(services, "create_token_pair", factory)
    return factory


async def test_new_phone_login_creates_passwordless_account(database, sms_check, phone_lookup):
    user = await UserService.login_with_phone(database, "+8613800138000", "0123")
    sms_check.assert_awaited_once_with("13800138000", "0123")
    phone_lookup.assert_awaited_once_with(database, "13800138000")
    assert user.phone == "13800138000"
    assert user.username is None
    assert user.hashed_password is None
    assert user.source == "phone"
    assert user.phone not in user.nickname
    database.add.assert_called_once_with(user)


async def test_existing_phone_login_does_not_create_account(database, sms_check, phone_lookup):
    existing = make_user(phone="13800138000")
    phone_lookup.return_value = existing
    assert await UserService.login_with_phone(database, existing.phone, "0123") is existing
    database.add.assert_not_called()


async def test_invalid_sms_never_looks_up_or_creates_user(database, sms_check, phone_lookup):
    sms_check.return_value = False
    with pytest.raises(BadRequestException):
        await UserService.login_with_phone(database, "13800138000", "0123")
    phone_lookup.assert_not_awaited()
    database.add.assert_not_called()


@pytest.mark.parametrize("state", [{"is_active": False}, {"is_deleted": True}])
async def test_unavailable_phone_account_is_not_recreated(database, sms_check, phone_lookup, state):
    phone_lookup.return_value = make_user(phone="13800138000", **state)
    with pytest.raises(ForbiddenException):
        await UserService.login_with_phone(database, "13800138000", "0123")
    database.add.assert_not_called()


@pytest.mark.parametrize("active", [True, False])
async def test_phone_unique_conflict_reuses_winner_and_checks_status(database, sms_check, phone_lookup, active):
    winner = make_user(phone="13800138000", is_active=active)
    phone_lookup.side_effect = [None, winner]
    database.commit.side_effect = IntegrityError(None, None, Exception("unique conflict"))
    if active:
        assert await UserService.login_with_phone(database, winner.phone, "0123") is winner
    else:
        with pytest.raises(ForbiddenException):
            await UserService.login_with_phone(database, winner.phone, "0123")
    database.rollback.assert_awaited_once()


async def test_legacy_wechat_never_creates_unverified_user(database):
    with pytest.raises(BadRequestException):
        await UserService.wechat_login(database, "test-openid", "test-appid")
    database.add.assert_not_called()


async def test_wechat_existing_identity_reuses_account(database, monkeypatch):
    user = make_user(phone="13800138000")
    database.execute.return_value = result_for(SimpleNamespace(user_id=user.id, is_deleted=False))
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    assert await UserService.wechat_login(database, "test-openid", "test-appid") is user
    database.add.assert_not_called()


async def test_deleted_wechat_identity_cannot_create_new_account(database):
    database.execute.return_value = result_for(SimpleNamespace(is_deleted=True))
    with pytest.raises(ForbiddenException):
        await UserService.wechat_login(database, "test-openid", "test-appid")
    database.add.assert_not_called()


@pytest.mark.parametrize("state", [{"is_active": False}, {"is_deleted": True}])
async def test_wechat_disabled_user_cannot_login(database, monkeypatch, state):
    user = make_user(**state)
    database.execute.return_value = result_for(SimpleNamespace(user_id=user.id, is_deleted=False))
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    with pytest.raises(ForbiddenException):
        await UserService.wechat_login(database, "test-openid", "test-appid")
    database.add.assert_not_called()


async def test_legacy_wechat_rejects_existing_phoneless_user(database, monkeypatch):
    user = make_user()
    database.execute.return_value = result_for(SimpleNamespace(user_id=user.id, is_deleted=False))
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    with pytest.raises(BadRequestException):
        await UserService.wechat_login(database, "test-openid", "test-appid")
    database.add.assert_not_called()
    database.commit.assert_not_awaited()


async def test_deleted_user_is_filtered_from_authenticated_lookup(database):
    await UserService.get_by_id(database, uuid4())
    assert "core_users.is_deleted = false" in str(database.execute.call_args.args[0])


async def test_missing_wechat_identity_is_rejected_before_database(database):
    with pytest.raises(BadRequestException):
        await UserService.wechat_login(database, "", "test-appid")
    database.execute.assert_not_awaited()


async def test_sms_binding_uses_shared_verified_path(database, monkeypatch, sms_check):
    user = make_user()
    binder = AsyncMock(return_value=user)
    monkeypatch.setattr(UserService, "bind_verified_phone", binder)
    await UserService.bind_phone(database, user, "+8613800138000", "0123")
    sms_check.assert_awaited_once_with("13800138000", "0123")
    binder.assert_awaited_once_with(database, user, "13800138000")


async def test_invalid_sms_does_not_bind_phone(database, monkeypatch, sms_check):
    sms_check.return_value = False
    binder = AsyncMock()
    monkeypatch.setattr(UserService, "bind_verified_phone", binder)
    with pytest.raises(BadRequestException):
        await UserService.bind_phone(database, make_user(), "13800138000", "0123")
    binder.assert_not_awaited()


async def test_repeat_binding_of_own_phone_needs_no_new_code(database, sms_check):
    user = make_user(phone="13800138000")
    database.execute.return_value = result_for(user)
    assert await UserService.bind_phone(database, user, user.phone, "0123") is user
    sms_check.assert_not_awaited()


async def test_verified_phone_binding_locks_account(database, phone_lookup):
    user = make_user()
    database.execute.return_value = result_for(user)
    assert await UserService.bind_verified_phone(database, user, "13800138000") is user
    assert "FOR UPDATE" in str(database.execute.call_args.args[0])
    assert user.phone == "13800138000"
    database.commit.assert_awaited_once()


async def test_binding_conflict_never_merges_accounts(database, phone_lookup):
    user = make_user()
    existing = make_user(phone="13800138000")
    database.execute.return_value = result_for(user)
    phone_lookup.return_value = existing
    with pytest.raises(BadRequestException) as error:
        await UserService.bind_verified_phone(database, user, existing.phone)
    assert "其他账号" in error.value.message
    assert user.phone is None
    assert existing.phone == "13800138000"
    database.rollback.assert_awaited_once()
    database.commit.assert_not_awaited()


async def test_concurrent_binding_conflict_returns_business_error(database, phone_lookup):
    user = make_user()
    database.execute.return_value = result_for(user)
    database.commit.side_effect = IntegrityError(None, None, Exception("phone conflict"))
    with pytest.raises(BadRequestException):
        await UserService.bind_verified_phone(database, user, "13800138000")
    database.rollback.assert_awaited_once()


async def test_binding_same_phone_is_idempotent(database, phone_lookup):
    user = make_user(phone="13800138000")
    database.execute.return_value = result_for(user)
    assert await UserService.bind_verified_phone(database, user, user.phone) is user
    phone_lookup.assert_not_awaited()


async def test_binding_cannot_replace_phone_after_concurrent_update(database, phone_lookup):
    stale_user = make_user()
    locked_user = make_user(id=stale_user.id, phone="13900139000")
    database.execute.return_value = result_for(locked_user)
    with pytest.raises(BadRequestException):
        await UserService.bind_verified_phone(database, stale_user, "13800138000")
    assert locked_user.phone == "13900139000"
    database.rollback.assert_awaited_once()


async def test_disabled_user_cannot_bind_verified_phone(database, phone_lookup):
    user = make_user(is_deleted=True)
    database.execute.return_value = result_for(user)
    with pytest.raises(ForbiddenException):
        await UserService.bind_verified_phone(database, user, "13800138000")
    phone_lookup.assert_not_awaited()


@pytest.mark.parametrize("superuser, active, deleted, allowed", [
    (True, True, False, True), (False, True, False, False),
    (True, False, False, False), (True, True, True, False),
])
async def test_password_login_is_reserved_for_active_superusers(database, monkeypatch, superuser, active, deleted, allowed):
    user = make_user(is_superuser=superuser, is_active=active, is_deleted=deleted, hashed_password="test-hash")
    monkeypatch.setattr(UserService, "get_by_username", AsyncMock(return_value=user))
    monkeypatch.setattr(services, "verify_password", Mock(return_value=True))
    result = await UserService.authenticate(database, "admin", "test-password")
    assert (result is user) is allowed


@pytest.mark.parametrize("phone", ["13800138000", "+8613800138000", "008613800138000"])
def test_phone_inputs_use_one_canonical_format(phone):
    for schema in (SendSmsRequest, PhoneLoginRequest, BindPhoneRequest):
        assert schema(phone=phone, code="0123").phone == "13800138000"


@pytest.mark.parametrize("code", ["123", "12345", "１２３４", "abcd", 1234])
def test_login_requires_four_ascii_digits(code):
    with pytest.raises(ValidationError):
        PhoneLoginRequest(phone="13800138000", code=code)


@pytest.fixture
async def api(database, tokens):
    app = FastAPI()
    app.include_router(router.router, prefix="/api/v1")
    app.include_router(miniapp_router.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: database
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield app, client


@pytest.mark.parametrize("existing", [True, False])
async def test_phone_login_http_contract(api, sms_check, phone_lookup, existing):
    if existing:
        phone_lookup.return_value = make_user(phone="13800138000")
    _, client = api
    response = await client.post("/api/v1/auth/phone/login", json={"phone": "13800138000", "code": "0123"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["access_token"] == "test-access"
    assert data["refresh_token"] == "test-refresh"
    assert data["user"]["phone"] == "13800138000"
    assert data["user"]["needs_phone_binding"] is False
    assert "hashed_password" not in data["user"]


@pytest.mark.parametrize("app_key", [None, "admin_web", "hope_aurakey", "passport", "unknown-business"])
async def test_password_login_is_gone_for_every_scope(api, monkeypatch, tokens, database, app_key):
    authenticate = AsyncMock(return_value=make_user(is_superuser=True, phone="13800138000"))
    monkeypatch.setattr(UserService, "authenticate", authenticate)
    payload = {"username": "admin", "password": "test-password"}
    if app_key is not None:
        payload["app_key"] = app_key
    response = await api[1].post("/api/v1/auth/login", json=payload)
    assert response.status_code == 410
    assert response.json()["code"] == 410
    assert response.json()["data"] is None
    assert "密码登录已停用" in response.json()["message"]
    authenticate.assert_not_awaited()
    database.execute.assert_not_awaited()
    tokens.assert_not_awaited()


async def test_retired_password_route_has_no_credential_schema(api, tokens):
    response = await api[1].post("/api/v1/auth/login")
    assert response.status_code == 410
    schema = api[0].openapi()
    route = schema["paths"]["/api/v1/auth/login"]["post"]
    assert route["deprecated"] is True
    assert "requestBody" not in route
    assert "200" not in route["responses"]
    assert "UsernameLogin" not in schema["components"]["schemas"]
    tokens.assert_not_awaited()


async def test_invalid_sms_does_not_issue_tokens(api, sms_check, tokens):
    sms_check.return_value = False
    _, client = api
    response = await client.post("/api/v1/auth/phone/login", json={"phone": "13800138000", "code": "0123"})
    assert response.status_code == 400
    tokens.assert_not_awaited()


async def test_sms_send_then_login_consumes_code(api, phone_lookup, monkeypatch):
    _, client = api
    redis = FakeRedis(decode_responses=True)
    provider = SimpleNamespace(send_sms_verify_code_with_options_async=AsyncMock(
        return_value=SimpleNamespace(body=SimpleNamespace(code="OK", success=True)),
    ))
    monkeypatch.setattr(sms, "redis_client", redis)
    monkeypatch.setattr(sms, "_create_client", lambda: provider)
    monkeypatch.setattr(sms.settings, "ALIYUN_SMS_SIGN_NAME", "test-sign")
    monkeypatch.setattr(sms.settings, "ALIYUN_SMS_TEMPLATE_CODE", "100001")
    try:
        response = await client.post("/api/v1/auth/sms/send", json={"phone": "+8613800138000"})
        assert response.status_code == 200
        request = provider.send_sms_verify_code_with_options_async.call_args.args[0]
        code = json.loads(request.template_param)["code"]
        payload = {"phone": "13800138000", "code": code}
        response = await client.post("/api/v1/auth/phone/login", json=payload)
        assert response.status_code == 200
        assert response.json()["data"]["user"]["needs_phone_binding"] is False
        assert (await client.post("/api/v1/auth/phone/login", json=payload)).status_code == 400
    finally:
        await redis.aclose()


async def test_register_routes_are_removed(api):
    app, client = api
    for endpoint in ("/api/v1/auth/register", "/api/v1/auth/phone/register"):
        assert endpoint not in app.openapi()["paths"]
        assert (await client.post(endpoint, json={})).status_code == 404
    properties = app.openapi()["components"]["schemas"]["PhoneLoginRequest"]["properties"]
    assert set(properties) == {"phone", "code", "app_key"}


async def test_profile_reflects_phone_binding_state(api, database):
    app, client = api
    user = make_user()
    async def authenticated(request: Request):
        request.state.auth_scope = "passport"
        return user
    app.dependency_overrides[get_current_user] = authenticated
    response = await client.get("/api/v1/auth/me")
    assert response.json()["data"]["needs_phone_binding"] is True
    database.execute.return_value = result_for(user)
    await UserService.bind_verified_phone(database, user, "13800138000")
    response = await client.get("/api/v1/auth/me")
    assert response.json()["data"]["needs_phone_binding"] is False


@pytest.fixture
def wechat_http(monkeypatch):
    client = AsyncMock()
    client.get.return_value = Mock(json=Mock(return_value={"openid": "test-openid", "access_token": "test-wechat"}))
    context = AsyncMock()
    context.__aenter__.return_value = client
    monkeypatch.setattr(miniapp_router.httpx, "AsyncClient", Mock(return_value=context))
    monkeypatch.setattr(miniapp_router.settings, "WECHAT_APPS", "test-appid:test-secret")
    return client


@pytest.mark.parametrize("channel", ["miniapp", "h5"])
async def test_legacy_wechat_never_issues_phoneless_token(database, tokens, monkeypatch, wechat_http, channel):
    from core.apps_config import REGISTERED_APPS
    scope = next(key for key in REGISTERED_APPS if key != "admin_web")
    monkeypatch.setattr(router.settings, "PASSPORT_WECHAT_APP_IDS", ["test-appid"] if channel == "h5" else [])
    monkeypatch.setattr(router.settings, "MINIAPP_APP_SCOPES", {"test-appid": scope} if channel == "miniapp" else {})
    monkeypatch.setattr(UserService, "wechat_login", AsyncMock(return_value=make_user()))
    with pytest.raises(BadRequestException):
        if channel == "miniapp":
            await miniapp_router.miniapp_login(MiniappLoginRequest(appid="test-appid", code="test-code"), database)
        else:
            await router.wechat_login(WechatLogin(appid="test-appid", code="test-code"), database)
    tokens.assert_not_awaited()


async def test_miniapp_phone_uses_shared_binding_rules(database, monkeypatch, wechat_http):
    from core.apps_config import REGISTERED_APPS
    scope = next(key for key in REGISTERED_APPS if key != "admin_web")
    monkeypatch.setattr(router.settings, "MINIAPP_APP_SCOPES", {"test-appid": scope})
    monkeypatch.setattr(router.settings, "PASSPORT_WECHAT_APP_IDS", [])
    request = Request({"type": "http"})
    request.state.auth_scope = scope
    user = make_user()
    wechat_http.post.return_value = Mock(json=Mock(return_value={"errcode": 0, "phone_info": {"phoneNumber": "13800138000"}}))
    binder = AsyncMock(return_value=user)
    monkeypatch.setattr(UserService, "bind_verified_phone", binder)
    await miniapp_router.miniapp_get_phone(MiniappPhoneRequest(appid="test-appid", code="test-code"), request, user, database)
    binder.assert_awaited_once_with(database, user, "13800138000")
