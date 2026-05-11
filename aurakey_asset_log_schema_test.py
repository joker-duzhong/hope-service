import subprocess
import sys
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from apps.aurakey.router import get_invite_info
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
        failed_reason="上游 API 生图失败",
        frozen_points=0,
    )

    class FakeDb:
        async def get(self, _model, requested_task_id):
            assert requested_task_id == task_id
            return task

    result = await AurakeyService.get_task_status(FakeDb(), task_id, user_id)

    assert result.image_url == "https://cdn.example.com/result.png"
    assert result.failed_reason == "上游 API 生图失败"


@pytest.mark.asyncio
async def test_publish_task_to_gallery_creates_gallery(monkeypatch):
    user_id = uuid.uuid4()
    task_id = uuid.uuid4()
    task = SimpleNamespace(
        id=task_id,
        user_id=user_id,
        is_published=False,
        image_url="https://cdn.example.com/result.png",
        prompt="生成一张猫图",
        model_name="gpt-image-2",
        aspect_ratio="1:1",
    )
    added_items = []

    class FakeSelect:
        def where(self, *args, **kwargs):
            return self

    class FakeColumn:
        def __eq__(self, other):
            return True

    class FakeGallery:
        task_id = FakeColumn()
        is_deleted = FakeColumn()

        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class FakeDb:
        async def scalar(self, _stmt):
            return None

        def add(self, item):
            added_items.append(item)

    monkeypatch.setattr("apps.aurakey.services.select", lambda *args, **kwargs: FakeSelect())
    monkeypatch.setattr("apps.aurakey.services.AurakeyGallery", FakeGallery)

    await AurakeyService.publish_task_to_gallery(FakeDb(), task, "阿杰", "https://cdn.example.com/avatar.png")

    assert task.is_published is True
    assert len(added_items) == 1
    gallery = added_items[0]
    assert gallery.user_id == user_id
    assert gallery.task_id == task_id
    assert gallery.author_nickname == "阿杰"
    assert gallery.author_avatar == "https://cdn.example.com/avatar.png"
    assert gallery.image_url == "https://cdn.example.com/result.png"


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
