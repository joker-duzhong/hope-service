from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ScanStatus(str, Enum):
    WAITING_SCAN = "WAITING_SCAN"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    CONSUMED = "CONSUMED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


class ScanAppResponse(BaseModel):
    app_key: str
    name: str


class ScanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_key: str = Field(..., min_length=1, max_length=64, description="从可用应用列表取得的业务标识，不是微信 AppID")


class ScanStateResponse(BaseModel):
    transaction_id: UUID
    status: ScanStatus
    app: ScanAppResponse | None = None
    expires_at: datetime | None = None


class ScanCreateResponse(ScanStateResponse):
    poll_token: str = Field(..., description="仅发起端保存，不放入二维码或 URL")
    poll_interval_seconds: int = 2


class ScanPollResponse(ScanStateResponse):
    exchange_code: str | None = Field(default=None, description="仅 CONFIRMED 状态返回的一次性兑换码")


class ScanExchangeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    transaction_id: UUID
    exchange_code: str = Field(..., min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class StoredScanSession(BaseModel):
    transaction_id: UUID
    app_key: str
    status: ScanStatus = ScanStatus.WAITING_SCAN
    poll_token_hash: str
    expires_at: datetime
    user_id: UUID | None = None
    token_version: int | None = None
    exchange_code: str | None = None
