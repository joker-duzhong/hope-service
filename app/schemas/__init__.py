"""
Pydantic 数据验证模型
"""
from app.schemas.user import (
    UserBase,
    UserCreate,
    UserResponse,
    UsernameLogin,
    WechatLogin,
    WechatAuthUrl,
    PhoneLogin,
    SendSmsCode,
    Token,
    TokenPayload,
)
from app.schemas.common import ResponseModel, ErrorResponse

__all__ = [
    "UserBase",
    "UserCreate",
    "UserResponse",
    "UsernameLogin",
    "WechatLogin",
    "WechatAuthUrl",
    "PhoneLogin",
    "SendSmsCode",
    "Token",
    "TokenPayload",
    "ResponseModel",
    "ErrorResponse",
]
