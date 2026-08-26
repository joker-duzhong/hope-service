"""账伴 ORM 模型，所有金额均以分为单位保存。"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import CoreModel


class LedgerMateBook(CoreModel):
    __tablename__ = "ledger_mate_books"
    __table_args__ = (UniqueConstraint("user_id", name="uq_ledger_mate_book_user"),)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(50), default="我的账本")
    currency: Mapped[str] = mapped_column(String(3), default="CNY")
    timezone: Mapped[str] = mapped_column(String(50), default="Asia/Shanghai")


class LedgerMateCategory(CoreModel):
    __tablename__ = "ledger_mate_categories"
    __table_args__ = (UniqueConstraint("user_id", "record_type", "name", name="uq_ledger_mate_category"),)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    record_type: Mapped[str] = mapped_column(String(10), index=True)  # income / expense
    name: Mapped[str] = mapped_column(String(30))
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False)


class LedgerMatePaymentMethod(CoreModel):
    __tablename__ = "ledger_mate_payment_methods"
    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_ledger_mate_payment_method"),)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(30))
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class LedgerMateRecord(CoreModel):
    __tablename__ = "ledger_mate_records"
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    book_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    category_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    payment_method_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    record_type: Mapped[str] = mapped_column(String(10), index=True)
    amount_cent: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, index=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    import_batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True, index=True)


class LedgerMateOperationLog(CoreModel):
    __tablename__ = "ledger_mate_operation_logs"
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    record_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    action: Mapped[str] = mapped_column(String(20))
    before_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    after_data: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class LedgerMateImportBatch(CoreModel):
    __tablename__ = "ledger_mate_import_batches"
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="preview")
    file_name: Mapped[str] = mapped_column(String(255))
    rows: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class LedgerMateAiSession(CoreModel):
    __tablename__ = "ledger_mate_ai_sessions"
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    title: Mapped[str] = mapped_column(String(100), default="AI 记账")


class LedgerMateAiMessage(CoreModel):
    __tablename__ = "ledger_mate_ai_messages"
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    role: Mapped[str] = mapped_column(String(10))
    content: Mapped[str] = mapped_column(Text)
    payload: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class LedgerMateAiRecordReference(CoreModel):
    __tablename__ = "ledger_mate_ai_record_references"
    __table_args__ = (UniqueConstraint("message_id", "record_id", name="uq_ledger_mate_ai_message_record"),)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    session_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    message_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
    record_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), index=True)
