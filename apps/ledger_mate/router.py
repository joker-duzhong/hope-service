"""账伴 HTTP 路由，仅负责鉴权、参数与响应封装。"""
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.schemas import AiChatResponse, AiConfirmRequest, AiMessageCreate, AiMessageOut, AiSessionCreate, AiSessionOut, CategoryCreate, CategoryOut, PaymentMethodCreate, PaymentMethodOut, RecordCreate, RecordOut, RecordUpdate, StatisticsOut
from apps.ledger_mate.services import LedgerMateService
from core.database import get_db
from core.response import PaginatedData, PaginatedResponse, ResponseModel
from core.users.dependencies import get_current_user
from core.users.models import User

router = APIRouter()


async def _ai_message_out(db: AsyncSession, user_id: uuid.UUID, message) -> AiMessageOut:
    records = await LedgerMateService._message_records(db, user_id, message.id)
    return AiMessageOut(id=message.id, role=message.role, content=message.content, payload=message.payload, records=[RecordOut.model_validate(record) for record in records], created_at=message.created_at)


@router.get("/categories", response_model=ResponseModel[list[CategoryOut]])
async def list_categories(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=[CategoryOut.model_validate(item) for item in await LedgerMateService.categories(db, current_user.id)])


@router.post("/categories", response_model=ResponseModel[CategoryOut])
async def create_category(data: CategoryCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=CategoryOut.model_validate(await LedgerMateService.add_category(db, current_user.id, data)), message="分类已创建")


@router.get("/payment-methods", response_model=ResponseModel[list[PaymentMethodOut]])
async def list_payment_methods(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=[PaymentMethodOut.model_validate(item) for item in await LedgerMateService.methods(db, current_user.id)])


@router.post("/payment-methods", response_model=ResponseModel[PaymentMethodOut])
async def create_payment_method(data: PaymentMethodCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=PaymentMethodOut.model_validate(await LedgerMateService.add_method(db, current_user.id, data)), message="支付方式已创建")


@router.post("/records", response_model=ResponseModel[RecordOut])
async def create_record(data: RecordCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=RecordOut.model_validate(await LedgerMateService.create_record(db, current_user.id, data)), message="记账成功")


@router.get("/records", response_model=PaginatedResponse[RecordOut])
async def list_records(page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), start_at: Optional[datetime] = None, end_at: Optional[datetime] = None, record_type: Optional[str] = Query(None, pattern="^(income|expense)$"), category_id: Optional[uuid.UUID] = None, keyword: Optional[str] = Query(None, max_length=100), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    records, total = await LedgerMateService.list_records(db, current_user.id, page, page_size, start_at, end_at, record_type, category_id, keyword)
    return PaginatedResponse(data=PaginatedData(items=[RecordOut.model_validate(item) for item in records], total=total, page=page, page_size=page_size, total_pages=(total + page_size - 1) // page_size if total else 0))


@router.get("/records/{record_id}", response_model=ResponseModel[RecordOut])
async def get_record(record_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=RecordOut.model_validate(await LedgerMateService.get_record(db, current_user.id, record_id)))


@router.put("/records/{record_id}", response_model=ResponseModel[RecordOut])
async def update_record(record_id: uuid.UUID, data: RecordUpdate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=RecordOut.model_validate(await LedgerMateService.update_record(db, current_user.id, record_id, data)), message="账单已更新")


@router.delete("/records/{record_id}", response_model=ResponseModel[None])
async def delete_record(record_id: uuid.UUID, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    await LedgerMateService.delete_record(db, current_user.id, record_id)
    return ResponseModel(message="账单已删除")


@router.get("/statistics", response_model=ResponseModel[StatisticsOut])
async def statistics(start_at: datetime, end_at: datetime, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    if end_at <= start_at:
        from fastapi import HTTPException
        raise HTTPException(400, "结束时间必须晚于开始时间")
    data = await LedgerMateService.statistics(db, current_user.id, start_at, end_at)
    return ResponseModel(data=StatisticsOut(start_at=start_at, end_at=end_at, **data))


@router.post("/ai/confirm", response_model=ResponseModel[list[RecordOut]])
async def confirm_ai_drafts(data: AiConfirmRequest, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    records = []
    for index, draft in enumerate(data.drafts):
        record = await LedgerMateService.create_record(db, current_user.id, RecordCreate(**draft.model_dump(), idempotency_key=f"{data.idempotency_key}:{index}"), source="ai")
        records.append(RecordOut.model_validate(record))
    return ResponseModel(data=records, message="AI 账单已确认入账")


@router.post("/ai/sessions", response_model=ResponseModel[AiSessionOut])
async def create_ai_session(data: AiSessionCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=AiSessionOut.model_validate(await LedgerMateService.create_ai_session(db, current_user.id, data)))


@router.get("/ai/sessions", response_model=ResponseModel[list[AiSessionOut]])
async def list_ai_sessions(current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=[AiSessionOut.model_validate(item) for item in await LedgerMateService.list_ai_sessions(db, current_user.id)])


@router.get("/ai/sessions/{session_id}/messages", response_model=ResponseModel[list[AiMessageOut]])
async def list_ai_messages(session_id: uuid.UUID, limit: int = Query(100, ge=1, le=100), current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    messages = await LedgerMateService.get_ai_messages(db, current_user.id, session_id, limit)
    return ResponseModel(data=[await _ai_message_out(db, current_user.id, message) for message in messages])


@router.post("/ai/sessions/{session_id}/messages", response_model=ResponseModel[AiChatResponse])
async def chat_ai(session_id: uuid.UUID, data: AiMessageCreate, current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    session, user_message, assistant_message, records = await LedgerMateService.chat_with_ai(db, current_user.id, session_id, data)
    return ResponseModel(data=AiChatResponse(session=AiSessionOut.model_validate(session), user_message=await _ai_message_out(db, current_user.id, user_message), assistant_message=AiMessageOut(id=assistant_message.id, role="assistant", content=assistant_message.content, payload=assistant_message.payload, records=[RecordOut.model_validate(record) for record in records], created_at=assistant_message.created_at)), message="AI 已解析并入账" if records else "AI 需要补充信息")
