import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.aurakey.router import get_invite_info
from apps.aurakey import router as aurakey_router
from apps.aurakey import tasks as aurakey_tasks
from apps.aurakey.config import merge_aurakey_config
from apps.aurakey.schemas import (
    AssetLogItem,
    AurakeySystemConfigResponse,
    InviteInfoResponse,
    ProductItem,
    TaskStreamGenerateRequest,
    UserEntitlementResponse,
    UserProfileResponse,
)
from apps.aurakey.services import AurakeyService
from apps.ai_gateway.schemas import ImageStreamChatRequest
from core.llm.engine import extract_image_result_from_content


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
async def test_stream_image_user_content_converts_reference_images_to_base64(monkeypatch):
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

    content = await aurakey_tasks._build_stream_image_user_content(None, task)

    assert content == [
        {"type": "text", "text": "基于参考图生成头像,aspect_ratio:1:1"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,cG5nLWJ5dGVz"}},
    ]


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
