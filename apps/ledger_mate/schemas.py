"""账伴接口入参与出参。"""
import uuid
from datetime import datetime
from typing import Literal, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

RecordType = Literal["income", "expense"]


class CategoryCreate(BaseModel):
    record_type: RecordType
    name: str = Field(min_length=1, max_length=30)
    icon: Optional[str] = Field(None, max_length=50)


class CategoryOut(CategoryCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_enabled: bool
    is_system: bool


class PaymentMethodCreate(BaseModel):
    name: str = Field(min_length=1, max_length=30)
    is_default: bool = False


class PaymentMethodOut(PaymentMethodCreate):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    is_enabled: bool


class RecordCreate(BaseModel):
    record_type: RecordType
    amount_cent: int = Field(gt=0, le=100_000_000)
    category_id: uuid.UUID
    occurred_at: datetime
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None
    idempotency_key: Optional[str] = Field(None, min_length=1, max_length=100)

    @field_validator("note")
    @classmethod
    def strip_note(cls, value: Optional[str]) -> Optional[str]:
        return value.strip() if value else None


class RecordUpdate(BaseModel):
    record_type: Optional[RecordType] = None
    amount_cent: Optional[int] = Field(None, gt=0, le=100_000_000)
    category_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None


class RecordOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    record_type: str
    amount_cent: int
    category_id: uuid.UUID
    payment_method_id: Optional[uuid.UUID]
    occurred_at: datetime
    note: Optional[str]
    source: str
    created_at: datetime


class StatisticsOut(BaseModel):
    start_at: datetime
    end_at: datetime
    income_cent: int
    expense_cent: int
    balance_cent: int
    category_expenses: list[dict]
    daily: list[dict]


class AiDraft(BaseModel):
    record_type: RecordType
    amount_cent: int = Field(gt=0, le=100_000_000)
    category_id: uuid.UUID
    occurred_at: datetime
    note: Optional[str] = Field(None, max_length=500)
    payment_method_id: Optional[uuid.UUID] = None


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


class AiParsedRecord(BaseModel):
    record_type: Optional[RecordType] = None
    amount_cent: Optional[int] = Field(None, gt=0, le=100_000_000)
    category_id: Optional[uuid.UUID] = None
    payment_method_id: Optional[uuid.UUID] = None
    occurred_at: Optional[datetime] = None
    note: Optional[str] = Field(None, max_length=500)


class AiParseResult(BaseModel):
    status: Literal["ready", "needs_clarification"]
    records: list[AiParsedRecord] = Field(default_factory=list, max_length=20)
    playful_text: str = Field(default="", max_length=40)
    emoji: str = Field(default="✨", max_length=8)
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
