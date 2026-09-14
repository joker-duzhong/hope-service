import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from pydantic import ValidationError

from apps.aurakey.router import get_invite_info
from apps.aurakey import router as aurakey_router
from apps.aurakey import tasks as aurakey_tasks
from apps.aurakey.config import merge_aurakey_config
from apps.aurakey.schemas import (
    AssetLogItem,
    AurakeySystemConfigResponse,
    InviteInfoResponse,
    ProductItem,
    TaskGenerateRequest,
    TaskStreamGenerateRequest,
    UserEntitlementResponse,
    UserProfileResponse,
)
from apps.aurakey.services import AurakeyService
from apps.ai_gateway.schemas import ImageStreamChatRequest
from core.llm.engine import (
    _image_generation_url_from_config,
    extract_image_result_from_content,
    parse_image_generation_result,
)


def test_celery_worker_registers_core_models_and_tasks():
    code = (
        "from sqlalchemy.orm import configure_mappers\n"
        "from worker.celery_app import celery_app\n"
        "configure_mappers()\n"
        "assert 'aurakey_stream_image_task' in celery_app.tasks\n"
        "assert 'aurakey_fail_stale_stream_image_tasks' in celery_app.tasks\n"
        "assert 'apps.just_right.tasks.notify_state_updates' in celery_app.tasks\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_asset_log_item_validates_from_orm_attributes():
    log_id = uuid.uuid4()
    log = SimpleNamespace(
        id=log_id,
        type=2,
        amount=-10,
        balance_after=90,
        description="生成插画(pro_1)",
    )

    item = AssetLogItem.model_validate(log)

    assert item.id == log_id
    assert item.type == 2
    assert item.amount == -10
    assert item.balance_after == 90
    assert item.description == "生成插画(pro_1)"


def test_extract_image_result_from_stream_content():
    content = (
        "\n\n> 生成中...\n\n"
        "![https://pro.filesystem.site/cdn/20260508/demo.png]"
        "(https://pro.filesystem.site/cdn/20260508/demo.png)\n\n"
        "[点击下载](https://pro.filesystem.site/cdn/download/20260508/demo.png)"
    )

    result = extract_image_result_from_content(content)

    assert result["image_url"] == "https://pro.filesystem.site/cdn/20260508/demo.png"
    assert result["download_url"] == "https://pro.filesystem.site/cdn/download/20260508/demo.png"


def test_parse_image_generation_result_reads_b64_json():
    result = parse_image_generation_result(
        {
            "created": 1781057917,
            "data": [{"b64_json": "cG5nLWJ5dGVz"}],
            "model": "gpt-image-2",
            "object": "list",
        }
    )

    assert result["b64_json"] == "cG5nLWJ5dGVz"
    assert result["image_url"] is None
    assert result["mime_type"] == "image/png"
    assert result["model"] == "gpt-image-2"


def test_image_generation_url_from_config_uses_images_generations_path():
    assert (
        _image_generation_url_from_config({"base_url": "https://oneapi.example.com/v1"})
        == "https://oneapi.example.com/v1/images/generations"
    )
    assert (
        _image_generation_url_from_config({"base_url": "https://oneapi.example.com/v1/images/generations"})
        == "https://oneapi.example.com/v1/images/generations"
    )
    assert (
        _image_generation_url_from_config({"image_chat_url": "https://oneapi.example.com/v1/chat/completions"})
        == "https://oneapi.example.com/v1/images/generations"
    )


def test_image_stream_chat_request_defaults():
    req = ImageStreamChatRequest(messages=[{"role": "user", "content": "生成一张猫图"}])

    assert req.model == "gpt-image-2"
    assert req.temperature == 0.7
    assert req.top_p == 1.0
    assert req.extra_body == {}


def test_task_stream_generate_request_public_defaults_to_private():
    req = TaskStreamGenerateRequest(
        prompt="生成一张猫图",
        model_name="gpt-image-2",
        aspect_ratio="1:1",
    )

    assert req.is_public is False


def test_task_stream_generate_request_accepts_public_flag():
    req = TaskStreamGenerateRequest(
        prompt="生成一张猫图",
        model_name="gpt-image-2",
        aspect_ratio="1:1",
        is_public=True,
    )

    assert req.is_public is True


def test_stream_image_timeout_defaults_to_600(monkeypatch):
    monkeypatch.setattr(aurakey_tasks.settings, "LLM_DEFAULT_PROVIDER", "zaiwenopenapi")
    monkeypatch.setattr(aurakey_tasks.settings, "LLM_PROVIDERS", {"zaiwenopenapi": {"timeout": 120}})

    assert aurakey_tasks._get_stream_image_timeout() == 600.0


def test_stream_image_timeout_uses_provider_image_timeout(monkeypatch):
    monkeypatch.setattr(aurakey_tasks.settings, "LLM_DEFAULT_PROVIDER", "zaiwenopenapi")
    monkeypatch.setattr(aurakey_tasks.settings, "LLM_PROVIDERS", {"zaiwenopenapi": {"image_timeout": "480"}})

    assert aurakey_tasks._get_stream_image_timeout() == 480.0


def test_recent_stream_image_task_is_not_stale(monkeypatch):
    now_utc = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        status="processing",
        remote_task_id=None,
        image_resource_id=None,
        created_at=now_utc - timedelta(seconds=120),
    )
    monkeypatch.setattr(aurakey_tasks, "_get_stream_image_timeout", lambda: 600.0)

    assert aurakey_tasks._is_stale_stream_image_task(task, now_utc=now_utc) is False


def test_old_stream_image_task_without_remote_id_is_stale(monkeypatch):
    now_utc = datetime(2026, 5, 19, 12, 0, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        status="processing",
        remote_task_id=None,
        image_resource_id=None,
        created_at=now_utc - timedelta(seconds=700),
    )
    monkeypatch.setattr(aurakey_tasks, "_get_stream_image_timeout", lambda: 600.0)

    assert aurakey_tasks._is_stale_stream_image_task(task, now_utc=now_utc) is True


@pytest.mark.asyncio
async def test_stale_stream_image_task_fails_and_refunds(monkeypatch):
    user_id = uuid.uuid4()
    task = SimpleNamespace(
        user_id=user_id,
        status="processing",
        remote_task_id=None,
        image_resource_id=None,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=700),
        frozen_points=10,
        point_deductions=[],
        failed_reason=None,
        progress=99,
    )
    asset = SimpleNamespace(user_id=user_id, balance=20)
    added = []

    class FakeDb:
        committed = False

        async def scalar(self, _stmt):
            return asset

        def add(self, item):
            added.append(item)

        async def commit(self):
            self.committed = True

    async def restore_points(_db, target_asset, allocation, fallback_amount, *, description):
        assert target_asset is asset
        assert allocation == []
        assert fallback_amount == 10
        assert description == aurakey_tasks.STREAM_IMAGE_INTERRUPTED_REASON
        target_asset.balance += fallback_amount
        return fallback_amount

    monkeypatch.setattr(aurakey_tasks, "_get_stream_image_timeout", lambda: 600.0)
    monkeypatch.setattr(aurakey_tasks.AurakeyService, "_restore_points", restore_points)

    db = FakeDb()
    result = await aurakey_tasks.fail_stale_stream_image_task_if_needed(db, task)

    assert result is True
    assert task.status == "failed"
    assert task.failed_reason == aurakey_tasks.STREAM_IMAGE_INTERRUPTED_REASON
    assert task.progress == 100
    assert task.frozen_points == 0
    assert task.point_deductions == []
    assert asset.balance == 30
    assert len(added) == 1
    assert db.committed is True


@pytest.mark.asyncio
async def test_reference_image_loads_original_file_bytes(monkeypatch):
    resource_id = uuid.uuid4()
    task = SimpleNamespace(
        prompt="基于参考图生成头像",
        aspect_ratio="1:1",
        reference_image_ids=[str(resource_id)],
    )
    resource = SimpleNamespace(
        id=resource_id,
        name="avatar.png",
        url="https://cdn.example.com/avatar.png",
        type="image/png",
    )

    async def get_resources_by_ids(_db, requested_ids):
        assert requested_ids == [resource_id]
        return {resource_id: resource}

    async def download_remote_file(remote_url, name, timeout, max_bytes):
        assert remote_url == resource.url
        assert name == resource.name
        assert timeout == 20.0
        assert max_bytes == 20 * 1024 * 1024
        return b"png-bytes", "image/png", name

    monkeypatch.setattr(aurakey_tasks.StorageService, "get_resources_by_ids", get_resources_by_ids)
    monkeypatch.setattr(aurakey_tasks.StorageService, "_download_remote_file", download_remote_file)

    image = await aurakey_tasks._load_reference_image_file(None, task)

    assert image == ("avatar.png", b"png-bytes", "image/png")


@pytest.mark.parametrize("request_type", [TaskGenerateRequest, TaskStreamGenerateRequest])
def test_image_task_request_supports_only_one_reference(request_type):
    payload = {"prompt": "参考图生成头像", "model_name": "gpt-image-2", "aspect_ratio": "1:1"}
    assert request_type(**payload).reference_images_ids == []
    resource_id = uuid.uuid4()
    assert request_type(**payload, reference_images_ids=[resource_id]).reference_images_ids == [resource_id]
    with pytest.raises(ValidationError):
        request_type(**payload, reference_images_ids=[resource_id, uuid.uuid4()])


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url, expected_error",
    [
        ("data:image/png;base64,cG5nLWJ5dGVz", None),
        ("data:image/png;base64,???", "base64"),
        ("data:image/png,raw-bytes", "格式无效"),
    ],
)
async def test_reference_image_loads_or_rejects_data_url(monkeypatch, url, expected_error):
    resource_id = uuid.uuid4()
    resource = SimpleNamespace(id=resource_id, name="avatar.png", url=url, type="image/png")
    monkeypatch.setattr(aurakey_tasks.StorageService, "get_resources_by_ids", AsyncMock(return_value={resource_id: resource}))
    download = AsyncMock()
    monkeypatch.setattr(aurakey_tasks.StorageService, "_download_remote_file", download)
    task = SimpleNamespace(reference_image_ids=[str(resource_id)])

    if expected_error:
        with pytest.raises(ValueError, match=expected_error):
            await aurakey_tasks._load_reference_image_file(None, task)
    else:
        assert await aurakey_tasks._load_reference_image_file(None, task) == ("avatar.png", b"png-bytes", "image/png")
    download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("reference_ids", [["invalid-id"], [str(uuid.uuid4()), str(uuid.uuid4())]])
async def test_reference_image_rejects_invalid_or_multiple_stored_ids(reference_ids):
    with pytest.raises(ValueError):
        await aurakey_tasks._load_reference_image_file(None, SimpleNamespace(reference_image_ids=reference_ids))


@pytest.fixture
def image_worker_context(monkeypatch):
    task = SimpleNamespace(
        id=uuid.uuid4(), user_id=uuid.uuid4(), is_deleted=False,
        prompt="生成头像", aspect_ratio="1:1", model_name="gpt-image-2",
        reference_image_ids=[], status="processing", progress=5,
        image_resource_id=None, image_url=None, remote_task_id=None,
        frozen_points=10, point_deductions=[], failed_reason=None,
        is_published=False, publish_status="approved", published_at=None,
    )
    asset = SimpleNamespace(balance=90)
    user = SimpleNamespace(nickname="昵称", username="user", avatar=None)
    db = SimpleNamespace(
        get=AsyncMock(side_effect=lambda model, _id: user if model is aurakey_tasks.User else task),
        scalar=AsyncMock(return_value=asset), add=Mock(), commit=AsyncMock(),
    )
    session = AsyncMock()
    session.__aenter__.return_value = db
    monkeypatch.setattr(aurakey_tasks, "_get_session_maker", lambda: lambda: session)
    monkeypatch.setattr(aurakey_tasks, "_get_stream_image_timeout", lambda: 600.0)
    generate = AsyncMock(return_value={"b64_json": "cG5nLWJ5dGVz", "mime_type": "image/png"})
    monkeypatch.setattr(aurakey_tasks, "generate_image_generation", generate)
    resource = SimpleNamespace(id=uuid.uuid4())

    async def upload(**kwargs):
        # 存储函数内部会提交事务，此时任务还不能提前变为成功。
        assert task.status == "processing"
        assert kwargs["file_bytes"] == b"png-bytes"
        return resource

    upload_mock = AsyncMock(side_effect=upload)
    monkeypatch.setattr(aurakey_tasks.StorageService, "upload_file_bytes", upload_mock)

    async def restore(_db, target_asset, _allocation, fallback_amount, *, description):
        target_asset.balance += fallback_amount
        return fallback_amount

    restore_mock = AsyncMock(side_effect=restore)
    monkeypatch.setattr(AurakeyService, "_restore_points", restore_mock)
    reference = SimpleNamespace(
        id=uuid.uuid4(), name="reference.png", type="image/png", url="https://cdn.example.com/reference.png",
    )
    references = AsyncMock(return_value={reference.id: reference})
    download = AsyncMock(return_value=(b"reference-bytes", "image/png", reference.name))
    monkeypatch.setattr(aurakey_tasks.StorageService, "get_resources_by_ids", references)
    monkeypatch.setattr(aurakey_tasks.StorageService, "_download_remote_file", download)
    return SimpleNamespace(
        db=db, task=task, asset=asset, resource=resource, generate=generate,
        upload=upload_mock, restore=restore_mock, reference=reference, references=references, download=download,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("with_reference", [False, True])
@pytest.mark.parametrize("is_public", [False, True])
async def test_image_worker_saves_result_before_success(image_worker_context, with_reference, is_public):
    ctx = image_worker_context
    if with_reference:
        ctx.task.reference_image_ids = [str(ctx.reference.id)]

    await aurakey_tasks._run_stream_image_task_async(str(ctx.task.id), is_public)

    ctx.generate.assert_awaited_once_with(
        prompt="生成头像, 图片比例为:1:1", model="gpt-image-2", n=1,
        response_format="b64_json", timeout=600.0,
        image=("reference.png", b"reference-bytes", "image/png") if with_reference else None,
    )
    assert ctx.task.status == "success"
    assert ctx.task.progress == 100
    assert ctx.task.image_resource_id == ctx.resource.id
    assert ctx.task.remote_task_id is None
    assert ctx.task.frozen_points == 0
    assert ctx.task.is_published is is_public
    assert ctx.asset.balance == 90
    ctx.upload.assert_awaited_once()
    ctx.restore.assert_not_awaited()
    if not with_reference:
        ctx.download.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["upstream", "timeout", "invalid_base64", "upload", "missing_reference", "non_image", "download"])
async def test_image_worker_failure_refunds_once(image_worker_context, failure):
    ctx = image_worker_context
    if failure == "upstream":
        ctx.generate.side_effect = RuntimeError("上游图片生成失败")
    elif failure == "timeout":
        ctx.generate.side_effect = httpx.ReadTimeout("timeout")
    elif failure == "invalid_base64":
        ctx.generate.return_value = {"b64_json": "???", "mime_type": "image/png"}
    elif failure == "upload":
        ctx.upload.side_effect = ValueError("存储失败")
    else:
        ctx.task.reference_image_ids = [str(ctx.reference.id)]
        if failure == "missing_reference":
            ctx.references.return_value = {}
        elif failure == "non_image":
            ctx.download.return_value = (b"html", "text/html", "error.html")
        else:
            ctx.download.side_effect = httpx.ConnectError("下载失败")

    await aurakey_tasks._run_stream_image_task_async(str(ctx.task.id))
    await aurakey_tasks._run_stream_image_task_async(str(ctx.task.id))

    assert ctx.task.status == "failed"
    assert ctx.task.failed_reason
    assert ctx.task.image_resource_id is None
    assert ctx.task.frozen_points == 0
    assert ctx.asset.balance == 100
    ctx.restore.assert_awaited_once()
    assert ctx.db.add.call_args.args[0].type == 3
    if failure == "timeout":
        assert "600 秒" in ctx.task.failed_reason
    if failure in {"missing_reference", "non_image", "download"}:
        ctx.generate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("use_stream", [False, True])
@pytest.mark.parametrize("queue_fails", [False, True])
async def test_image_submission_queues_worker_or_refunds(monkeypatch, use_stream, queue_fails):
    user_id, resource_id = uuid.uuid4(), uuid.uuid4()
    asset = SimpleNamespace(balance=100, is_vip=False)
    model = SimpleNamespace(cost=10, is_vip_only=False)
    tasks = []

    def add(item):
        if isinstance(item, aurakey_tasks.AurakeyTask):
            item.id = uuid.uuid4()
            tasks.append(item)

    db = SimpleNamespace(scalar=AsyncMock(side_effect=[model, asset]), add=Mock(side_effect=add), commit=AsyncMock(), refresh=AsyncMock())
    monkeypatch.setattr(AurakeyService, "get_or_create_user_asset", AsyncMock(return_value=asset))
    validate = AsyncMock(return_value={resource_id: SimpleNamespace(type="image/png")})
    monkeypatch.setattr(AurakeyService, "_validate_reference_images", validate)

    async def spend(_db, target_asset, cost, *, description):
        target_asset.balance -= cost
        return cost, []

    async def restore(_db, target_asset, _allocation, fallback_amount, *, description):
        target_asset.balance += fallback_amount
        return fallback_amount

    spend_mock = AsyncMock(side_effect=spend)
    restore_mock = AsyncMock(side_effect=restore)
    monkeypatch.setattr(AurakeyService, "_spend_points", spend_mock)
    monkeypatch.setattr(AurakeyService, "_restore_points", restore_mock)
    queue = Mock(side_effect=RuntimeError("broker unavailable") if queue_fails else None)
    monkeypatch.setattr(aurakey_tasks.run_stream_image_task, "delay", queue)
    request_type = TaskStreamGenerateRequest if use_stream else TaskGenerateRequest
    request = request_type(
        prompt="生成头像", model_name="gpt-image-2", aspect_ratio="1:1",
        reference_images_ids=[resource_id], **({"is_public": True} if use_stream else {}),
    )
    submit = AurakeyService.submit_stream_generate_task if use_stream else AurakeyService.submit_generate_task

    result = await submit(db, request, user_id)

    task = tasks[0]
    assert task.reference_image_ids == [str(resource_id)]
    assert task.remote_task_id is None
    assert result.task_id == task.id
    validate.assert_awaited_once_with(db, [resource_id])
    queue.assert_called_once_with(str(task.id), use_stream)
    spend_mock.assert_awaited_once()
    if queue_fails:
        assert task.status == "failed"
        assert task.frozen_points == result.frozen_points == 0
        assert result.balance_after == 100
        restore_mock.assert_awaited_once()
    else:
        assert task.status == "processing"
        assert result.frozen_points == 10
        assert result.balance_after == 90
        restore_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_image_generation_result_uploads_b64_bytes(monkeypatch):
    task_id = uuid.uuid4()
    user_id = uuid.uuid4()
    task = SimpleNamespace(id=task_id, user_id=user_id)
    uploaded = {}
    resource = SimpleNamespace(id=uuid.uuid4())

    async def upload_file_bytes(**kwargs):
        uploaded.update(kwargs)
        return resource

    monkeypatch.setattr(aurakey_tasks.StorageService, "upload_file_bytes", upload_file_bytes)

    result = await aurakey_tasks._upload_image_generation_result(
        None,
        task,
        {"b64_json": "cG5nLWJ5dGVz", "mime_type": "image/png"},
    )

    assert result is resource
    assert uploaded["file_bytes"] == b"png-bytes"
    assert uploaded["mime_type"] == "image/png"
    assert uploaded["owner_id"] == user_id
    assert uploaded["scope"] == "hope_aurakey"


@pytest.mark.asyncio
async def test_invite_info_returns_response_model_fields(monkeypatch):
    user_id = uuid.uuid4()
    asset = SimpleNamespace(
        invite_code="ABC123",
        invited_count=5,
        total_reward_points=250,
    )

    async def get_or_create_user_asset(_db, requested_user_id):
        assert requested_user_id == user_id
        return asset

    async def get_system_config(_db):
        return {"invite_reward_points": 50}

    monkeypatch.setattr(AurakeyService, "get_or_create_user_asset", get_or_create_user_asset)
    monkeypatch.setattr(AurakeyService, "get_system_config", get_system_config)

    response = await get_invite_info(current_user=SimpleNamespace(id=user_id), db=None)
    invite_info = InviteInfoResponse.model_validate(response.data)

    assert invite_info.invite_code == "ABC123"
    assert invite_info.invited_count == 5
    assert invite_info.total_reward_points == 250
    assert invite_info.rule_text == "每邀请1位新用户注册，双方各得 50 点算力"


def test_user_profile_response_includes_openid():
    response = UserProfileResponse(
        user_id=uuid.uuid4(),
        openid="oxxxxxxxxxxxxxxxxxxxxxx",
        balance=100,
    )

    assert response.openid == "oxxxxxxxxxxxxxxxxxxxxxx"


def test_product_item_includes_vip_fields():
    product = SimpleNamespace(
        id=uuid.uuid4(),
        type="vip",
        name="测试套餐",
        price=990,
        original_price=None,
        point_amount=10,
        bonus_amount=0,
        tag=None,
        vip_type="测试套餐",
        vip_level=2,
        valid_days=30,
    )

    item = ProductItem.model_validate(product)

    assert item.vip_type == "测试套餐"
    assert item.vip_level == 2
    assert item.valid_days == 30


def test_entitlement_response_fields():
    response = UserEntitlementResponse(
        vip_expire_time=1770000000,
        remaining_points=30,
        is_vip=True,
        vip_type="测试套餐",
        vip_level=3,
    )

    assert response.remaining_points == 30
    assert response.is_vip is True
    assert response.vip_type == "测试套餐"
    assert response.vip_level == 3


def test_system_config_merges_defaults_and_custom_values():
    config = merge_aurakey_config({"daily_sign_in_reward_points": 8, "custom": {"foo": "bar"}})
    response = AurakeySystemConfigResponse(**config)

    assert response.register_reward_points == 10
    assert response.daily_sign_in_reward_points == 8
    assert response.invite_reward_points == 50
    assert response.custom == {"foo": "bar"}


@pytest.mark.asyncio
async def test_task_status_response_preserves_optional_fields():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        status="failed",
        progress=100,
        remote_task_id=None,
        image_url="https://cdn.example.com/result.png",
        image_resource_id=None,
        reference_image_ids=[],
        failed_reason="上游 API 生图失败",
        frozen_points=0,
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

    result = await AurakeyService.get_task_status(FakeDb(), task_id, user_id)

    assert result.resource is None
    assert result.failed_reason == "上游 API 生图失败"


@pytest.mark.asyncio
async def test_task_progress_uses_upstream_progress():
    user_id = uuid.uuid4()
    task = SimpleNamespace(
        user_id=user_id,
        status="processing",
        progress=5,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=30),
    )

    progress = await AurakeyService.resolve_task_progress(
        SimpleNamespace(),
        task,
        upstream_progress="45",
        average_duration_seconds=120,
    )

    assert progress == 45
    assert task.progress == 45


@pytest.mark.asyncio
async def test_task_progress_simulates_from_recent_average_duration():
    user_id = uuid.uuid4()
    task = SimpleNamespace(
        user_id=user_id,
        status="processing",
        progress=5,
        created_at=datetime.now(timezone.utc) - timedelta(seconds=60),
    )

    progress = await AurakeyService.resolve_task_progress(
        SimpleNamespace(),
        task,
        average_duration_seconds=120,
    )

    assert 45 <= progress <= 55
    assert progress > 5


@pytest.mark.asyncio
async def test_task_progress_simulates_from_naive_local_created_at(monkeypatch):
    user_id = uuid.uuid4()
    now_utc = datetime(2026, 5, 18, 15, 0, 0, tzinfo=timezone.utc)
    task = SimpleNamespace(
        user_id=user_id,
        status="processing",
        progress=5,
        created_at=datetime(2026, 5, 18, 22, 59, 0),
    )
    monkeypatch.setattr(AurakeyService, "_now_utc", staticmethod(lambda: now_utc))

    progress = await AurakeyService.resolve_task_progress(
        SimpleNamespace(),
        task,
        average_duration_seconds=120,
    )

    assert 45 <= progress <= 55
    assert progress > 5


@pytest.mark.asyncio
async def test_average_task_duration_caps_outlier_duration():
    user_id = uuid.uuid4()
    created_at = datetime(2026, 5, 18, 10, 0, 0)
    updated_at = datetime(2026, 5, 18, 11, 0, 0)

    class FakeResult:
        def all(self):
            return [(created_at, updated_at)]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    duration = await AurakeyService._get_recent_average_task_duration_seconds(FakeDb(), user_id)

    assert duration == AurakeyService.MAX_TASK_DURATION_SECONDS


@pytest.mark.asyncio
async def test_user_history_resolves_progress_with_shared_logic(monkeypatch):
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        image_resource_id=None,
        reference_image_ids=[],
        prompt="生成一张猫图",
        status="processing",
        progress=5,
        cost=10,
        is_published=False,
        publish_status="approved",
        category_id=None,
        aspect_ratio="1:1",
        model_name="gpt-image-2",
        show_title="猫猫标题",
        template_prompt="猫猫模板",
    )

    class FakeRows:
        def scalars(self):
            return self

        def all(self):
            return [task]

    class FakeDb:
        commits = 0

        async def execute(self, _stmt):
            return FakeRows()

        async def scalar(self, _stmt):
            return 1

        async def commit(self):
            self.commits += 1

    async def get_resources_by_ids(_db, _ids):
        return {}

    async def get_reference_map(_db, _tasks):
        return {}

    async def get_average(_db, requested_user_id):
        assert requested_user_id == user_id
        return 120

    async def resolve_progress(_db, item, *, average_duration_seconds=None, upstream_progress=None):
        assert average_duration_seconds == 120
        assert upstream_progress is None
        item.progress = 66
        return item.progress

    monkeypatch.setattr(aurakey_router.StorageService, "get_resources_by_ids", get_resources_by_ids)
    monkeypatch.setattr(AurakeyService, "_get_task_reference_image_map", get_reference_map)
    monkeypatch.setattr(AurakeyService, "_get_recent_average_task_duration_seconds", get_average)
    monkeypatch.setattr(AurakeyService, "resolve_task_progress", resolve_progress)

    response = await aurakey_router.get_user_history(
        current_user=SimpleNamespace(id=user_id),
        db=FakeDb(),
    )

    assert response.data.items[0]["task_id"] == task_id
    assert response.data.items[0]["progress"] == 66


@pytest.mark.asyncio
async def test_publish_task_to_gallery_updates_task_flags():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        is_published=False,
        publish_status="approved",
        published_at=None,
        image_url="https://cdn.example.com/result.png",
        image_resource_id=uuid.uuid4(),
        prompt="生成一张猫图",
        model_name="gpt-image-2",
        aspect_ratio="1:1",
    )

    class FakeDb:
        pass

    await AurakeyService.publish_task_to_gallery(FakeDb(), task, "阿杰", "https://cdn.example.com/avatar.png")

    assert task.is_published is True
    assert task.publish_status == "approved"
    assert task.published_at is not None


@pytest.mark.asyncio
async def test_update_task_publish_state_updates_category_and_flag():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    category_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        is_deleted=False,
        status="success",
        is_published=False,
        publish_status="approved",
        category_id=None,
        published_at=None,
        image_url="https://cdn.example.com/result.png",
        image_resource_id=uuid.uuid4(),
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

        async def commit(self):
            pass

    result = await AurakeyService.update_task_publish_state(FakeDb(), task_id, user_id, True, category_id)

    assert task.is_published is True
    assert task.category_id == category_id
    assert result["is_published"] is True
    assert result["category_id"] == category_id


@pytest.mark.asyncio
async def test_admin_gallery_task_edit_updates_publish_and_display_fields():
    task_id = uuid.uuid4()
    category_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        is_deleted=False,
        status="success",
        image_resource_id=uuid.uuid4(),
        is_published=False,
        publish_status="approved",
        category_id=None,
        published_at=None,
        show_title=None,
        template_prompt=None,
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

        async def commit(self):
            pass

    result = await AurakeyService.update_gallery_task_by_admin(
        FakeDb(),
        task_id,
        {
            "is_published": True,
            "category_id": category_id,
            "show_title": "展示标题",
            "template_prompt": "模板提示词",
        },
    )

    assert task.is_published is True
    assert task.category_id == category_id
    assert task.show_title == "展示标题"
    assert task.template_prompt == "模板提示词"
    assert task.published_at is not None
    assert result["show_title"] == "展示标题"
    assert result["template_prompt"] == "模板提示词"


@pytest.mark.asyncio
async def test_update_task_publish_review_status_blocks_task_without_changing_publish_flag():
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        is_deleted=False,
        is_published=True,
        publish_status="approved",
        category_id=None,
        published_at=None,
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

        async def commit(self):
            pass

    result = await AurakeyService.update_task_publish_review_status(FakeDb(), task_id, "blocked")

    assert task.publish_status == "blocked"
    assert task.is_published is True
    assert result["publish_status"] == "blocked"
    assert result["is_published"] is True


@pytest.mark.asyncio
async def test_user_publish_state_can_change_when_review_status_is_blocked():
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        is_deleted=False,
        status="success",
        image_url="https://cdn.example.com/result.png",
        image_resource_id=uuid.uuid4(),
        is_published=False,
        publish_status="blocked",
        category_id=None,
        published_at=None,
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

        async def commit(self):
            pass

    result = await AurakeyService.update_task_publish_state(FakeDb(), task_id, user_id, True)

    assert task.is_published is True
    assert task.publish_status == "blocked"
    assert result["is_published"] is True
    assert result["publish_status"] == "blocked"


@pytest.mark.asyncio
async def test_admin_batch_publish_updates_valid_tasks_and_reports_failures(monkeypatch):
    valid_task_id = uuid.uuid4()
    failed_task_id = uuid.uuid4()
    missing_task_id = uuid.uuid4()
    valid_task = SimpleNamespace(
        id=valid_task_id,
        is_deleted=False,
        status="success",
        image_url="https://cdn.example.com/result.png",
        image_resource_id=uuid.uuid4(),
        is_published=False,
        publish_status="approved",
        category_id=None,
        published_at=None,
    )
    failed_task = SimpleNamespace(
        id=failed_task_id,
        is_deleted=False,
        status="processing",
        image_url=None,
        image_resource_id=None,
        is_published=False,
        publish_status="approved",
        category_id=None,
        published_at=None,
    )

    class FakeSelect:
        def where(self, *args, **kwargs):
            return self

    class FakeColumn:
        def in_(self, _items):
            return True

    class FakeTaskModel:
        id = FakeColumn()

    class ScalarRows:
        def scalars(self):
            return self

        def all(self):
            return [valid_task, failed_task]

    class FakeDb:
        async def execute(self, _stmt):
            return ScalarRows()

        async def commit(self):
            pass

    monkeypatch.setattr("apps.aurakey.services.select", lambda *args, **kwargs: FakeSelect())
    monkeypatch.setattr("apps.aurakey.services.AurakeyTask", FakeTaskModel)

    result = await AurakeyService.batch_update_task_publish_state_by_admin(
        FakeDb(),
        [valid_task_id, failed_task_id, missing_task_id],
        True,
    )

    assert valid_task.is_published is True
    assert valid_task.published_at is not None
    assert result["updated_count"] == 1
    assert result["failed_count"] == 2
    assert {item["task_id"] for item in result["failed_items"]} == {failed_task_id, missing_task_id}


@pytest.mark.asyncio
async def test_daily_sign_in_returns_response_model_fields(monkeypatch):
    user_id = uuid.uuid4()
    asset = SimpleNamespace(user_id=user_id, balance=0)

    async def get_or_create_user_asset(_db, requested_user_id):
        assert requested_user_id == user_id
        return asset

    async def get_system_config(_db):
        return {
            "daily_sign_in_reward_points": 12,
            "daily_free_points_reset_hour": 12,
        }

    async def credit_points(_db, requested_asset, amount, **kwargs):
        assert requested_asset is asset
        assert amount == 12
        requested_asset.balance += amount
        return None

    class FakeSelect:
        def where(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def limit(self, *args, **kwargs):
            return self

    def fake_select(*args, **kwargs):
        return FakeSelect()

    class FakeColumn:
        def __eq__(self, other):
            return True

        def __ge__(self, other):
            return True

        def __lt__(self, other):
            return True

    class FakeAssetLog:
        user_id = FakeColumn()
        type = FakeColumn()
        created_at = FakeColumn()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ScalarRows:
        def scalars(self):
            return self

        def all(self):
            return [datetime.now(timezone.utc)]

    class FakeDb:
        async def scalar(self, _stmt):
            return None

        def add(self, _item):
            pass

        async def commit(self):
            pass

        async def execute(self, _stmt):
            return ScalarRows()

    monkeypatch.setattr(AurakeyService, "get_or_create_user_asset", get_or_create_user_asset)
    monkeypatch.setattr(AurakeyService, "get_system_config", get_system_config)
    monkeypatch.setattr(AurakeyService, "_credit_points", credit_points)
    monkeypatch.setattr("apps.aurakey.services.select", fake_select)
    monkeypatch.setattr("apps.aurakey.services.desc", lambda value: value)
    monkeypatch.setattr("apps.aurakey.services.AurakeyAssetLog", FakeAssetLog)

    result = await AurakeyService.daily_sign_in(FakeDb(), user_id)

    assert result == {"reward_points": 12, "continuous_days": 1}


@pytest.mark.asyncio
async def test_wechat_notify_rejects_amount_mismatch(monkeypatch):
    order = SimpleNamespace(
        order_no="OD123",
        status="waiting",
        amount=990,
    )

    class FakeSelect:
        def where(self, *args, **kwargs):
            return self

    class FakeColumn:
        def __eq__(self, other):
            return True

    class FakeOrder:
        order_no = FakeColumn()

    class FakeDb:
        async def scalar(self, _stmt):
            return order

    monkeypatch.setattr("apps.aurakey.services.select", lambda *args, **kwargs: FakeSelect())
    monkeypatch.setattr("apps.aurakey.services.AurakeyOrder", FakeOrder)

    with pytest.raises(ValueError) as exc_info:
        await AurakeyService.handle_wechat_notify(FakeDb(), "OD123", True, 980, "wx001")

    assert "金额" in str(exc_info.value)
    assert order.status == "waiting"


@pytest.mark.asyncio
async def test_wechat_notify_vip_grants_points_and_vip_snapshot(monkeypatch):
    user_id = uuid.uuid4()
    product_id = uuid.uuid4()
    order_id = uuid.uuid4()
    product = SimpleNamespace(
        id=product_id,
        type="vip",
        name="测试套餐",
        price=990,
        point_amount=10,
        bonus_amount=0,
        tag="",
        vip_type="黄金会员",
        vip_level=2,
        valid_days=None,
        is_deleted=False,
    )
    order = SimpleNamespace(
        id=order_id,
        user_id=user_id,
        order_no="OD123",
        product_id=product_id,
        amount=990,
        status="waiting",
        paid_at=None,
        third_trade_no=None,
        entitlement_start_at=None,
        entitlement_expire_at=None,
        product_name=None,
        product_type=None,
        vip_type=None,
        vip_level=0,
        point_amount=0,
        bonus_amount=0,
        valid_days=None,
        granted_points=0,
    )
    asset = SimpleNamespace(
        user_id=user_id,
        balance=0,
        is_vip=False,
        vip_type=None,
        vip_expire_time=None,
    )
    added_items = []

    class FakeSelect:
        def where(self, *args, **kwargs):
            return self

    class FakeColumn:
        def __eq__(self, other):
            return True

    class FakeOrder:
        order_no = FakeColumn()

    class FakeAssetLog:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDb:
        async def scalar(self, _stmt):
            return order

        async def get(self, _model, requested_id):
            assert requested_id == product_id
            return product

        def add(self, item):
            added_items.append(item)

        async def commit(self):
            pass

    async def get_or_create_user_asset(_db, requested_user_id):
        assert requested_user_id == user_id
        return asset

    async def get_system_config(_db):
        return {"default_vip_valid_days": 30, "default_point_pack_valid_days": None}

    async def credit_points(_db, requested_asset, amount, **kwargs):
        assert requested_asset is asset
        requested_asset.balance += amount
        return None

    monkeypatch.setattr("apps.aurakey.services.select", lambda *args, **kwargs: FakeSelect())
    monkeypatch.setattr("apps.aurakey.services.AurakeyOrder", FakeOrder)
    monkeypatch.setattr("apps.aurakey.services.AurakeyAssetLog", FakeAssetLog)
    monkeypatch.setattr(AurakeyService, "get_or_create_user_asset", get_or_create_user_asset)
    monkeypatch.setattr(AurakeyService, "get_system_config", get_system_config)
    monkeypatch.setattr(AurakeyService, "_credit_points", credit_points)

    await AurakeyService.handle_wechat_notify(FakeDb(), "OD123", True, 990, "wx001")

    assert order.status == "success"
    assert order.product_type == "vip"
    assert order.vip_type == "黄金会员"
    assert order.vip_level == 2
    assert order.granted_points == 10
    assert order.third_trade_no == "wx001"
    assert asset.balance == 10
    assert asset.is_vip is True
    assert asset.vip_type == "黄金会员"
    assert asset.vip_expire_time is not None
    assert order.entitlement_expire_at == asset.vip_expire_time
    assert len(added_items) == 1
    assert added_items[0].amount == 10
