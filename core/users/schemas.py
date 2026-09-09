"""
用户相关的 Pydantic 进出参模型
"""
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from core.sms import normalize_phone


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
    scope: str | None = None

    class Config:
        from_attributes = True


class UserResponse(UserBase):
    """用户响应模型"""
    id: UUID
    openid: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    source: Optional[str] = "default"
    is_active: Optional[bool] = True
    is_superuser: Optional[bool] = False
    roles: List[RoleInfo] = []
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @computed_field
    @property
    def needs_phone_binding(self) -> bool:
        return not bool(self.phone)

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


class LoginResponse(Token):
    user: UserResponse
    app_scope: str = "passport"


class RefreshRequest(BaseModel):
    """刷新令牌请求"""
    refresh_token: str


# ==================== 短信与手机验证模型 ====================

class SendSmsRequest(BaseModel):
    phone: str = Field(..., max_length=20, description="中国大陆手机号，可带 +86 前缀")

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        return normalize_phone(value)


class SendSmsCodeRequest(SendSmsRequest):
    test: str | None = Field(None, description="精确传入 hope 时跳过短信发送并返回测试验证码，所有环境可用")


class SmsCodeResponse(BaseModel):
    code: str = Field(..., pattern=r"^[0-9]{4}$", description="测试流程的四位验证码")


class PhoneLoginRequest(SendSmsRequest):
    code: str = Field(..., pattern=r"^[0-9]{4}$", description="四位短信验证码")
    app_key: str | None = Field(None, min_length=1, max_length=64, description="PC 目标应用；授权中心不填写")


class BindPhoneRequest(SendSmsRequest):
    """验证码验证后首次绑定手机号，不支持换绑或合并。"""
    code: str = Field(..., pattern=r"^[0-9]{4}$", description="四位短信验证码")
