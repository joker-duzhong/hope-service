import asyncio
import json
from types import SimpleNamespace

import pytest
from fakeredis.aioredis import FakeRedis
from pydantic import ValidationError
from redis.exceptions import ConnectionError as RedisConnectionError

from core import sms
from core.config import Settings


@pytest.fixture(scope="session", autouse=True)
async def setup_db():
    yield


class FakeSmsClient:
    def __init__(self):
        self.send_requests = []
        self.check_requests = []
        self.send_code = "OK"
        self.send_success = True
        self.verify_result = "PASS"
        self.error = None
        self.check_hook = None

    async def send_sms_verify_code_with_options_async(self, request, runtime):
        self.send_requests.append(request)
        assert runtime.autoretry is False
        if self.error:
            raise self.error
        return SimpleNamespace(body=SimpleNamespace(code=self.send_code, success=self.send_success))

    async def check_sms_verify_code_with_options_async(self, request, runtime):
        self.check_requests.append(request)
        await asyncio.sleep(0)
        if self.error:
            raise self.error
        if self.check_hook:
            await self.check_hook()
        return SimpleNamespace(body=SimpleNamespace(
            code="OK", success=True,
            model=SimpleNamespace(verify_result=self.verify_result, out_id=request.out_id),
        ))


class FakeProviderError(Exception):
    code = "InvalidParameter"
    message = "invalid template"
    data = {"Recommend": "request-id"}


@pytest.fixture
async def configured_sms(monkeypatch):
    monkeypatch.setattr(sms.settings, "ALIYUN_SMS_SIGN_NAME", "TestSign")
    monkeypatch.setattr(sms.settings, "ALIYUN_SMS_TEMPLATE_CODE", "100002")
    redis = FakeRedis(decode_responses=True)
    client = FakeSmsClient()
    monkeypatch.setattr(sms, "redis_client", redis)
    monkeypatch.setattr(sms, "_create_client", lambda: client)
    yield redis, client
    await redis.aclose()


async def test_send_uses_four_digit_code_and_keeps_only_session(configured_sms):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000") is True
    request = client.send_requests[0]
    assert request.template_code == "100002"
    template_param = json.loads(request.template_param)
    assert template_param["code"].isdigit()
    assert len(template_param["code"]) == 4
    assert template_param["min"] == "5"
    assert request.valid_time == 300
    assert request.code_length == 4
    assert request.duplicate_policy == 1
    session = json.loads(await redis.get("sms:session:13800138000"))
    assert session["code"] != template_param["code"]
    assert len(session["code"]) == 64


@pytest.mark.parametrize("code, success", [("Rejected", True), ("OK", False)])
async def test_rejected_send_never_creates_session(configured_sms, code, success):
    redis, client = configured_sms
    client.send_code = code
    client.send_success = success
    assert await sms.send_sms_code("13800138000") is False
    assert await redis.get("sms:session:13800138000") is None


async def test_concurrent_sends_are_limited(configured_sms):
    _, client = configured_sms
    results = await asyncio.gather(*(sms.send_sms_code("13800138000") for index in range(8)))
    assert results.count(True) == 1
    assert len(client.send_requests) == 1


async def test_daily_quota_survives_cooldown_expiry(configured_sms):
    redis, client = configured_sms
    for index in range(5):
        assert await sms.send_sms_code("13800138000") is True
        await redis.delete("sms:cooldown:13800138000")
    assert await sms.send_sms_code("13800138000") is False
    assert len(client.send_requests) == 5
    assert await redis.ttl("sms:daily:13800138000") > 0


async def test_verify_consumes_once(configured_sms):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000")
    code = json.loads(client.send_requests[0].template_param)["code"]
    assert await sms.verify_sms_code("13800138000", code) is True
    assert await sms.verify_sms_code("13800138000", code) is False


async def test_verification_attempt_limit(configured_sms):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000")
    actual_code = json.loads(client.send_requests[0].template_param)["code"]
    wrong_code = "0000" if actual_code != "0000" else "0001"
    for index in range(6):
        assert await sms.verify_sms_code("13800138000", wrong_code) is False
    assert await redis.get("sms:session:13800138000") is None


async def test_concurrent_verifications_only_succeed_once(configured_sms):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000")
    code = json.loads(client.send_requests[0].template_param)["code"]
    results = await asyncio.gather(*(sms.verify_sms_code("13800138000", code) for index in range(4)))
    assert results.count(True) == 1


async def test_send_timeout_is_not_retried_or_logged_with_sensitive_details(configured_sms, caplog):
    redis, client = configured_sms
    client.error = TimeoutError("sensitive-test-marker")
    assert await sms.send_sms_code("13800138000") is False
    assert len(client.send_requests) == 1
    assert await redis.get("sms:session:13800138000") is None
    assert "sensitive-test-marker" not in caplog.text
    assert "13800138000" not in caplog.text


async def test_provider_error_logs_diagnostic_fields_without_phone(configured_sms, caplog):
    _, client = configured_sms
    client.error = FakeProviderError()
    assert await sms.send_sms_code("13800138000") is False
    assert "InvalidParameter" in caplog.text
    assert "invalid template" in caplog.text
    assert "13800138000" not in caplog.text


async def test_redis_failure_prevents_external_send(configured_sms, monkeypatch):
    redis, client = configured_sms

    async def fail(*args):
        raise RedisConnectionError()

    monkeypatch.setattr(redis, "eval", fail)
    assert await sms.send_sms_code("13800138000") is False
    assert client.send_requests == []


async def test_expired_session_never_calls_provider(configured_sms):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000")
    await redis.expire("sms:session:13800138000", 0)
    assert await sms.verify_sms_code("13800138000", "1234") is False


async def test_verification_redis_failure_does_not_accept_code_or_log_details(configured_sms, caplog, monkeypatch):
    redis, client = configured_sms
    assert await sms.send_sms_code("13800138000")
    async def fail(*args):
        raise RedisConnectionError("sensitive-test-marker")

    monkeypatch.setattr(redis, "eval", fail)
    assert await sms.verify_sms_code("13800138000", "1234") is False
    assert await redis.get("sms:session:13800138000") is not None
    assert "sensitive-test-marker" not in caplog.text
    assert "1234" not in caplog.text


@pytest.mark.parametrize("code", [("123"), ("abcdef")])
async def test_invalid_verification_never_calls_provider(configured_sms, code):
    _, client = configured_sms
    assert await sms.verify_sms_code("13800138000", code) is False


def test_sms_configuration_is_environment_based():
    settings = Settings(_env_file=None)
    assert settings.ALIBABA_CLOUD_ACCESS_KEY_ID == ""
    assert settings.ALIBABA_CLOUD_ACCESS_KEY_SECRET == ""


async def test_resend_resets_attempt_budget(configured_sms, monkeypatch):
    redis, client = configured_sms
    monkeypatch.setattr(sms.secrets, "randbelow", lambda maximum: 123)
    assert await sms.send_sms_code("13800138000")
    first = json.loads(await redis.get("sms:session:13800138000"))
    for attempt in range(5):
        assert await sms.verify_sms_code("13800138000", "9999") is False
    assert await redis.get("sms:session:13800138000") is None
    await redis.delete("sms:cooldown:13800138000")
    assert await sms.send_sms_code("13800138000")
    second = json.loads(await redis.get("sms:session:13800138000"))
    assert first["session_id"] != second["session_id"]
    assert await sms.verify_sms_code("13800138000", "0123") is True


async def test_old_verification_cannot_consume_identical_code_in_new_session(configured_sms, monkeypatch):
    redis, client = configured_sms
    monkeypatch.setattr(sms.secrets, "randbelow", lambda maximum: 123)
    assert await sms.send_sms_code("13800138000")
    consume = sms._consume_session
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_consume(cache_key, session_data):
        started.set()
        await release.wait()
        return await consume(cache_key, session_data)

    monkeypatch.setattr(sms, "_consume_session", delayed_consume)
    task = asyncio.create_task(sms.verify_sms_code("13800138000", "0123"))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        await redis.delete("sms:cooldown:13800138000")
        assert await sms.send_sms_code("13800138000")
    finally:
        release.set()
        result = await asyncio.wait_for(task, timeout=2)
    assert result is False
    assert await sms.verify_sms_code("13800138000", "0123") is True


async def test_last_allowed_attempt_can_succeed(configured_sms, monkeypatch):
    monkeypatch.setattr(sms.secrets, "randbelow", lambda maximum: 123)
    assert await sms.send_sms_code("13800138000")
    for attempt in range(4):
        assert await sms.verify_sms_code("13800138000", "9999") is False
    assert await sms.verify_sms_code("13800138000", "0123") is True


async def test_phone_prefixes_share_cooldown_and_session(configured_sms, monkeypatch):
    redis, client = configured_sms
    monkeypatch.setattr(sms.secrets, "randbelow", lambda maximum: 123)
    assert await sms.send_sms_code("+8613800138000")
    assert await sms.send_sms_code("13800138000") is False
    assert client.send_requests[0].phone_number == "13800138000"
    assert await sms.verify_sms_code("008613800138000", "0123") is True


@pytest.mark.parametrize("phone", ["", "123", "+12025550123", "１３８００１３８０００"])
async def test_invalid_phone_never_calls_sms_provider(configured_sms, phone):
    redis, client = configured_sms
    assert await sms.send_sms_code(phone) is False
    assert client.send_requests == []


async def test_legacy_session_cannot_bypass_new_attempt_limits(configured_sms):
    redis, client = configured_sms
    await redis.setex("sms:session:13800138000", 300, json.dumps({"code": "old-digest"}))
    assert await sms.verify_sms_code("13800138000", "0123") is False
