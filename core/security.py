"""
密码哈希、JWT 生成与校验
"""
from datetime import datetime, timedelta, timezone
import json
from typing import Any, Optional
from uuid import uuid4

import bcrypt
from jose import jwt

from core.config import settings
from core.redis_client import redis_client
from core.auth_scope import PASSPORT_SCOPE, validate_scope


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    return bcrypt.checkpw(
        plain_password.encode("utf-8"), hashed_password.encode("utf-8")
    )


def get_password_hash(password: str) -> str:
    """生成密码哈希"""
    return bcrypt.hashpw(
        password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")


def create_access_token(
    subject: Any,
    expires_delta: Optional[timedelta] = None,
    token_version: int = 0,
    app_scope: str = PASSPORT_SCOPE,
) -> str:
    """创建访问令牌"""
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    now = datetime.now(timezone.utc)
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "access",
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "jti": str(uuid4()),
        "token_version": token_version,
        "app_scope": app_scope,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def create_refresh_token(subject: Any, token_version: int = 0, app_scope: str = PASSPORT_SCOPE) -> str:
    """创建刷新令牌"""
    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    now = datetime.now(timezone.utc)
    to_encode = {
        "exp": expire,
        "sub": str(subject),
        "type": "refresh",
        "app_scope": app_scope,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "iat": now,
        "jti": str(uuid4()),
        "token_version": token_version,
    }
    return jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    """解码令牌"""
    try:
        return jwt.decode(
            token,
            settings.SECRET_KEY,
            algorithms=[settings.ALGORITHM],
            issuer=settings.JWT_ISSUER,
            audience=settings.JWT_AUDIENCE,
        )
    except jwt.JWTError:
        return None


def _build_token_pair(subject: Any, token_version: int, app_scope: str = PASSPORT_SCOPE) -> tuple[str, str, str]:
    access_token = create_access_token(subject, token_version=token_version, app_scope=app_scope)
    refresh_token = create_refresh_token(subject, token_version=token_version, app_scope=app_scope)
    payload = decode_token(refresh_token)
    if not payload or not payload.get("jti"):
        raise RuntimeError("无法创建刷新令牌会话")
    return access_token, refresh_token, payload["jti"]


async def create_token_pair(subject: Any, token_version: int = 0, app_scope: str = PASSPORT_SCOPE) -> tuple[str, str]:
    """Issue an access token and a single-use, server-tracked refresh token."""
    validate_scope(app_scope)
    access_token, refresh_token, token_id = _build_token_pair(subject, token_version, app_scope)

    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60
    await redis_client.setex(
        f"auth:refresh:{token_id}",
        ttl,
        json.dumps({"sub": str(subject), "token_version": token_version, "app_scope": app_scope}),
    )
    return access_token, refresh_token


async def rotate_refresh_token(
    token: str, subject: Any, token_version: int
) -> Optional[tuple[str, str]]:
    """原子替换刷新会话；旧令牌严格一次性使用，不提供重试宽限窗口。"""
    payload = decode_token(token)
    if not payload or payload.get("type") != "refresh" or not payload.get("jti"):
        return None
    if payload.get("sub") != str(subject) or payload.get("token_version") != token_version:
        return None
    app_scope = payload.get("app_scope")
    if not isinstance(app_scope, str) or not app_scope:
        return None
    validate_scope(app_scope)

    access_token, refresh_token, token_id = _build_token_pair(subject, token_version, app_scope)
    session_data = json.dumps({"sub": str(subject), "token_version": token_version, "app_scope": app_scope})
    ttl = settings.REFRESH_TOKEN_EXPIRE_DAYS * 24 * 60 * 60

    result = await redis_client.eval(
        """
        local value = redis.call('GET', KEYS[1])
        if not value then return 0 end
        local valid, session = pcall(cjson.decode, value)
        if not valid or type(session) ~= 'table' then return 0 end
        if session.sub ~= ARGV[1] or session.token_version ~= tonumber(ARGV[2]) then return 0 end
        if session.app_scope ~= ARGV[5] then return 0 end
        local created = redis.call('SET', KEYS[2], ARGV[4], 'EX', ARGV[3], 'NX')
        if not created then return 0 end
        redis.call('DEL', KEYS[1])
        return 1
        """,
        2,
        f"auth:refresh:{payload['jti']}",
        f"auth:refresh:{token_id}",
        str(subject),
        token_version,
        ttl,
        session_data,
        app_scope,
    )
    return (access_token, refresh_token) if result == 1 else None
