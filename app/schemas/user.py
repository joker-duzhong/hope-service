"""
用户相关的 Pydantic 模型
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ==================== 用户模型 ====================

class UserBase(BaseModel):
    """用户基础模型"""
    nickname: Optional[str] = Field(None, max_length=100)
    avatar: Optional[str] = Field(None, max_length=500)


class UserCreate(BaseModel):
    """用户名密码注册模型"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    nickname: Optional[str] = Field(None, max_length=100)
    source: str = Field(default="default", max_length=50)


class UserResponse(UserBase):
    """用户响应模型"""
    id: int
    openid: Optional[str] = None
    phone: Optional[str] = None
    source: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ==================== 登录模型 ====================

class UsernameLogin(BaseModel):
    """用户名密码登录"""
    username: str
    password: str


class WechatLogin(BaseModel):
    """微信授权登录"""
    code: str = Field(..., description="微信授权码")


class WechatAuthUrl(BaseModel):
    """微信授权URL响应"""
    auth_url: str


class PhoneLogin(BaseModel):
    """手机号验证码登录"""
    phone: str = Field(..., description="手机号")
    code: str = Field(..., description="短信验证码")


class SendSmsCode(BaseModel):
    """发送短信验证码"""
    phone: str = Field(..., description="手机号")
    scene: str = Field(default="login", description="场景：login/register/reset")


# ==================== Token模型 ====================

class Token(BaseModel):
    """令牌响应模型"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenPayload(BaseModel):
    """令牌载荷模型"""
    sub: Optional[int] = None
    exp: Optional[int] = None
    type: Optional[str] = None
