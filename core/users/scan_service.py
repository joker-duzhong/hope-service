import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from core.apps_config import AppConfig, REGISTERED_APPS
from core.auth_scope import validate_scope
from core.redis_client import redis_client
from core.security import create_token_pair
from core.users.models import User
from core.users.scan_schemas import (
    ScanAppResponse, ScanCreateResponse, ScanPollResponse,
    ScanStateResponse, ScanStatus, StoredScanSession,
)
from core.users.schemas import LoginResponse
from core.users.services import UserService

logger = logging.getLogger(__name__)

SESSION_TTL_SECONDS = 300
POLL_INTERVAL_SECONDS = 2
CREATE_LIMIT_PER_MINUTE = 10
REQUEST_LIMIT_PER_MINUTE = 180
POLL_LIMIT_PER_MINUTE = 40


async def _redis_call(method: str, *args, **kwargs):
    try:
        return await getattr(redis_client, method)(*args, **kwargs)
    except RedisError:
        logger.warning("Scan login Redis operation failed")
        raise HTTPException(503, "扫码登录服务暂不可用，请稍后重试") from None


def _app(app_key: str) -> AppConfig:
    app = REGISTERED_APPS.get(app_key)
    if app is None or not app.is_active:
        raise HTTPException(400, "应用不存在或已停用")
    return app


def available_apps() -> list[ScanAppResponse]:
    return [
        ScanAppResponse(app_key=app.key, name=app.name)
        for app in REGISTERED_APPS.values() if app.is_active
    ]


async def rate_limit(scope: str, subject: str, limit: int) -> None:
    subject_hash = hashlib.sha256(subject.encode()).hexdigest()
    count = await _redis_call(
        "eval",
        """
        local count = redis.call('INCR', KEYS[1])
        if count == 1 then redis.call('EXPIRE', KEYS[1], 60) end
        return count
        """,
        1, f"auth:scan:rate:{scope}:{subject_hash}",
    )
    if count > limit:
        raise HTTPException(429, "扫码请求过于频繁，请稍后重试", headers={"Retry-After": "60"})


def _key(transaction_id: UUID) -> str:
    return f"auth:scan:session:{transaction_id}"


def _state(session: StoredScanSession) -> ScanStateResponse:
    app = _app(session.app_key)
    return ScanStateResponse(
        transaction_id=session.transaction_id, status=session.status,
        app=ScanAppResponse(app_key=app.key, name=app.name), expires_at=session.expires_at,
    )


async def _load(transaction_id: UUID) -> tuple[str, StoredScanSession] | None:
    raw = await _redis_call("get", _key(transaction_id))
    if raw is None:
        return None
    try:
        session = StoredScanSession.model_validate_json(raw)
        if session.transaction_id != transaction_id or session.expires_at.tzinfo is None:
            raise ValueError("invalid session")
    except (ValidationError, ValueError):
        logger.warning("Invalid scan login session data")
        raise HTTPException(503, "扫码会话异常，请重新生成二维码") from None
    if session.expires_at <= datetime.now(timezone.utc):
        return None
    return raw, session


def _check_poll_token(session: StoredScanSession, poll_token: str) -> None:
    digest = hashlib.sha256(poll_token.encode()).hexdigest()
    if not hmac.compare_digest(session.poll_token_hash, digest):
        raise HTTPException(403, "扫码登录发起端凭证不匹配")


async def _replace(raw: str, session: StoredScanSession) -> bool:
    return bool(await _redis_call(
        "eval",
        """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
        local ttl = redis.call('PTTL', KEYS[1])
        if ttl <= 0 then return 0 end
        redis.call('SET', KEYS[1], ARGV[2], 'PX', ttl)
        return 1
        """,
        1, _key(session.transaction_id), raw, session.model_dump_json(),
    ))


def _check_user(user: User, app_key: str, require_phone: bool = True) -> None:
    _app(app_key)
    UserService.ensure_login_allowed(user)
    validate_scope(app_key, user)
    if require_phone and not user.phone:
        raise HTTPException(403, "请先绑定手机号，再确认扫码登录")


async def create_session(app_key: str) -> ScanCreateResponse:
    _app(app_key)
    poll_token = secrets.token_urlsafe(32)
    session = StoredScanSession(
        transaction_id=uuid4(), app_key=app_key,
        poll_token_hash=hashlib.sha256(poll_token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=SESSION_TTL_SECONDS),
    )
    created = await _redis_call(
        "set", _key(session.transaction_id), session.model_dump_json(),
        ex=SESSION_TTL_SECONDS, nx=True,
    )
    if not created:
        raise HTTPException(503, "创建扫码会话失败，请重试")
    return ScanCreateResponse(
        **_state(session).model_dump(), poll_token=poll_token,
        poll_interval_seconds=POLL_INTERVAL_SECONDS,
    )


async def session_info(transaction_id: UUID) -> ScanStateResponse:
    loaded = await _load(transaction_id)
    if loaded is None:
        return ScanStateResponse(transaction_id=transaction_id, status=ScanStatus.EXPIRED)
    return _state(loaded[1])


async def poll_session(transaction_id: UUID, poll_token: str) -> ScanPollResponse:
    loaded = await _load(transaction_id)
    if loaded is None:
        return ScanPollResponse(transaction_id=transaction_id, status=ScanStatus.EXPIRED)
    session = loaded[1]
    _check_poll_token(session, poll_token)
    await rate_limit("poll", str(transaction_id), POLL_LIMIT_PER_MINUTE)
    return ScanPollResponse(
        **_state(session).model_dump(),
        exchange_code=session.exchange_code if session.status == ScanStatus.CONFIRMED else None,
    )


async def transition(
    transaction_id: UUID, action: str, user: User | None = None,
) -> ScanStateResponse:
    if action not in {"scanned", "confirm", "cancel"}:
        raise ValueError("unsupported scan transition")
    for attempt in range(3):
        loaded = await _load(transaction_id)
        if loaded is None:
            raise HTTPException(410, "二维码已过期，请重新生成")
        raw, session = loaded
        _app(session.app_key)
        if action == "scanned":
            if session.status != ScanStatus.WAITING_SCAN:
                return _state(session)
            session.status = ScanStatus.PENDING
        else:
            if user is None:
                raise HTTPException(401, "请先在手机端登录")
            _check_user(user, session.app_key, require_phone=action == "confirm")
            if action == "confirm":
                if session.status == ScanStatus.CONFIRMED and session.user_id == user.id:
                    if session.token_version != user.token_version:
                        raise HTTPException(409, "登录凭据已变更，请重新扫码")
                    return _state(session)
                if session.status != ScanStatus.PENDING:
                    raise HTTPException(409, "当前扫码状态不允许确认登录")
                session.status = ScanStatus.CONFIRMED
                session.user_id = user.id
                session.token_version = user.token_version
                session.exchange_code = secrets.token_urlsafe(32)
            else:
                if session.status == ScanStatus.CANCELLED:
                    return _state(session)
                if session.status not in {ScanStatus.WAITING_SCAN, ScanStatus.PENDING}:
                    raise HTTPException(409, "当前扫码状态不允许取消")
                session.status = ScanStatus.CANCELLED
        if await _replace(raw, session):
            return _state(session)
    raise HTTPException(409, "扫码状态已变化，请重新查询")


async def exchange_session(
    transaction_id: UUID, exchange_code: str, poll_token: str, db: AsyncSession,
) -> LoginResponse:
    loaded = await _load(transaction_id)
    if loaded is None:
        raise HTTPException(410, "二维码已过期，请重新生成")
    raw, session = loaded
    _check_poll_token(session, poll_token)
    _app(session.app_key)
    if session.status != ScanStatus.CONFIRMED:
        raise HTTPException(409, "扫码尚未确认或兑换码已失效")
    if not session.exchange_code or not hmac.compare_digest(session.exchange_code, exchange_code):
        raise HTTPException(403, "扫码兑换码无效")
    if session.user_id is None:
        raise HTTPException(409, "扫码会话缺少授权用户")
    user = await UserService.get_by_id(db, session.user_id)
    if user is None:
        raise HTTPException(401, "授权账号已不可用")
    _check_user(user, session.app_key)
    if session.token_version != user.token_version:
        raise HTTPException(401, "授权登录已失效，请重新扫码")
    profile = await UserService.build_scoped_user_response(db, user, session.app_key)
    if session.expires_at <= datetime.now(timezone.utc):
        raise HTTPException(410, "二维码已过期，请重新生成")
    session.status = ScanStatus.CONSUMED
    session.exchange_code = None
    if not await _replace(raw, session):
        raise HTTPException(409, "扫码兑换码已被使用或过期，请重新查询")
    try:
        access_token, refresh_token = await create_token_pair(user.id, user.token_version, session.app_key)
    except RedisError:
        logger.warning("Scan login token issuance failed")
        raise HTTPException(503, "签发登录凭据失败，请重新扫码") from None
    return LoginResponse(access_token=access_token, refresh_token=refresh_token, user=profile, app_scope=session.app_key)
