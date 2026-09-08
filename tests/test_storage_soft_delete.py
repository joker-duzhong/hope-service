from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

import core.roles.models
from core.exceptions import BadRequestException, NotFoundException
from core.oss.qiniu_client import QiniuClient
from core.storage.services import StorageService
from core.storage.tasks import delete_oss_file_task


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    yield


def make_session(resource):
    return SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: resource)),
        commit=AsyncMock(), delete=AsyncMock(),
    )


async def test_deletion_only_marks_record_and_never_enqueues_cleanup(monkeypatch):
    owner = uuid4()
    resource = SimpleNamespace(id=uuid4(), owner=owner, is_deleted=False, url="shared-key", thumb_url="shared-thumb")
    survivor = SimpleNamespace(is_deleted=False, url=resource.url)
    db = make_session(resource)
    enqueue = Mock(side_effect=AssertionError("physical cleanup must not be scheduled"))
    monkeypatch.setattr(delete_oss_file_task, "delay", enqueue)
    assert await StorageService.delete_resource(db, resource.id, owner) is True
    assert resource.is_deleted is True
    assert survivor.is_deleted is False
    assert resource.url == survivor.url
    db.commit.assert_awaited_once()
    db.delete.assert_not_awaited()
    enqueue.assert_not_called()


async def test_deletion_still_checks_owner():
    resource = SimpleNamespace(id=uuid4(), owner=uuid4(), is_deleted=False)
    db = make_session(resource)
    with pytest.raises(BadRequestException):
        await StorageService.delete_resource(db, resource.id, uuid4())
    assert resource.is_deleted is False
    db.commit.assert_not_awaited()


async def test_missing_resource_is_not_committed():
    db = make_session(None)
    with pytest.raises(NotFoundException):
        await StorageService.delete_resource(db, uuid4(), uuid4())
    db.commit.assert_not_awaited()


def test_legacy_task_is_inert_and_client_has_no_delete_method():
    assert delete_oss_file_task.run("legacy-object") is False
    assert not hasattr(QiniuClient, "delete_file_from_oss")
