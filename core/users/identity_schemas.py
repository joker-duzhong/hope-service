from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from core.users.schemas import LoginResponse, SendSmsRequest


class IdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    appid: str = Field(min_length=1, max_length=64)
    code: str = Field(min_length=1, max_length=512)


class H5IdentityRequest(IdentityRequest):
    transaction_id: UUID | None = None


class PendingIdentityResponse(BaseModel):
    status: Literal["PHONE_REQUIRED"] = "PHONE_REQUIRED"
    login_ticket: str
    expires_at: datetime


class AuthenticatedIdentityResponse(LoginResponse):
    status: Literal["AUTHENTICATED"] = "AUTHENTICATED"


IdentityResponse = Annotated[
    PendingIdentityResponse | AuthenticatedIdentityResponse, Field(discriminator="status")
]


class CompleteSmsIdentityRequest(SendSmsRequest):
    model_config = ConfigDict(extra="forbid")
    login_ticket: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    code: str = Field(pattern=r"^[0-9]{4}$")
    accepted_terms: Literal[True]


class CompleteMiniappIdentityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    login_ticket: str = Field(min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    phone_code: str = Field(min_length=1, max_length=512)
    accepted_terms: Literal[True]


class StoredIdentity(BaseModel):
    channel: Literal["h5", "miniapp"]
    appid: str
    openid: str
    unionid: str | None = None
    app_scope: str
    expires_at: datetime
    transaction_id: UUID | None = None
    legacy_user_id: UUID | None = None
    token_version: int | None = None
