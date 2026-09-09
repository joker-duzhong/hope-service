import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession

from core import auth_scope, security
from core.apps_config import AppConfig
from core.database import get_db
from core.exceptions import ForbiddenException, register_exception_handlers
from core.users import scan_service
from core.users.dependencies import get_current_user
from core.users.scan_router import router
from core.users.scan_schemas import ScanStatus
from core.users.schemas import UserResponse
from core.users.services import UserService


def make_user(**overrides):
    fields = dict(
        id=uuid4(), phone="13800138000", is_active=True, is_deleted=False,
        is_superuser=False, token_version=0, nickname="测试用户", roles=[],
    )
    fields.update(overrides)
    return SimpleNamespace(**fields)


@pytest.fixture
async def store(monkeypatch):
    redis = FakeRedis(decode_responses=True)
    monkeypatch.setattr(scan_service, "redis_client", redis)
    monkeypatch.setattr(scan_service, "REGISTERED_APPS", {
        "test_app": AppConfig(key="test_app", name="测试应用", created_at="2026-09-08", wechat_appids=["private-appid"]),
        "other_app": AppConfig(key="other_app", name="另一应用", created_at="2026-09-08"),
        "disabled_app": AppConfig(key="disabled_app", name="已停用", created_at="2026-09-08", is_active=False),
        "admin_web": AppConfig(key="admin_web", name="管理后台", created_at="2026-09-08"),
    })
    monkeypatch.setattr(auth_scope, "REGISTERED_APPS", scan_service.REGISTERED_APPS)
    yield redis
    await redis.aclose()


@pytest.fixture
def user():
    return make_user()


@pytest.fixture
def database(user, monkeypatch):
    database = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    monkeypatch.setattr(UserService, "build_user_response", AsyncMock(return_value=UserResponse.model_validate(user)))
    return database


@pytest.fixture
def token_factory(monkeypatch):
    factory = AsyncMock(return_value=("test-access", "test-refresh"))
    monkeypatch.setattr(scan_service, "create_token_pair", factory)
    return factory


async def confirmed_session(user, app_key="test_app"):
    created = await scan_service.create_session(app_key)
    await scan_service.transition(created.transaction_id, "scanned")
    await scan_service.transition(created.transaction_id, "confirm", user)
    poll = await scan_service.poll_session(created.transaction_id, created.poll_token)
    return created, poll.exchange_code


async def test_available_apps_only_exposes_active_key_and_name(store):
    apps = [app.model_dump() for app in scan_service.available_apps()]
    assert apps == [
        {"app_key": "test_app", "name": "测试应用"},
        {"app_key": "other_app", "name": "另一应用"},
        {"app_key": "admin_web", "name": "管理后台"},
    ]
    assert "private-appid" not in json.dumps(apps)


@pytest.mark.parametrize("app_key", ["missing", "disabled_app"])
async def test_unavailable_app_cannot_create_session(store, app_key):
    with pytest.raises(HTTPException) as error:
        await scan_service.create_session(app_key)
    assert error.value.status_code == 400
    assert await store.dbsize() == 0


async def test_new_session_defaults_to_waiting_and_hashes_poll_token(store):
    created = await scan_service.create_session("test_app")
    assert created.status == ScanStatus.WAITING_SCAN
    assert created.poll_interval_seconds == 2
    raw = await store.get(scan_service._key(created.transaction_id))
    saved = json.loads(raw)
    assert created.poll_token not in raw
    assert saved["poll_token_hash"] == hashlib.sha256(created.poll_token.encode()).hexdigest()
    assert saved["user_id"] is None
    assert 0 < await store.ttl(scan_service._key(created.transaction_id)) <= 300


async def test_anonymous_scan_notification_is_idempotent_and_does_not_extend_ttl(store):
    created = await scan_service.create_session("test_app")
    key = scan_service._key(created.transaction_id)
    await store.pexpire(key, 60000)
    first = await scan_service.transition(created.transaction_id, "scanned")
    second = await scan_service.transition(created.transaction_id, "scanned")
    assert first.status == second.status == ScanStatus.PENDING
    assert 0 < await store.pttl(key) <= 60000
    assert first.expires_at == created.expires_at
    state = json.loads(await store.get(key))
    assert state["user_id"] is None
    assert state["exchange_code"] is None


async def test_public_info_never_contains_credentials_or_user(store, user):
    created, exchange_code = await confirmed_session(user)
    info = (await scan_service.session_info(created.transaction_id)).model_dump_json()
    assert exchange_code not in info
    assert created.poll_token not in info
    assert str(user.id) not in info
    assert user.phone not in info
    scanned = await scan_service.transition(created.transaction_id, "scanned")
    assert scanned.status == ScanStatus.CONFIRMED
    assert exchange_code not in scanned.model_dump_json()


async def test_poll_is_bound_to_original_client_and_returns_stable_code(store, user):
    created, code = await confirmed_session(user)
    other = await scan_service.create_session("test_app")
    with pytest.raises(HTTPException) as error:
        await scan_service.poll_session(created.transaction_id, other.poll_token)
    assert error.value.status_code == 403
    first = await scan_service.poll_session(created.transaction_id, created.poll_token)
    second = await scan_service.poll_session(created.transaction_id, created.poll_token)
    assert first.exchange_code == second.exchange_code == code


async def test_confirm_requires_logged_in_user_and_pending_state(store, user):
    created = await scan_service.create_session("test_app")
    with pytest.raises(HTTPException) as error:
        await scan_service.transition(created.transaction_id, "confirm", user)
    assert error.value.status_code == 409
    await scan_service.transition(created.transaction_id, "scanned")
    with pytest.raises(HTTPException) as error:
        await scan_service.transition(created.transaction_id, "confirm")
    assert error.value.status_code == 401


@pytest.mark.parametrize("changes", [{"phone": None}, {"is_active": False}, {"is_deleted": True}])
async def test_ineligible_user_cannot_confirm(store, changes):
    created = await scan_service.create_session("test_app")
    await scan_service.transition(created.transaction_id, "scanned")
    with pytest.raises((HTTPException, ForbiddenException)):
        await scan_service.transition(created.transaction_id, "confirm", make_user(**changes))
    assert (await scan_service.session_info(created.transaction_id)).status == ScanStatus.PENDING


async def test_confirmation_cannot_be_overwritten_by_another_user(store, user):
    created, code = await confirmed_session(user)
    response = await scan_service.transition(created.transaction_id, "confirm", user)
    assert response.status == ScanStatus.CONFIRMED
    with pytest.raises(HTTPException) as error:
        await scan_service.transition(created.transaction_id, "confirm", make_user())
    assert error.value.status_code == 409
    assert (await scan_service.poll_session(created.transaction_id, created.poll_token)).exchange_code == code


async def test_concurrent_confirmation_has_only_one_authorized_user(store):
    created = await scan_service.create_session("test_app")
    await scan_service.transition(created.transaction_id, "scanned")
    results = await asyncio.gather(*(
        scan_service.transition(created.transaction_id, "confirm", make_user()) for attempt in range(6)
    ), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert all(not isinstance(result, Exception) or isinstance(result, HTTPException) for result in results)


@pytest.mark.parametrize("scanned", [True, False])
async def test_cancellation_is_terminal_and_idempotent(store, user, scanned):
    created = await scan_service.create_session("test_app")
    if scanned:
        await scan_service.transition(created.transaction_id, "scanned")
    assert (await scan_service.transition(created.transaction_id, "cancel", user)).status == ScanStatus.CANCELLED
    assert (await scan_service.transition(created.transaction_id, "cancel", user)).status == ScanStatus.CANCELLED
    assert (await scan_service.transition(created.transaction_id, "scanned")).status == ScanStatus.CANCELLED
    with pytest.raises(HTTPException):
        await scan_service.transition(created.transaction_id, "confirm", user)


async def test_confirmed_session_cannot_be_cancelled(store, user):
    created, code = await confirmed_session(user)
    with pytest.raises(HTTPException) as error:
        await scan_service.transition(created.transaction_id, "cancel", user)
    assert error.value.status_code == 409


async def test_confirm_cancel_race_has_one_terminal_outcome(store, user):
    created = await scan_service.create_session("test_app")
    await scan_service.transition(created.transaction_id, "scanned")
    results = await asyncio.gather(
        scan_service.transition(created.transaction_id, "confirm", user),
        scan_service.transition(created.transaction_id, "cancel", user), return_exceptions=True,
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1


async def test_exchange_consumes_once_and_issues_tokens_only_once(store, user, database, token_factory):
    created, code = await confirmed_session(user)
    response = await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    assert response.access_token == "test-access"
    assert response.user.id == user.id
    token_factory.assert_awaited_once_with(user.id, user.token_version, "test_app")
    poll = await scan_service.poll_session(created.transaction_id, created.poll_token)
    assert poll.status == ScanStatus.CONSUMED
    assert poll.exchange_code is None
    with pytest.raises(HTTPException) as error:
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    assert error.value.status_code == 409
    assert token_factory.await_count == 1


async def test_concurrent_exchange_issues_one_token_pair(store, user, database, token_factory):
    created, code = await confirmed_session(user)
    results = await asyncio.gather(*(
        scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
        for attempt in range(8)
    ), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == 1
    assert token_factory.await_count == 1


@pytest.mark.parametrize("wrong_field", ["poll_token", "exchange_code"])
async def test_wrong_exchange_credentials_never_consume_session(store, user, database, token_factory, wrong_field):
    created, code = await confirmed_session(user)
    poll_token = created.poll_token
    if wrong_field == "poll_token":
        poll_token = "x" * 43
    else:
        code = "x" * 43
    with pytest.raises(HTTPException) as error:
        await scan_service.exchange_session(created.transaction_id, code, poll_token, database)
    assert error.value.status_code == 403
    token_factory.assert_not_awaited()
    assert (await scan_service.session_info(created.transaction_id)).status == ScanStatus.CONFIRMED


async def test_exchange_code_cannot_be_used_in_another_session(store, user, database, token_factory):
    first, first_code = await confirmed_session(user)
    second, second_code = await confirmed_session(user, "other_app")
    with pytest.raises(HTTPException):
        await scan_service.exchange_session(second.transaction_id, first_code, second.poll_token, database)
    token_factory.assert_not_awaited()


@pytest.mark.parametrize("changes", [
    {"is_active": False}, {"is_deleted": True}, {"token_version": 1}, {"phone": None},
])
async def test_account_is_revalidated_at_exchange(store, user, database, token_factory, changes):
    created, code = await confirmed_session(user)
    for name, value in changes.items():
        setattr(user, name, value)
    with pytest.raises((HTTPException, ForbiddenException)):
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    token_factory.assert_not_awaited()


async def test_missing_account_cannot_exchange(store, user, database, token_factory):
    created, code = await confirmed_session(user)
    UserService.get_by_id.return_value = None
    with pytest.raises(HTTPException) as error:
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    assert error.value.status_code == 401
    token_factory.assert_not_awaited()


async def test_disabled_app_rejected_at_confirmation_and_exchange(store, user, database, token_factory):
    pending = await scan_service.create_session("test_app")
    await scan_service.transition(pending.transaction_id, "scanned")
    created, code = await confirmed_session(user)
    scan_service.REGISTERED_APPS["test_app"].is_active = False
    with pytest.raises(HTTPException):
        await scan_service.transition(pending.transaction_id, "confirm", user)
    with pytest.raises(HTTPException):
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    token_factory.assert_not_awaited()


async def test_admin_app_requires_superuser_at_confirmation_and_exchange(store, user, database, token_factory):
    created = await scan_service.create_session("admin_web")
    await scan_service.transition(created.transaction_id, "scanned")
    with pytest.raises(HTTPException) as error:
        await scan_service.transition(created.transaction_id, "confirm", user)
    assert error.value.status_code == 403
    user.is_superuser = True
    await scan_service.transition(created.transaction_id, "confirm", user)
    poll = await scan_service.poll_session(created.transaction_id, created.poll_token)
    user.is_superuser = False
    with pytest.raises(HTTPException):
        await scan_service.exchange_session(created.transaction_id, poll.exchange_code, created.poll_token, database)
    token_factory.assert_not_awaited()


@pytest.mark.parametrize("expire_mode", ["redis", "deadline"])
async def test_expired_sessions_cannot_be_revived_or_exchanged(store, user, database, token_factory, expire_mode):
    created, code = await confirmed_session(user)
    key = scan_service._key(created.transaction_id)
    if expire_mode == "redis":
        await store.delete(key)
    else:
        saved = json.loads(await store.get(key))
        saved["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        await store.set(key, json.dumps(saved), ex=300)
    assert (await scan_service.poll_session(created.transaction_id, created.poll_token)).status == ScanStatus.EXPIRED
    assert (await scan_service.session_info(created.transaction_id)).status == ScanStatus.EXPIRED
    for action in ("scanned", "confirm", "cancel"):
        with pytest.raises(HTTPException) as error:
            await scan_service.transition(created.transaction_id, action, user)
        assert error.value.status_code == 410
    with pytest.raises(HTTPException) as error:
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    assert error.value.status_code == 410
    token_factory.assert_not_awaited()


async def test_compare_and_swap_does_not_recreate_expired_session(store):
    created = await scan_service.create_session("test_app")
    raw, session = await scan_service._load(created.transaction_id)
    await store.delete(scan_service._key(created.transaction_id))
    session.status = ScanStatus.PENDING
    assert await scan_service._replace(raw, session) is False
    assert await store.exists(scan_service._key(created.transaction_id)) == 0


async def test_token_issuance_failure_does_not_reopen_consumed_session(store, user, database, token_factory, caplog):
    created, code = await confirmed_session(user)
    token_factory.side_effect = RedisConnectionError("private-internal-error")
    with pytest.raises(HTTPException) as error:
        await scan_service.exchange_session(created.transaction_id, code, created.poll_token, database)
    assert error.value.status_code == 503
    assert (await scan_service.session_info(created.transaction_id)).status == ScanStatus.CONSUMED
    assert "private-internal-error" not in caplog.text
    assert code not in caplog.text
    assert created.poll_token not in caplog.text


async def test_bad_redis_data_fails_closed(store):
    transaction_id = uuid4()
    await store.set(scan_service._key(transaction_id), "not-json", ex=300)
    with pytest.raises(HTTPException) as error:
        await scan_service.session_info(transaction_id)
    assert error.value.status_code == 503


async def test_legacy_wechat_sessions_are_not_accepted(store):
    transaction_id = uuid4()
    await store.set(f"wechat_scan:{transaction_id}", json.dumps({"status": "SUCCESS", "user_id": str(uuid4())}))
    assert (await scan_service.session_info(transaction_id)).status == ScanStatus.EXPIRED


async def test_polling_limit_does_not_disclose_code_to_wrong_client(store, user):
    created, code = await confirmed_session(user)
    for attempt in range(scan_service.POLL_LIMIT_PER_MINUTE - 1):
        await scan_service.poll_session(created.transaction_id, created.poll_token)
    with pytest.raises(HTTPException) as error:
        await scan_service.poll_session(created.transaction_id, created.poll_token)
    assert error.value.status_code == 429
    with pytest.raises(HTTPException) as error:
        await scan_service.poll_session(created.transaction_id, "x" * 43)
    assert error.value.status_code == 403


@pytest.fixture
async def api(store, database, token_factory):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_db] = lambda: database
    register_exception_handlers(app)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield app, client


async def test_http_full_scan_login_flow(api, user, monkeypatch, store):
    app, client = api
    async def authenticated(request: Request):
        request.state.auth_scope = "passport"
        return user
    app.dependency_overrides[get_current_user] = authenticated
    monkeypatch.setattr(security, "redis_client", store)
    monkeypatch.setattr(scan_service, "create_token_pair", security.create_token_pair)
    response = await client.get("/api/v1/auth/scan/apps")
    assert response.status_code == 200
    assert response.json()["data"][0] == {"app_key": "test_app", "name": "测试应用"}
    response = await client.post("/api/v1/auth/scan/sessions", json={"app_key": "test_app"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    created = response.json()["data"]
    assert created["status"] == "WAITING_SCAN"
    base = f"/api/v1/auth/scan/sessions/{created['transaction_id']}"
    headers = {"X-Scan-Token": created["poll_token"]}
    assert (await client.get(base)).status_code == 422
    assert (await client.get(base + "/info")).json()["data"]["status"] == "WAITING_SCAN"
    assert (await client.post(base + "/scanned")).json()["data"]["status"] == "PENDING"
    confirm = await client.post(base + "/confirm", json={"user_id": str(uuid4()), "app_key": "other_app"})
    assert confirm.status_code == 200
    assert "exchange_code" not in confirm.json()["data"]
    polled = await client.get(base, headers=headers)
    data = polled.json()["data"]
    assert data["status"] == "CONFIRMED"
    assert data["app"]["app_key"] == "test_app"
    payload = {"transaction_id": created["transaction_id"], "exchange_code": data["exchange_code"]}
    response = await client.post("/api/v1/auth/scan/exchange", json=payload, headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    login = response.json()["data"]
    assert login["user"]["id"] == str(user.id)
    assert login["user"]["needs_phone_binding"] is False
    assert security.decode_token(login["access_token"])["sub"] == str(user.id)
    assert security.decode_token(login["access_token"])["app_scope"] == "test_app"
    assert login["app_scope"] == "test_app"
    refresh_id = security.decode_token(login["refresh_token"])["jti"]
    assert await store.exists(f"auth:refresh:{refresh_id}") == 1
    assert (await client.get(base, headers=headers)).json()["data"]["status"] == "CONSUMED"
    assert (await client.post("/api/v1/auth/scan/exchange", json=payload, headers=headers)).status_code == 409


async def test_http_confirmation_and_cancellation_require_authentication(api):
    app, client = api
    created = (await client.post("/api/v1/auth/scan/sessions", json={"app_key": "test_app"})).json()["data"]
    base = f"/api/v1/auth/scan/sessions/{created['transaction_id']}"
    for action in ("confirm", "cancel"):
        assert (await client.post(base + "/" + action)).status_code in (401, 403)


@pytest.mark.parametrize("payload", [{}, {"app_key": "missing"}, {"app_key": "disabled_app"}, {"app_key": "test_app", "redirect_url": "https://untrusted.invalid"}])
async def test_http_invalid_app_or_extra_creation_parameters_are_rejected(api, payload):
    app, client = api
    assert (await client.post("/api/v1/auth/scan/sessions", json=payload)).status_code in (400, 422)


async def test_http_create_rate_limit(api):
    app, client = api
    for attempt in range(scan_service.CREATE_LIMIT_PER_MINUTE):
        assert (await client.post("/api/v1/auth/scan/sessions", json={"app_key": "test_app"})).status_code == 200
    response = await client.post("/api/v1/auth/scan/sessions", json={"app_key": "test_app"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


async def test_http_redis_failure_returns_sanitized_503(api, monkeypatch, caplog):
    app, client = api
    monkeypatch.setattr(scan_service.redis_client, "eval", AsyncMock(side_effect=RedisConnectionError("secret-marker")))
    response = await client.post("/api/v1/auth/scan/sessions", json={"app_key": "test_app"})
    assert response.status_code == 503
    assert "secret-marker" not in response.text
    assert "secret-marker" not in caplog.text


async def test_http_exchange_rejects_unicode_credential(api):
    app, client = api
    response = await client.post("/api/v1/auth/scan/exchange", json={
        "transaction_id": str(uuid4()), "exchange_code": "密" * 43,
    }, headers={"X-Scan-Token": "x" * 43})
    assert response.status_code == 422
