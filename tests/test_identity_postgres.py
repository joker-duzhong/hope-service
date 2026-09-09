import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.associations import user_roles_table
from core.database import Base
from core.roles.models import Role
from core.users.identity_schemas import StoredIdentity
from core.users.identity_service import link_identity
from core.users.models import User, UserIdentity
from core.users.services import UserService

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
async def isolated_database():
    url = os.environ.get("PASSPORT_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set PASSPORT_TEST_DATABASE_URL to a disposable local passport_identity_test database")
    parsed = make_url(url)
    if parsed.host not in {"127.0.0.1", "localhost"} or parsed.database != "passport_identity_test":
        pytest.fail("Refusing non-local or non-test database")
    schema = "identity_qa_" + uuid4().hex
    engine = create_async_engine(url, connect_args={"server_settings": {"search_path": schema}}, echo=False)
    tables = [User.__table__, UserIdentity.__table__, Role.__table__, user_roles_table]
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await engine.dispose()


def identity(appid, openid="same-subject", **changes):
    return StoredIdentity(channel="h5", appid=appid, openid=openid, app_scope="passport",
                          expires_at=datetime.now(timezone.utc) + timedelta(minutes=10), **changes)


async def test_parallel_h5_and_miniapp_binding_share_one_user(isolated_database):
    async def bind(appid):
        async with isolated_database() as session:
            user = await link_identity(session, identity(appid), "13800138000")
            return user.id
    users = await asyncio.gather(*(bind(f"wechat-{index}") for index in range(8)))
    assert len(set(users)) == 1
    async with isolated_database() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(UserIdentity)) == 8


async def test_parallel_same_identity_only_creates_one_user_and_link(isolated_database):
    async def bind():
        async with isolated_database() as session:
            return (await link_identity(session, identity("public"), "13800138000")).id
    users = await asyncio.gather(*(bind() for index in range(8)))
    assert len(set(users)) == 1
    async with isolated_database() as session:
        assert await session.scalar(select(func.count()).select_from(User)) == 1
        assert await session.scalar(select(func.count()).select_from(UserIdentity)) == 1


async def test_parallel_conflicting_phones_never_move_identity(isolated_database):
    async def bind(phone):
        async with isolated_database() as session:
            return (await link_identity(session, identity("public"), phone)).phone
    results = await asyncio.gather(bind("13800138000"), bind("13900139000"), return_exceptions=True)
    winners = [value for value in results if isinstance(value, str)]
    assert len(winners) == 1
    assert any(isinstance(value, HTTPException) and value.status_code == 409 for value in results)
    async with isolated_database() as session:
        user = (await session.scalars(select(User))).one()
        linked = (await session.scalars(select(UserIdentity))).one()
        assert user.phone == winners[0]
        assert linked.user_id == user.id


async def test_real_commit_preserves_roles_and_existing_platform_id(isolated_database):
    async with isolated_database() as session:
        role = Role(scope="app_a", name="Member", code="member")
        user = User(phone="13800138000", roles=[role])
        session.add(user)
        await session.commit()
        original_id = user.id
    async with isolated_database() as session:
        user = await link_identity(session, identity("public"), "13800138000")
        assert user.id == original_id
        profile = await UserService.build_scoped_user_response(session, user, "app_a")
        assert len(profile.roles) == 1
        assert profile.roles[0].scope == "app_a"
        assert not (await UserService.build_scoped_user_response(session, user, "app_b")).roles


async def test_legacy_conflict_rolls_back_without_losing_data(isolated_database):
    async with isolated_database() as session:
        legacy = User(nickname="Existing business data", roles=[])
        phone_user = User(phone="13800138000", roles=[])
        session.add_all([legacy, phone_user])
        await session.flush()
        session.add(UserIdentity(user_id=legacy.id, provider="wechat", provider_app_id="public", subject="same-subject"))
        await session.commit()
        legacy_id = legacy.id
    async with isolated_database() as session:
        with pytest.raises(HTTPException) as error:
            await link_identity(session, identity("public", legacy_user_id=legacy_id, token_version=0), "13800138000")
        assert error.value.status_code == 409
    async with isolated_database() as session:
        legacy = await session.get(User, legacy_id)
        assert legacy.phone is None
        assert legacy.nickname == "Existing business data"
        assert await session.scalar(select(func.count()).select_from(User)) == 2
