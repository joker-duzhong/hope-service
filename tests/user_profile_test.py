import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.exceptions import BadRequestException
from core.storage.schemas import ResourceResponse
from core.users.schemas import UserResponse
from core.users.services import UserService


def build_user(**overrides):
    now = datetime.now(timezone.utc)
    data = {
        "id": uuid.uuid4(),
        "openid": None,
        "username": "current_user",
        "email": "current@example.com",
        "phone": None,
        "nickname": "Current",
        "avatar": None,
        "source": "default",
        "is_active": True,
        "is_superuser": False,
        "roles": [],
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_unique_profile_fields_rejects_existing_username(monkeypatch):
    existing = build_user(username="taken")

    async def get_by_username(_db, _username):
        return existing

    monkeypatch.setattr(UserService, "get_by_username", get_by_username)

    with pytest.raises(BadRequestException) as exc_info:
        await UserService.ensure_unique_profile_fields(None, username="taken")
    assert exc_info.value.message == "用户名已存在"


@pytest.mark.asyncio
async def test_unique_profile_fields_rejects_existing_email(monkeypatch):
    existing = build_user(email="taken@example.com")

    async def get_by_email(_db, _email):
        return existing

    monkeypatch.setattr(UserService, "get_by_email", get_by_email)

    with pytest.raises(BadRequestException) as exc_info:
        await UserService.ensure_unique_profile_fields(None, email="taken@example.com")
    assert exc_info.value.message == "邮箱已存在"


@pytest.mark.asyncio
async def test_unique_profile_fields_allows_current_user(monkeypatch):
    existing = build_user(username="current_user", email="current@example.com")

    async def get_by_username(_db, _username):
        return existing

    async def get_by_email(_db, _email):
        return existing

    monkeypatch.setattr(UserService, "get_by_username", get_by_username)
    monkeypatch.setattr(UserService, "get_by_email", get_by_email)

    await UserService.ensure_unique_profile_fields(
        None,
        current_user_id=existing.id,
        username=existing.username,
        email=existing.email,
    )


@pytest.mark.asyncio
async def test_user_response_avatar_wraps_https_url():
    user = build_user(avatar="https://cdn.example.com/avatar.png")

    response = await UserService.build_user_response(None, user)

    assert response.avatar is not None
    assert response.avatar.url == "https://cdn.example.com/avatar.png"
    assert response.avatar.id is None
    assert response.is_superuser is False
    assert isinstance(response, UserResponse)


@pytest.mark.asyncio
async def test_user_response_includes_superuser_flag():
    user = build_user(is_superuser=True)

    response = await UserService.build_user_response(None, user)

    assert response.is_superuser is True


@pytest.mark.asyncio
async def test_user_response_avatar_expands_resource_id(monkeypatch):
    resource_id = uuid.uuid4()
    resource = ResourceResponse(
        id=resource_id,
        name="avatar.png",
        url="https://cdn.example.com/avatars/avatar.png",
        thumb_url="https://cdn.example.com/avatars/avatar_thumb.png",
        size=128,
        type="image/png",
        scope="profile",
        hash=uuid.uuid4().hex,
        owner=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    async def get_resource_response_or_none(_db, requested_id):
        assert requested_id == resource_id
        return resource

    monkeypatch.setattr(
        "core.users.services.StorageService.get_resource_response_or_none",
        get_resource_response_or_none,
    )

    user = build_user(avatar=str(resource_id))

    response = await UserService.build_user_response(None, user)

    assert response.avatar is not None
    assert response.avatar.id == resource_id
    assert response.avatar.url == "https://cdn.example.com/avatars/avatar.png"
    assert response.avatar.thumb_url == "https://cdn.example.com/avatars/avatar_thumb.png"
    assert response.avatar.scope == "profile"
