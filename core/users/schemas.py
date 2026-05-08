"""
用户相关的 Pydantic 进出参模型
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


# ==================== 用户模型 ====================

class UserAvatarResponse(BaseModel):
    """用户头像响应模型，资源 ID 会展开资源信息，外链仅填充 url"""
    id: Optional[UUID] = None
    name: Optional[str] = None
    url: str
    thumb_url: Optional[str] = None
    size: Optional[int] = None
    type: Optional[str] = None
    scope: Optional[str] = None
    hash: Optional[str] = None
    owner: Optional[UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="before")
    @classmethod
    def accept_url_string(cls, value):
        if isinstance(value, str):
            return {"url": value}
        return value


class UserBase(BaseModel):
    """用户基础模型"""
    nickname: Optional[str] = Field(None, max_length=100)
    avatar: Optional[UserAvatarResponse] = None


class UserCreate(BaseModel):
    """用户名密码注册"""
    username: str = Field(..., min_length=2, max_length=50)
    password: str = Field(..., min_length=6, max_length=100)
    email: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    nickname: Optional[str] = Field(None, max_length=100)
    source: str = Field(default="default", max_length=50)


class UserUpdate(BaseModel):
    """更新用户信息"""
    username: Optional[str] = Field(None, min_length=2, max_length=50)
    email: Optional[str] = Field(None, max_length=100)
    nickname: Optional[str] = Field(None, max_length=100)
    avatar: Optional[str] = Field(None, max_length=500)


class RoleInfo(BaseModel):
    """角色简要信息（嵌入用户响应中）"""
    id: UUID
    name: str
    code: str

    class Config:
        from_attributes = True


class UserResponse(UserBase):
    """用户响应模型"""
    id: UUID
    openid: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: str
    is_active: bool
    roles: List[RoleInfo] = []
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
    appid: str = Field(..., description="微信公众号 AppID")
    code: str = Field(..., description="微信授权码")


class WechatAuthUrl(BaseModel):
    """微信授权URL响应"""
    auth_url: str


# ==================== Token 模型 ====================

class Token(BaseModel):
    """令牌响应"""
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    """刷新令牌请求"""
    refresh_token: str


# ==================== 短信与手机验证模型 ====================

class SendSmsRequest(BaseModel):
    phone: str = Field(..., max_length=20, description="手机号")
    purpose: str = Field(..., description="用途: register, bind")

class PhoneRegisterRequest(BaseModel):
    phone: str = Field(..., max_length=20, description="手机号")
    code: str = Field(..., description="短信验证码")
    password: Optional[str] = Field(None, description="可选设置密码")
    nickname: Optional[str] = Field(None, max_length=100)
    source: str = Field(default="phone", max_length=50)

class BindPhoneRequest(BaseModel):
    phone: str = Field(..., max_length=20, description="手机号")
    code: str = Field(..., description="短信验证码")
