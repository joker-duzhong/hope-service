import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth_scope import PASSPORT_SCOPE, validate_scope
from core.config import settings
from core.redis_client import redis_client
from core.sms import normalize_phone, verify_sms_code
from core.users import scan_service
from core.users.identity_schemas import (
    AuthenticatedIdentityResponse, H5IdentityRequest, IdentityRequest,
    PendingIdentityResponse, StoredIdentity,
)
from core.users.models import User, UserIdentity
from core.users.services import UserService
from core.wechat.services import WeChatService

TICKET_TTL_SECONDS = 600


async def _redis(method: str, *args, **kwargs):
    try:
        return await getattr(redis_client, method)(*args, **kwargs)
    except RedisError:
        raise HTTPException(503, "身份验证服务暂不可用，请稍后重新发起登录") from None


def _ticket_key(ticket: str) -> str:
    return "auth:identity:ticket:" + hashlib.sha256(ticket.encode()).hexdigest()


def resolve_identity_scope(channel: str, appid: str) -> str:
    if not settings.get_wechat_config(appid):
        raise HTTPException(400, "当前微信应用尚未配置")
    if channel == "h5":
        if settings.PASSPORT_WECHAT_APP_IDS and appid not in settings.PASSPORT_WECHAT_APP_IDS:
            raise HTTPException(403, "该公众号未登记为授权中心登录入口")
        if appid in settings.MINIAPP_APP_SCOPES:
            raise HTTPException(403, "微信应用渠道配置冲突")
        return PASSPORT_SCOPE
    scope = settings.MINIAPP_APP_SCOPES.get(appid)
    if not scope or scope == PASSPORT_SCOPE:
        raise HTTPException(403, "该小程序尚未绑定业务应用")
    validate_scope(scope)
    if appid in settings.PASSPORT_WECHAT_APP_IDS:
        raise HTTPException(403, "微信应用渠道配置冲突")
    return scope


def validate_oauth_target(appid: str, redirect_uri: str) -> None:
    resolve_identity_scope("h5", appid)
    target = urlparse(redirect_uri)
    origin = f"{target.scheme}://{target.netloc}"
    allow_http = settings.ENVIRONMENT.strip().lower() in {"development", "dev", "local"}
    allowed_scheme = target.scheme == "https" or (allow_http and target.scheme == "http")
    if not allowed_scheme or not target.hostname or target.username or target.password or target.fragment or \
        target.path != "/wechat/callback" or \
        (settings.PASSPORT_CALLBACK_ORIGINS and origin not in settings.PASSPORT_CALLBACK_ORIGINS):
        raise HTTPException(400, "微信回调地址无效、未登记或协议不允许；HTTP 仅限开发环境")


async def _exchange_code(channel: str, appid: str, code: str) -> dict:
    fingerprint = hashlib.sha256(f"{channel}:{appid}:{code}".encode()).hexdigest()
    if not await _redis("set", "auth:identity:code:" + fingerprint, "used", nx=True, ex=TICKET_TTL_SECONDS):
        raise HTTPException(409, "本次微信 code 已处理，请重新获取 code 登录")
    exchange = WeChatService.exchange_h5_code_for_openid if channel == "h5" else WeChatService.exchange_miniapp_code_for_openid
    try:
        identity = await exchange(appid, code)
        if not isinstance(identity, dict) or not isinstance(identity.get("openid"), str) or not 1 <= len(identity["openid"]) <= 128:
            raise ValueError()
        if identity.get("unionid") is not None and (not isinstance(identity["unionid"], str) or not 1 <= len(identity["unionid"]) <= 64):
            raise ValueError()
        return identity
    except HTTPException:
        raise HTTPException(400, "微信身份验证未通过，请重新获取 code 登录") from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(502, "微信身份验证失败，请重新发起登录") from None


async def _authenticated(db: AsyncSession, user: User, scope: str) -> AuthenticatedIdentityResponse:
    try:
        session = await UserService.build_login_response(db, user, scope)
    except RedisError:
        raise HTTPException(503, "登录凭据签发暂不可用，请重新获取微信 code 登录") from None
    return AuthenticatedIdentityResponse(**session.model_dump())


async def identify(db: AsyncSession, channel: str, request: IdentityRequest):
    scope = resolve_identity_scope(channel, request.appid)
    transaction_id = request.transaction_id if isinstance(request, H5IdentityRequest) else None
    if transaction_id:
        transaction = await scan_service.session_info(transaction_id)
        if transaction.status not in {"WAITING_SCAN", "PENDING"}:
            raise HTTPException(410, "扫码请求已结束，请返回原设备重新扫码")
    identity = await _exchange_code(channel, request.appid, request.code)
    user = await UserService._get_wechat_login_user(db, request.appid, identity["openid"])
    if user is not None and user.phone:
        return await _authenticated(db, user, scope)
    ticket = secrets.token_urlsafe(32)
    stored = StoredIdentity(
        channel=channel, appid=request.appid, openid=identity["openid"],
        unionid=identity.get("unionid"), app_scope=scope, transaction_id=transaction_id,
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=TICKET_TTL_SECONDS),
        legacy_user_id=user.id if user is not None else None,
        token_version=user.token_version if user is not None else None,
    )
    if not await _redis("set", _ticket_key(ticket), stored.model_dump_json(), nx=True, ex=TICKET_TTL_SECONDS):
        raise HTTPException(503, "无法创建验证会话，请重新登录")
    return PendingIdentityResponse(login_ticket=ticket, expires_at=stored.expires_at)


async def load_ticket(ticket: str) -> tuple[str, StoredIdentity]:
    raw = await _redis("get", _ticket_key(ticket))
    if raw is None:
        raise HTTPException(410, "登录验证已过期或已使用，请重新发起微信登录")
    try:
        stored = StoredIdentity.model_validate_json(raw)
        if stored.expires_at.tzinfo is None or stored.expires_at <= datetime.now(timezone.utc):
            raise ValueError()
    except (ValidationError, ValueError):
        raise HTTPException(410, "登录验证已失效，请重新登录") from None
    if resolve_identity_scope(stored.channel, stored.appid) != stored.app_scope:
        raise HTTPException(409, "应用配置已改变，请重新登录")
    return raw, stored


async def _consume_ticket(ticket: str, raw: str) -> None:
    consumed = await _redis("eval", """
        if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
        return redis.call('DEL', KEYS[1])
    """, 1, _ticket_key(ticket), raw)
    if not consumed:
        raise HTTPException(410, "验证会话已使用或过期，请重新登录")


async def _link_once(db: AsyncSession, stored: StoredIdentity, phone: str) -> User:
    lock_names = sorted([f"phone:{phone}", f"wechat:{stored.appid}:{stored.openid}"])
    for name in lock_names:
        lock_id = int.from_bytes(hashlib.sha256(name.encode()).digest()[:8], "big", signed=True)
        await db.execute(text("SELECT pg_advisory_xact_lock(:lock_id)"), {"lock_id": lock_id})
    result = await db.execute(select(UserIdentity).where(
        UserIdentity.provider == "wechat", UserIdentity.provider_app_id == stored.appid,
        UserIdentity.subject == stored.openid,
    ).with_for_update())
    identity = result.scalar_one_or_none()
    result = await db.execute(select(User).where(User.phone == phone).with_for_update())
    phone_user = result.scalar_one_or_none()
    if phone_user is not None:
        UserService.ensure_login_allowed(phone_user)
    if identity is not None:
        if identity.is_deleted:
            raise HTTPException(403, "该微信身份已停用")
        result = await db.execute(select(User).where(User.id == identity.user_id).with_for_update())
        user = result.scalar_one_or_none()
        if user is None:
            raise HTTPException(403, "微信身份所属账号不可用")
        UserService.ensure_login_allowed(user)
        if user.phone:
            if user.phone != phone:
                raise HTTPException(409, "该微信身份已属于其他手机号账号，不允许转移")
            return user
        if stored.legacy_user_id != user.id or stored.token_version != user.token_version:
            raise HTTPException(409, "账号状态已改变，请重新验证微信身份")
        if phone_user is not None and phone_user.id != user.id:
            raise HTTPException(409, "历史微信账号与手机号账号不同，请联系管理员处理；不会自动合并数据")
        user.phone = phone
        identity.verified_at = datetime.now(timezone.utc)
        return user
    if stored.legacy_user_id is not None:
        raise HTTPException(409, "原微信身份已改变，请重新登录")
    user = phone_user
    if user is None:
        user = User(phone=phone, nickname="Hope 用户", source="wechat", roles=[])
        db.add(user)
        await db.flush()
    db.add(UserIdentity(
        user_id=user.id, provider="wechat", provider_app_id=stored.appid,
        subject=stored.openid, unionid=stored.unionid, verified_at=datetime.now(timezone.utc),
    ))
    return user


async def link_identity(db: AsyncSession, stored: StoredIdentity, phone: str) -> User:
    for attempt in range(2):
        try:
            user = await _link_once(db, stored, phone)
            validate_scope(stored.app_scope, user)
            await db.commit()
            await db.refresh(user)
            return user
        except IntegrityError:
            await db.rollback()
            if attempt:
                raise HTTPException(409, "身份关联发生并发冲突，请重新登录确认结果") from None
        except HTTPException:
            await db.rollback()
            raise
        except SQLAlchemyError:
            await db.rollback()
            raise HTTPException(503, "账号关联暂未完成，请重新获取微信 code 并验证手机号") from None
        except Exception:
            await db.rollback()
            raise
    raise HTTPException(409, "身份关联未完成")


async def complete_sms(db: AsyncSession, ticket: str, phone: str, code: str):
    raw, stored = await load_ticket(ticket)
    phone = normalize_phone(phone)
    if not await verify_sms_code(phone, code):
        raise HTTPException(400, "验证码错误或已过期，请重新获取")
    await _consume_ticket(ticket, raw)
    user = await link_identity(db, stored, phone)
    return await _authenticated(db, user, stored.app_scope)


async def complete_miniapp(db: AsyncSession, ticket: str, phone_code: str):
    raw, stored = await load_ticket(ticket)
    if stored.channel != "miniapp":
        raise HTTPException(403, "此验证会话不适用于小程序手机号授权")
    await _consume_ticket(ticket, raw)
    phone = await WeChatService.exchange_phone_code(stored.appid, phone_code)
    user = await link_identity(db, stored, phone)
    return await _authenticated(db, user, stored.app_scope)
