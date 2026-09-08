import json
import hashlib
import hmac
import logging
import secrets

from alibabacloud_dypnsapi20170525 import models as dypnsapi_models
from alibabacloud_dypnsapi20170525.client import Client as DypnsapiClient
from alibabacloud_tea_openapi import models as open_api_models
from alibabacloud_tea_util.models import RuntimeOptions

from core.config import settings
from core.redis_client import redis_client

logger = logging.getLogger(__name__)

CODE_TTL_SECONDS = 300
PHONE_COOLDOWN_SECONDS = 60
PHONE_DAILY_SEND_LIMIT = 5
CODE_VERIFY_ATTEMPT_LIMIT = 5


def _provider_error_details(error: Exception) -> str:
    code = getattr(error, "code", None)
    message = getattr(error, "message", None)
    data = getattr(error, "data", None)
    recommend = data.get("Recommend") if isinstance(data, dict) else None
    details = [str(value) for value in (code, message) if value]
    if recommend:
        details.append(f"recommendation={recommend}")
    return "; ".join(details) or type(error).__name__


def _get_template_code() -> str:
    return settings.ALIYUN_SMS_TEMPLATE_CODE


def _create_client() -> DypnsapiClient:
    return DypnsapiClient(open_api_models.Config(
        access_key_id=settings.ALIBABA_CLOUD_ACCESS_KEY_ID,
        access_key_secret=settings.ALIBABA_CLOUD_ACCESS_KEY_SECRET,
        endpoint="dypnsapi.aliyuncs.com",
    ))


def _runtime_options() -> RuntimeOptions:
    return RuntimeOptions(autoretry=False, connect_timeout=3000, read_timeout=10000)


async def send_sms_code(phone: str) -> bool:
    """发送四位短信验证码，并保存本地一次性验证会话。"""
    template_code = _get_template_code()
    if not template_code or not settings.ALIYUN_SMS_SIGN_NAME:
        logger.warning("SMS configuration is unavailable")
        return False

    try:
        allowed = await redis_client.eval(
            """
            if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
            local count = tonumber(redis.call('GET', KEYS[2]) or '0')
            if count >= tonumber(ARGV[2]) then return 0 end
            redis.call('SET', KEYS[1], '1', 'EX', ARGV[1])
            count = redis.call('INCR', KEYS[2])
            if count == 1 then redis.call('EXPIRE', KEYS[2], 86400) end
            return 1
            """,
            2,
            f"sms:cooldown:{phone}",
            f"sms:daily:{phone}",
            PHONE_COOLDOWN_SECONDS,
            PHONE_DAILY_SEND_LIMIT,
        )
        if not allowed:
            return False

        code = f"{secrets.randbelow(10000):04d}"
        request = dypnsapi_models.SendSmsVerifyCodeRequest(
            sign_name=settings.ALIYUN_SMS_SIGN_NAME,
            template_code=template_code,
            phone_number=phone,
            template_param=json.dumps({"code": code, "min": "5"}),
            country_code="86",
            code_length=4,
            code_type=1,
            valid_time=CODE_TTL_SECONDS,
            interval=PHONE_COOLDOWN_SECONDS,
            duplicate_policy=1,
        )
        response = await _create_client().send_sms_verify_code_with_options_async(
            request, _runtime_options()
        )
        body = response.body
        if not body or body.code != "OK" or body.success is not True:
            logger.warning("Dypnsapi rejected SMS send request")
            return False

        await redis_client.setex(
            f"sms:session:{phone}",
            CODE_TTL_SECONDS,
            json.dumps({
                "code": hmac.new(
                    settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256
                ).hexdigest(),
            }),
        )
        return True
    except Exception as error:
        logger.warning("SMS send failed: %s", _provider_error_details(error))
        return False


async def _consume_session(cache_key: str, session_data: str) -> bool:
    return bool(await redis_client.eval(
        """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
        return redis.call('DEL', KEYS[1])
        """,
        1,
        cache_key,
        session_data,
    ))


async def verify_sms_code(phone: str, code: str) -> bool:
    """校验短信验证码，本地会话只能成功消费一次。"""
    if not code.isdigit() or len(code) != 4:
        return False

    cache_key = f"sms:session:{phone}"
    try:
        session_data = await redis_client.get(cache_key)
        if not session_data:
            return False
        session = json.loads(session_data)
        if not isinstance(session, dict):
            return False
        attempts = await redis_client.eval(
            """
            local count = redis.call('INCR', KEYS[1])
            if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
            return count
            """,
            1,
            f"sms:attempts:{phone}",
            CODE_TTL_SECONDS,
        )
        if attempts > CODE_VERIFY_ATTEMPT_LIMIT:
            await _consume_session(cache_key, session_data)
            return False

        expected = hmac.new(
            settings.SECRET_KEY.encode(), code.encode(), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(session.get("code", ""), expected):
            return False
        return await _consume_session(cache_key, session_data)
    except Exception as error:
        logger.warning("SMS verification failed (%s)", type(error).__name__)
        return False
