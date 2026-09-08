import asyncio
import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fakeredis.aioredis import FakeRedis
from fastapi import HTTPException
from redis.exceptions import ConnectionError as RedisConnectionError, ResponseError

from core import security
from core.users.schemas import RefreshRequest
from core.users.services import UserService

users_router = importlib.import_module("core.users.router")


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    yield


@pytest.fixture
async def sessions(monkeypatch):
    redis = FakeRedis(decode_responses=True)
    monkeypatch.setattr(security, "redis_client", redis)
    monkeypatch.setattr(security.settings, "SECRET_KEY", "test-only-signing-key-not-for-production")
    monkeypatch.setattr(security.settings, "ALGORITHM", "HS256")
    yield redis
    await redis.aclose()


def session_key(token):
    return f"auth:refresh:{security.decode_token(token)['jti']}"


async def test_rotation_replaces_session_and_rejects_immediate_replay(sessions):
    user_id = uuid4()
    _, old_token = await security.create_token_pair(user_id, 2)
    pair = await security.rotate_refresh_token(old_token, user_id, 2)
    assert pair is not None
    assert await sessions.get(session_key(old_token)) is None
    assert json.loads(await sessions.get(session_key(pair[1]))) == {"sub": str(user_id), "token_version": 2}
    assert await sessions.ttl(session_key(pair[1])) > 0
    assert await security.rotate_refresh_token(old_token, user_id, 2) is None


async def test_concurrent_rotation_creates_one_session(sessions):
    user_id = uuid4()
    _, old_token = await security.create_token_pair(user_id)
    results = await asyncio.gather(*(security.rotate_refresh_token(old_token, user_id, 0) for index in range(8)))
    assert sum(result is not None for result in results) == 1
    assert len(await sessions.keys("auth:refresh:*")) == 1


async def test_database_failure_does_not_consume_old_session(sessions, monkeypatch):
    user = SimpleNamespace(id=uuid4(), token_version=0, is_active=True)
    _, old_token = await security.create_token_pair(user.id)
    lookup = AsyncMock(side_effect=RuntimeError("simulated database failure"))
    monkeypatch.setattr(UserService, "get_by_id", lookup)
    with pytest.raises(RuntimeError):
        await users_router.refresh_token(RefreshRequest(refresh_token=old_token), None)
    assert await sessions.get(session_key(old_token)) is not None
    lookup.side_effect = None
    lookup.return_value = user
    response = await users_router.refresh_token(RefreshRequest(refresh_token=old_token), None)
    assert response.data.refresh_token != old_token


async def test_redis_failure_before_rotation_preserves_old_session(sessions, monkeypatch):
    user_id = uuid4()
    _, old_token = await security.create_token_pair(user_id)

    async def fail(*args):
        raise RedisConnectionError()

    monkeypatch.setattr(sessions, "eval", fail)
    with pytest.raises(RedisConnectionError):
        await security.rotate_refresh_token(old_token, user_id, 0)
    assert await sessions.get(session_key(old_token)) is not None


async def test_failed_new_session_write_preserves_old_session(sessions, monkeypatch):
    user_id = uuid4()
    _, old_token = await security.create_token_pair(user_id)
    monkeypatch.setattr(security.settings, "REFRESH_TOKEN_EXPIRE_DAYS", 0)
    monkeypatch.setattr(security, "_build_token_pair", lambda *args: ("unused-access", "unused-refresh", "new-id"))
    with pytest.raises(ResponseError):
        await security.rotate_refresh_token(old_token, user_id, 0)
    assert await sessions.get(session_key(old_token)) is not None


@pytest.mark.parametrize("stored", ['not-json', '[]', '{"sub":"wrong","token_version":0}'])
async def test_malformed_or_mismatched_session_cannot_rotate(sessions, stored):
    user_id = uuid4()
    _, old_token = await security.create_token_pair(user_id)
    await sessions.set(session_key(old_token), stored)
    assert await security.rotate_refresh_token(old_token, user_id, 0) is None


@pytest.mark.parametrize("active, version", [(False, 0), (True, 1)])
async def test_disabled_or_revoked_user_cannot_refresh(sessions, monkeypatch, active, version):
    user = SimpleNamespace(id=uuid4(), token_version=version, is_active=active)
    _, old_token = await security.create_token_pair(user.id)
    monkeypatch.setattr(UserService, "get_by_id", AsyncMock(return_value=user))
    with pytest.raises(HTTPException) as error:
        await users_router.refresh_token(RefreshRequest(refresh_token=old_token), None)
    assert error.value.status_code == 401


async def test_access_token_is_rejected_before_user_lookup(sessions, monkeypatch):
    access_token, _ = await security.create_token_pair(uuid4())
    lookup = AsyncMock()
    monkeypatch.setattr(UserService, "get_by_id", lookup)
    with pytest.raises(HTTPException) as error:
        await users_router.refresh_token(RefreshRequest(refresh_token=access_token), None)
    assert error.value.status_code == 401
    lookup.assert_not_awaited()
