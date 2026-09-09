import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import Depends, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import core.roles.models
from core import auth_scope, security
from core.apps_config import AppConfig
from core.database import get_db
from core.dependencies import bind_app_key
from core.exceptions import BadRequestException, ForbiddenException, register_exception_handlers
from core.users import identity_service as service, scan_service
from core.users.dependencies import get_current_user, require_roles
from core.users.identity_router import router
from core.users.identity_schemas import H5IdentityRequest, IdentityRequest, StoredIdentity
from core.users.models import User, UserIdentity
from core.users.scan_router import router as scan_router
from core.users.services import UserService
from core.wechat.services import WeChatService


def make_user(**changes):
    fields = dict(id=uuid4(), phone="13800138000", nickname="Test", roles=[],
                  is_active=True, is_deleted=False, is_superuser=False, token_version=0)
    fields.update(changes)
    return User(**fields)


def result_for(value):
    result = Mock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.fixture
async def store(monkeypatch):
    redis = FakeRedis(decode_responses=True)
    for module in (service, scan_service, security):
        monkeypatch.setattr(module, "redis_client", redis)
    apps = {name: AppConfig(key=name, name=name, created_at="2026-09-09") for name in ("app_a", "app_b", "admin_web")}
    monkeypatch.setattr(auth_scope, "REGISTERED_APPS", apps)
    monkeypatch.setattr(scan_service, "REGISTERED_APPS", apps)
    monkeypatch.setattr(service.settings, "WECHAT_APPS", "public-id:test-secret,mini-a:test-secret,mini-b:test-secret")
    monkeypatch.setattr(service.settings, "PASSPORT_WECHAT_APP_IDS", ["public-id"])
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", ["https://passport.example.test"])
    monkeypatch.setattr(service.settings, "MINIAPP_APP_SCOPES", {"mini-a": "app_a", "mini-b": "app_b"})
    monkeypatch.setattr(security.settings, "SECRET_KEY", "isolated-test-only-key-not-production")
    yield redis
    await redis.aclose()


@pytest.fixture
def database():
    database = AsyncMock(spec=AsyncSession)
    database.execute.return_value = result_for(None)

    async def flush():
        for call in database.add.call_args_list:
            user = call.args[0]
            if isinstance(user, User) and user.id is None:
                user.id = uuid4()
                user.token_version = 0
                user.is_active = True
                user.is_deleted = False
                user.is_superuser = False
    database.flush.side_effect = flush
    return database


@pytest.fixture
def exchange(monkeypatch):
    exchange = AsyncMock(return_value={"openid": "verified-openid", "unionid": "untrusted-unionid"})
    monkeypatch.setattr(WeChatService, "exchange_h5_code_for_openid", exchange)
    monkeypatch.setattr(WeChatService, "exchange_miniapp_code_for_openid", exchange)
    return exchange


@pytest.fixture
def lookup(monkeypatch):
    lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(UserService, "_get_wechat_login_user", lookup)
    return lookup


def stored_identity(**changes):
    fields = dict(channel="h5", appid="public-id", openid="verified-openid", app_scope="passport",
                  expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
    fields.update(changes)
    return StoredIdentity(**fields)


async def pending(database):
    return await service.identify(database, "h5", H5IdentityRequest(appid="public-id", code="new-code"))


async def test_unbound_identity_has_only_ticket_no_user_or_tokens(store, database, exchange, lookup):
    response = await pending(database)
    assert response.status == "PHONE_REQUIRED"
    assert set(response.model_dump()) == {"status", "login_ticket", "expires_at"}
    database.add.assert_not_called()
    database.commit.assert_not_awaited()
    assert not await store.keys("auth:refresh:*")
    assert not await store.exists("auth:identity:ticket:" + response.login_ticket)


@pytest.mark.parametrize("appid,channel,expected", [("public-id", "h5", "passport"), ("mini-a", "miniapp", "app_a"), ("mini-b", "miniapp", "app_b")])
async def test_linked_identity_uses_same_user_and_server_selected_scope(store, database, exchange, lookup, appid, channel, expected):
    user = make_user()
    lookup.return_value = user
    response = await service.identify(database, channel, IdentityRequest(appid=appid, code="fresh-code"))
    assert response.status == "AUTHENTICATED"
    assert response.user.id == user.id
    assert response.app_scope == expected
    for token in (response.access_token, response.refresh_token):
        assert security.decode_token(token)["app_scope"] == expected
    database.add.assert_not_called()


async def test_same_public_identity_can_scan_a_then_b_without_phone_rebinding(store, database, exchange, lookup):
    lookup.return_value = make_user()
    for app_key in ("app_a", "app_b"):
        transaction = await scan_service.create_session(app_key)
        response = await service.identify(database, "h5", H5IdentityRequest(
            appid="public-id", code=app_key, transaction_id=transaction.transaction_id))
        assert response.status == "AUTHENTICATED"
        assert response.app_scope == "passport"
        assert (await scan_service.session_info(transaction.transaction_id)).status == "WAITING_SCAN"
    database.add.assert_not_called()


async def test_code_replay_is_rejected_before_second_exchange(store, database, exchange, lookup):
    await pending(database)
    with pytest.raises(HTTPException) as error:
        await pending(database)
    assert error.value.status_code == 409
    exchange.assert_awaited_once()


async def test_expired_scan_does_not_exchange_wechat_code(store, database, exchange, lookup):
    with pytest.raises(HTTPException) as error:
        await service.identify(database, "h5", H5IdentityRequest(appid="public-id", code="new", transaction_id=uuid4()))
    assert error.value.status_code == 410
    exchange.assert_not_awaited()


@pytest.mark.parametrize("phone_user", [None, "existing"])
async def test_phone_proof_creates_or_reuses_user_and_links_identity(store, database, phone_user):
    existing = make_user() if phone_user else None
    database.execute.side_effect = [result_for(None), result_for(None), result_for(None), result_for(existing)]
    user = await service.link_identity(database, stored_identity(), "13800138000")
    assert user.phone == "13800138000"
    added = [call.args[0] for call in database.add.call_args_list]
    assert len([item for item in added if isinstance(item, User)]) == (0 if existing else 1)
    identity = next(item for item in added if isinstance(item, UserIdentity))
    assert identity.user_id == user.id
    assert identity.subject == "verified-openid"
    assert identity.provider_app_id == "public-id"
    if existing:
        assert user.id == existing.id
    database.commit.assert_awaited_once()


@pytest.mark.parametrize("legacy", [False, True])
async def test_existing_formal_identity_is_never_moved_or_merged(store, database, legacy):
    owner = make_user(phone=None if legacy else "13900139000")
    phone_user = make_user()
    identity = SimpleNamespace(user_id=owner.id, is_deleted=False)
    database.execute.side_effect = [result_for(None), result_for(None), result_for(identity), result_for(phone_user), result_for(owner)]
    stored = stored_identity(legacy_user_id=owner.id if legacy else None, token_version=0 if legacy else None)
    with pytest.raises(HTTPException) as error:
        await service.link_identity(database, stored, phone_user.phone)
    assert error.value.status_code == 409
    assert identity.user_id == owner.id
    assert owner.phone == (None if legacy else "13900139000")
    database.commit.assert_not_awaited()
    database.rollback.assert_awaited_once()


async def test_legacy_phoneless_user_retains_id_when_binding_unused_phone(store, database):
    owner = make_user(phone=None)
    identity = SimpleNamespace(user_id=owner.id, is_deleted=False)
    database.execute.side_effect = [result_for(None), result_for(None), result_for(identity), result_for(None), result_for(owner)]
    result = await service.link_identity(database, stored_identity(legacy_user_id=owner.id, token_version=0), "13800138000")
    assert result.id == owner.id
    assert result.phone == "13800138000"
    database.add.assert_not_called()


@pytest.mark.parametrize("changes", [{"is_active": False}, {"is_deleted": True}])
async def test_unavailable_phone_user_cannot_receive_identity(store, database, changes):
    database.execute.side_effect = [result_for(None), result_for(None), result_for(None), result_for(make_user(**changes))]
    with pytest.raises(ForbiddenException):
        await service.link_identity(database, stored_identity(), "13800138000")
    database.add.assert_not_called()
    database.commit.assert_not_awaited()


async def test_unique_race_retries_after_rollback(store, database, monkeypatch):
    winner = make_user()
    link = AsyncMock(side_effect=[IntegrityError(None, None, Exception("unique")), winner])
    monkeypatch.setattr(service, "_link_once", link)
    assert await service.link_identity(database, stored_identity(), winner.phone) is winner
    database.rollback.assert_awaited_once()
    database.commit.assert_awaited_once()


async def test_bad_sms_preserves_ticket_and_never_links(store, database, exchange, lookup, monkeypatch):
    response = await pending(database)
    monkeypatch.setattr(service, "verify_sms_code", AsyncMock(return_value=False))
    with pytest.raises(HTTPException) as error:
        await service.complete_sms(database, response.login_ticket, "13800138000", "0000")
    assert error.value.status_code == 400
    await service.load_ticket(response.login_ticket)
    database.add.assert_not_called()


async def test_concurrent_completion_links_and_issues_tokens_once(store, database, exchange, lookup, monkeypatch):
    response = await pending(database)
    monkeypatch.setattr(service, "verify_sms_code", AsyncMock(return_value=True))
    link = AsyncMock(return_value=make_user())
    monkeypatch.setattr(service, "link_identity", link)
    results = await asyncio.gather(*(service.complete_sms(database, response.login_ticket, "13800138000", "0123") for attempt in range(8)), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    link.assert_awaited_once()
    assert len(await store.keys("auth:refresh:*")) == 1


async def test_token_failure_after_link_requires_fresh_code_and_keeps_link(store, database, exchange, lookup, monkeypatch):
    response = await pending(database)
    monkeypatch.setattr(service, "verify_sms_code", AsyncMock(return_value=True))
    linked = make_user()
    link = AsyncMock(return_value=linked)
    monkeypatch.setattr(service, "link_identity", link)
    factory = AsyncMock(side_effect=HTTPException(503, "unavailable"))
    monkeypatch.setattr(service, "_authenticated", factory)
    with pytest.raises(HTTPException):
        await service.complete_sms(database, response.login_ticket, linked.phone, "0123")
    link.assert_awaited_once()
    with pytest.raises(HTTPException) as error:
        await service.load_ticket(response.login_ticket)
    assert error.value.status_code == 410
    lookup.return_value = linked
    factory.side_effect = None
    await service.identify(database, "h5", H5IdentityRequest(appid="public-id", code="fresh-recovery"))
    assert factory.await_count == 2


async def test_miniapp_phone_exchange_uses_ticket_appid_not_client_identity(store, database, exchange, lookup, monkeypatch):
    response = await service.identify(database, "miniapp", IdentityRequest(appid="mini-a", code="new-code"))
    phone_exchange = AsyncMock(return_value="13800138000")
    monkeypatch.setattr(WeChatService, "exchange_phone_code", phone_exchange)
    monkeypatch.setattr(service, "link_identity", AsyncMock(return_value=make_user()))
    result = await service.complete_miniapp(database, response.login_ticket, "phone-code")
    assert result.app_scope == "app_a"
    phone_exchange.assert_awaited_once_with("mini-a", "phone-code")


@pytest.mark.parametrize("allowed_appids,expected_status", [([], None), (["public-id"], None), (["other-id"], 403)])
async def test_public_appid_allowlist(store, monkeypatch, allowed_appids, expected_status):
    monkeypatch.setattr(service.settings, "PASSPORT_WECHAT_APP_IDS", allowed_appids)
    if expected_status is None:
        assert service.resolve_identity_scope("h5", "public-id") == "passport"
    else:
        with pytest.raises(HTTPException) as error:
            service.resolve_identity_scope("h5", "public-id")
        assert error.value.status_code == expected_status


@pytest.mark.parametrize("channel,appid,expected_status", [
    ("h5", "unknown", 400), ("h5", "mini-a", 403), ("miniapp", "public-id", 403),
])
async def test_empty_public_allowlist_keeps_app_configuration_checks(store, monkeypatch, channel, appid, expected_status):
    monkeypatch.setattr(service.settings, "PASSPORT_WECHAT_APP_IDS", [])
    with pytest.raises(HTTPException) as error:
        service.resolve_identity_scope(channel, appid)
    assert error.value.status_code == expected_status


@pytest.mark.parametrize("callback", [
    "http://192.168.31.93:5173/wechat/callback?env=local",
    "https://other.example.test:8443/wechat/callback",
    "http://192.168.31.93:5173/passport/wechat/callback?env=local",
    "https://other.example.test:8443/passport/wechat/callback",
])
async def test_empty_allowlists_allow_configured_public_app_and_any_origin(store, monkeypatch, callback):
    monkeypatch.setattr(service.settings, "PASSPORT_WECHAT_APP_IDS", [])
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", [])
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "local")
    service.validate_oauth_target("public-id", callback)
    assert service.resolve_identity_scope("miniapp", "mini-a") == "app_a"


@pytest.mark.parametrize("callback", [
    "http://other.example.test/other",
    "http://user@other.example.test/wechat/callback",
    "http://:password@other.example.test/wechat/callback",
    "http://other.example.test/wechat/callback#fragment",
    "ftp://other.example.test/wechat/callback",
    "https:///wechat/callback",
    "/wechat/callback",
])
async def test_empty_callback_allowlist_keeps_url_validation(store, monkeypatch, callback):
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", [])
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "local")
    with pytest.raises(HTTPException) as error:
        service.validate_oauth_target("public-id", callback)
    assert error.value.status_code == 400


@pytest.mark.parametrize("uri", ["http://passport.example.test/wechat/callback", "https://evil.test/wechat/callback", "https://passport.example.test/other", "https://user@passport.example.test/wechat/callback", "https://passport.example.test/wechat/callback#fragment"])
async def test_oauth_redirect_allowlist(store, uri):
    with pytest.raises(HTTPException):
        service.validate_oauth_target("public-id", uri)
    service.validate_oauth_target("public-id", "https://passport.example.test/wechat/callback?env=local")


@pytest.mark.parametrize("origins", [[], ["http://192.168.1.10:5173", "https://passport.example.test"]])
@pytest.mark.parametrize("path", ["/wechat/callback", "/passport/wechat/callback"])
@pytest.mark.parametrize("environment,allowed", [
    ("development", True), ("dev", True), ("local", True), (" Development ", True),
    ("production", False), ("prod", False), (" Production ", False),
    ("staging", False), ("unknown", False), ("", False),
])
async def test_http_callback_only_in_explicit_development_environment(store, monkeypatch, environment, allowed, origins, path):
    monkeypatch.setattr(service.settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(service.settings, "DEBUG", True)
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", origins)
    callback = f"http://192.168.1.10:5173{path}?env=local"
    if allowed:
        service.validate_oauth_target("public-id", callback)
    else:
        with pytest.raises(HTTPException) as error:
            service.validate_oauth_target("public-id", callback)
        assert error.value.status_code == 400
    service.validate_oauth_target("public-id", f"https://passport.example.test{path}")


@pytest.mark.parametrize("path", ["/wechat/callback", "/passport/wechat/callback"])
async def test_passport_deployment_callbacks_allowlisted(store, monkeypatch, path):
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", ["https://tool.lxyy.fun"])
    service.validate_oauth_target("public-id", f"https://tool.lxyy.fun{path}")
    service.validate_oauth_target("public-id", f"https://tool.lxyy.fun{path}?env=local")


@pytest.mark.parametrize("callback", [
    "https://tool.lxyy.fun/other/wechat/callback",
    "https://tool.lxyy.fun/passport/wechat/callback/extra",
    "https://tool.lxyy.fun/passport/wechat/callback/",
    "https://tool.lxyy.fun/passport//wechat/callback",
    "https://tool.lxyy.fun/passport/../wechat/callback",
    "https://tool.lxyy.fun/passport/%77echat/callback",
    "https://tool.lxyy.fun/passport/wechat/callback;extra",
    "https://tool.lxyy.fun/passport/wechat/callback;",
    "https://tool.lxyy.fun/wechat/callback;extra",
    "https://tool.lxyy.fun/passport/wechat/callback#fragment",
    "https://user@tool.lxyy.fun/passport/wechat/callback",
    "https://:password@tool.lxyy.fun/passport/wechat/callback",
    "https://other.example.test/passport/wechat/callback",
    "https://tool.lxyy.fun:8443/passport/wechat/callback",
    "http://tool.lxyy.fun/passport/wechat/callback?env=local",
    "/passport/wechat/callback",
])
async def test_passport_deployment_rejects_other_paths_and_origins(store, monkeypatch, callback):
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", ["https://tool.lxyy.fun"])
    with pytest.raises(HTTPException) as error:
        service.validate_oauth_target("public-id", callback)
    assert error.value.status_code == 400


@pytest.mark.parametrize("callback", [
    "http://evil.test/wechat/callback?env=local",
    "http://192.168.1.10:5174/wechat/callback",
    "http://192.168.1.10:5173/other",
    "http://user@192.168.1.10:5173/wechat/callback",
    "http://192.168.1.10:5173/wechat/callback#fragment",
    "ftp://192.168.1.10:5173/wechat/callback",
])
async def test_local_http_keeps_origin_path_and_credential_checks(store, monkeypatch, callback):
    monkeypatch.setattr(service.settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(service.settings, "PASSPORT_CALLBACK_ORIGINS", ["http://192.168.1.10:5173"])
    with pytest.raises(HTTPException) as error:
        service.validate_oauth_target("public-id", callback)
    assert error.value.status_code == 400


@pytest.mark.parametrize("channel,appid", [("h5", "mini-a"), ("miniapp", "public-id"), ("h5", "unknown")])
async def test_wrong_channel_fails_before_code_exchange(store, database, exchange, channel, appid):
    with pytest.raises(HTTPException):
        await service.identify(database, channel, IdentityRequest(appid=appid, code="new"))
    exchange.assert_not_awaited()


async def test_redis_failure_does_not_expose_internal_exception(store, database, exchange, monkeypatch):
    monkeypatch.setattr(store, "set", AsyncMock(side_effect=RedisConnectionError("private-marker")))
    with pytest.raises(HTTPException) as error:
        await pending(database)
    assert error.value.status_code == 503
    assert "private-marker" not in str(error.value)
    exchange.assert_not_awaited()


@pytest.fixture
async def api(store, database, monkeypatch):
    import core.dependencies
    monkeypatch.setattr(core.dependencies, "REGISTERED_APPS", auth_scope.REGISTERED_APPS)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.include_router(scan_router, prefix="/api/v1")
    for app_key in ("app_a", "app_b"):
        async def profile(user=Depends(get_current_user)):
            return {"id": str(user.id)}
        app.add_api_route("/" + app_key, profile, dependencies=[Depends(bind_app_key(app_key))])
    app.dependency_overrides[get_db] = lambda: database
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.mark.parametrize("payload", [{"openid": "client-openid", "appid": "mini-a"}, {"appid": "mini-a", "code": "new", "app_key": "app_b"}])
async def test_http_rejects_bare_openid_or_client_scope(api, exchange, payload):
    response = await api.post("/api/v1/auth/identity/miniapp", json=payload)
    assert response.status_code == 422
    exchange.assert_not_awaited()


async def test_http_two_phase_contract_and_consent(api, exchange, lookup, monkeypatch):
    response = await api.post("/api/v1/auth/identity/h5", json={"appid": "public-id", "code": "new"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    pending_data = response.json()["data"]
    assert pending_data["status"] == "PHONE_REQUIRED"
    payload = {"login_ticket": pending_data["login_ticket"], "phone": "13800138000", "code": "0123"}
    assert (await api.post("/api/v1/auth/identity/complete/sms", json=payload)).status_code == 422
    payload["accepted_terms"] = True
    monkeypatch.setattr(service, "verify_sms_code", AsyncMock(return_value=True))
    monkeypatch.setattr(service, "link_identity", AsyncMock(return_value=make_user()))
    response = await api.post("/api/v1/auth/identity/complete/sms", json=payload)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "AUTHENTICATED"
    assert response.json()["data"]["app_scope"] == "passport"
    assert (await api.post("/api/v1/auth/identity/complete/sms", json=payload)).status_code == 410


@pytest.mark.parametrize("scope,target,status", [("app_a", "app_a", 200), ("app_a", "app_b", 403), ("app_b", "app_a", 403), ("passport", "app_a", 403)])
async def test_real_auth_dependency_rejects_cross_app_tokens(api, monkeypatch, scope, target, status):
    user = make_user()
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    access, refresh = await security.create_token_pair(user.id, 0, scope)
    pair = await security.rotate_refresh_token(refresh, user.id, 0)
    assert security.decode_token(pair[0])["app_scope"] == scope
    response = await api.get("/" + target, headers={"Authorization": "Bearer " + access, "app": target})
    assert response.status_code == status
    assert auth_scope.current_app_key.get() is None


async def test_business_token_cannot_confirm_pc_scan(api, monkeypatch):
    user = make_user()
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    session = await scan_service.create_session("app_a")
    await scan_service.transition(session.transaction_id, "scanned")
    access, _ = await security.create_token_pair(user.id, 0, "app_a")
    response = await api.post(f"/api/v1/auth/scan/sessions/{session.transaction_id}/confirm", headers={"Authorization": "Bearer " + access})
    assert response.status_code == 403
    assert (await scan_service.session_info(session.transaction_id)).status == "PENDING"


@pytest.mark.parametrize("role_scope,active,deleted,allowed", [("app_a", True, False, True), ("app_b", True, False, False), ("app_a", False, False, False), ("app_a", True, True, False)])
async def test_same_role_code_cannot_leak_across_apps(role_scope, active, deleted, allowed):
    user = SimpleNamespace(is_superuser=False, roles=[SimpleNamespace(scope=role_scope, code="admin", is_active=active, is_deleted=deleted)])
    request = Request({"type": "http"})
    request.state.auth_scope = "app_a"
    checker = require_roles("admin")
    if allowed:
        assert await checker(request, user) is user
    else:
        with pytest.raises(HTTPException):
            await checker(request, user)


async def test_multiple_wechat_identities_never_select_arbitrary_payer(database):
    database.execute.return_value.scalars.return_value.all.return_value = ["first", "second"]
    with pytest.raises(BadRequestException):
        await UserService.get_wechat_openid(database, uuid4(), "public-id")


@pytest.mark.parametrize("identity", [{}, {"openid": 123}, {"openid": ""}, {"openid": "valid", "unionid": 1}])
async def test_malformed_upstream_identity_is_sanitized(store, database, exchange, identity):
    exchange.return_value = identity
    with pytest.raises(HTTPException) as error:
        await pending(database)
    assert error.value.status_code == 502
    database.add.assert_not_called()


async def test_ticket_expiry_and_mapping_change_require_new_login(store, database, exchange, lookup, monkeypatch):
    response = await pending(database)
    raw, stored = await service.load_ticket(response.login_ticket)
    stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await store.set(service._ticket_key(response.login_ticket), stored.model_dump_json())
    with pytest.raises(HTTPException) as error:
        await service.load_ticket(response.login_ticket)
    assert error.value.status_code == 410
    response = await service.identify(database, "miniapp", IdentityRequest(appid="mini-a", code="mini-code"))
    monkeypatch.setattr(service.settings, "MINIAPP_APP_SCOPES", {"mini-a": "app_b"})
    with pytest.raises(HTTPException) as error:
        await service.load_ticket(response.login_ticket)
    assert error.value.status_code == 409


async def test_unscoped_legacy_access_and_refresh_tokens_require_relogin(api, store, monkeypatch):
    from jose import jwt
    user = make_user()
    lookup = AsyncMock(return_value=user)
    monkeypatch.setattr(UserService, "get_by_id", lookup)
    access, refresh = await security.create_token_pair(user.id, 0, "app_a")
    payload = security.decode_token(access)
    del payload["app_scope"]
    legacy_access = jwt.encode(payload, security.settings.SECRET_KEY, algorithm=security.settings.ALGORITHM)
    response = await api.get("/app_a", headers={"Authorization": "Bearer " + legacy_access})
    assert response.status_code == 401
    lookup.assert_not_awaited()
    payload = security.decode_token(refresh)
    del payload["app_scope"]
    legacy_refresh = jwt.encode(payload, security.settings.SECRET_KEY, algorithm=security.settings.ALGORITHM)
    assert await security.rotate_refresh_token(legacy_refresh, user.id, 0) is None


@pytest.mark.parametrize("country,status", [("86", 200), ("1", 400)])
async def test_phone_code_is_verified_by_wechat_not_client_phone(store, monkeypatch, country, status):
    import httpx
    calls = []
    async def respond(request):
        calls.append(request)
        if request.url.path.endswith('/token'):
            return httpx.Response(200, json={"access_token": "fixture-wechat-token"})
        return httpx.Response(200, json={"errcode": 0, "phone_info": {"countryCode": country, "purePhoneNumber": "13800138000"}})
    factory = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: factory(transport=httpx.MockTransport(respond), **kwargs))
    if status == 200:
        assert await WeChatService.exchange_phone_code("mini-a", "fixture-phone-code") == "13800138000"
    else:
        with pytest.raises(HTTPException) as error:
            await WeChatService.exchange_phone_code("mini-a", "fixture-phone-code")
        assert error.value.status_code == status
    assert calls[0].url.params["appid"] == "mini-a"
    assert calls[1].url.path == "/wxa/business/getuserphonenumber"
    assert calls[1].method == "POST"


async def test_legacy_wechat_scan_event_cannot_create_user(monkeypatch):
    creator = AsyncMock(side_effect=AssertionError("must not create a user"))
    monkeypatch.setattr(UserService, "create_by_wechat", creator)
    await WeChatService.process_scan_event("public-id", "legacy-scene", "legacy-subject")
    creator.assert_not_awaited()


async def test_legacy_scan_endpoints_cannot_bypass_two_phase_login(database):
    from core.wechat.router import router as wechat_router
    app = FastAPI()
    app.include_router(wechat_router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: database
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in ("/auth/wechat/qrcode?appid=public-id", "/auth/wechat/status?scene_id=legacy"):
            assert (await client.get("/api/v1" + path)).status_code == 410
        response = await client.post("/api/v1/auth/wechat/exchange", json={"scene_id": "legacy"})
        assert response.status_code == 410
    database.execute.assert_not_awaited()
