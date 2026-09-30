"""账伴接口入参与出参，日期优先使用 YYYY-MM-DD。"""
import uuid
from datetime import date, datetime
from typing import Annotated, Literal, Optional

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, field_validator, model_validator

from apps.ledger_mate.dates import date_only, local_datetime, midnight

RecordType = Literal["income", "expense"]
DateOnly = Annotated[date, BeforeValidator(date_only)]


class CategoryCreate(BaseModel):
    record_type: RecordType
    name: str = Field(min_length=1, max_length=30)
    icon: Optional[str] = Field(None, max_length=500)

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class CategoryOut(CategoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    sort_order: int
    is_enabled: bool
    is_system: bool


class CategoryTemplateCreate(BaseModel):
    record_type: RecordType
    name: str = Field(min_length=1, max_length=30)
    icon: Optional[str] = Field(None, max_length=500)
    sort_order: int = Field(default=0, ge=0, le=1_000_000)
    is_enabled: bool = True

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class CategoryTemplateUpdate(BaseModel):
    record_type: Optional[RecordType] = None
    name: Optional[str] = Field(None, min_length=1, max_length=30)
    icon: Optional[str] = Field(None, max_length=500)
    sort_order: Optional[int] = Field(None, ge=0, le=1_000_000)
    is_enabled: Optional[bool] = None

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class CategoryTemplateOut(CategoryTemplateCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class PaymentMethodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    is_default: bool = False

    @field_validator("name", mode="before")
    @classmethod
    def trim_name(cls, value):
        return value.strip() if isinstance(value, str) else value


class PaymentMethodOut(PaymentMethodCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_enabled: bool


class RecordDateFields(BaseModel):
    occurred_date: Optional[DateOnly] = None
    occurred_at: Optional[datetime] = None

    @model_validator(mode="after")
    def synchronize_date(self):
        if self.occurred_date is not None:
            if self.occurred_at is not None and local_datetime(self.occurred_at).date() != self.occurred_date:
                raise ValueError("账单日期与旧时间字段不一致")
            self.occurred_at = midnight(self.occurred_date)
        elif self.occurred_at is not None:
            self.occurred_at = local_datetime(self.occurred_at)
        return self


class RecordCreate(RecordDateFields):
    record_type: RecordType
    amount_cent: int = Field(gt=0, le=100_000_000, strict=True)
    category_id: uuid.UUID
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def require_date(self):
        if self.occurred_at is None:
            raise ValueError("请提供账单日期 occurred_date")
        return self

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() or None if value else None


class RecordUpdate(RecordDateFields):
    record_type: Optional[RecordType] = None
    amount_cent: Optional[int] = Field(None, gt=0, le=100_000_000, strict=True)
    category_id: Optional[uuid.UUID] = None
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def validate_update(self):
        if not self.model_fields_set:
            raise ValueError("请提供要修改的账单信息")
        for field in ("record_type", "amount_cent", "category_id", "occurred_date", "occurred_at"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不可为空")
        return self

    @field_validator("note")
    @classmethod
    def strip_note(cls, value):
        return value.strip() or None if value else None


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    record_type: str
    amount_cent: int
    category_id: uuid.UUID
    payment_method_id: Optional[uuid.UUID]
    occurred_date: date
    occurred_at: datetime
    note: Optional[str]
    source: str
    created_at: datetime

    @model_validator(mode="before")
    @classmethod
    def expose_local_date(cls, value):
        values = dict(value) if isinstance(value, dict) else {
            key: getattr(value, key) for key in cls.model_fields if key != "occurred_date"
        }
        occurred_at = values.get("occurred_at")
        if isinstance(occurred_at, str):
            occurred_at = datetime.fromisoformat(occurred_at.replace("Z", "+00:00"))
        if occurred_at is not None:
            values["occurred_date"] = local_datetime(occurred_at).date()
        return values


class StatisticsOut(BaseModel):
    start_at: datetime
    end_at: datetime
    start_date: date
    end_date: date
    income_cent: int
    expense_cent: int
    balance_cent: int
    record_count: int
    income_count: int
    expense_count: int
    category_expenses: list[dict]
    category_incomes: list[dict]
    daily: list[dict]


class AiDraft(RecordDateFields):
    record_type: RecordType
    amount_cent: int = Field(gt=0, le=100_000_000, strict=True)
    category_id: uuid.UUID
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def require_date(self):
        if self.occurred_at is None:
            raise ValueError("请提供账单日期 occurred_date")
        return self


class AiConfirmRequest(BaseModel):
    drafts: list[AiDraft] = Field(min_length=1, max_length=20)
    idempotency_key: str = Field(min_length=1, max_length=100)


class AiSessionCreate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=100)


class AiSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    created_at: datetime
    updated_at: datetime


class AiMessageCreate(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    client_message_id: str = Field(min_length=1, max_length=100)

    @field_validator("content", "client_message_id", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value


class AiParsedRecord(RecordDateFields):
    record_type: Optional[RecordType] = None
    amount_cent: Optional[int] = Field(None, gt=0, le=100_000_000, strict=True)
    category_id: Optional[uuid.UUID] = None
    category_name: Optional[str] = Field(None, max_length=30)
    payment_method_id: Optional[uuid.UUID] = None
    note: Optional[str] = Field(None, max_length=500)


class AiParseResult(BaseModel):
    status: Literal["ready", "needs_clarification"]
    records: list[AiParsedRecord] = Field(default_factory=list, max_length=20)
    playful_text: str = Field(default="", max_length=40)
    emoji: str = Field(default="", max_length=8)
    sticker: Optional[str] = None
    questions: list[str] = Field(default_factory=list, max_length=5)


class AiMessageOut(BaseModel):
    id: uuid.UUID
    role: Literal["user", "assistant"]
    content: str
    payload: Optional[dict] = None
    records: list[RecordOut] = Field(default_factory=list)
    created_at: datetime


class AiChatResponse(BaseModel):
    session: AiSessionOut
    user_message: AiMessageOut
    assistant_message: AiMessageOut


class AiRequestOut(BaseModel):
    status: Literal["queued", "processing", "completed", "failed"]
    client_message_id: str
    session: AiSessionOut
    user_message: AiMessageOut
    assistant_message: Optional[AiMessageOut] = None
    error_message: Optional[str] = None
