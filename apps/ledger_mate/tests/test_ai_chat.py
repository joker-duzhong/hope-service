import json
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql

from apps.ledger_mate import services
from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiRecordReference, LedgerMateOperationLog, LedgerMateRecord
from apps.ledger_mate.schemas import AiConfirmRequest, AiMessageCreate, RecordOut, RecordUpdate
from apps.ledger_mate.services import LedgerMateService
from core.llm.errors import ChatGenerationError


def ready(h, **changes):
    record = {"record_type": "expense", "amount_cent": 2800, "category_id": str(h.expense.id), "occurred_date": "2026-09-26", "note": "午餐", **changes}
    return {"status": "ready", "records": [record], "playful_text": "午餐记好了。", "questions": []}


def fake_llm(monkeypatch, result):
    calls = []

    async def generate(messages, **kwargs):
        calls.append(messages)
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(services, "generate_chat", generate)
    return calls


@pytest.mark.asyncio
async def test_retry_returns_same_message_and_latest_linked_record_without_new_llm(ledger_db, monkeypatch):
    h = ledger_db
    calls = fake_llm(monkeypatch, ready(h))
    data = AiMessageCreate(content="午餐 28 元", client_message_id="stable-request")
    first = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert len(first[3]) == 1
    assert first[1].payload["client_message_id"] == "stable-request"
    assert first[2].payload["reply_to"] == str(first[1].id)
    record_id = first[3][0].id
    await LedgerMateService.update_record(h.db, h.user, record_id, RecordUpdate(amount_cent=3200, occurred_date="2026-09-25"))
    retry = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert retry[1].id == first[1].id and retry[2].id == first[2].id
    assert retry[3][0].amount_cent == 3200
    assert RecordOut.model_validate(retry[3][0]).occurred_date.isoformat() == "2026-09-25"
    await LedgerMateService.delete_record(h.db, h.user, record_id)
    deleted_retry = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert deleted_retry[3] == []
    assert len(calls) == 1
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateAiMessage)) == 2
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 1


@pytest.mark.asyncio
async def test_reusing_client_id_for_different_content_is_conflict(ledger_db, monkeypatch):
    h = ledger_db
    calls = fake_llm(monkeypatch, ready(h))
    await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐 28", client_message_id="request"))
    with pytest.raises(HTTPException) as conflict:
        await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐 99", client_message_id="request"))
    assert conflict.value.status_code == 409 and len(calls) == 1


@pytest.mark.asyncio
async def test_missing_amount_and_foreign_category_produce_questions_not_records(ledger_db, monkeypatch):
    h = ledger_db
    for index, changes in enumerate([{"amount_cent": None}, {"category_id": str(uuid4())}, {"occurred_date": None}, {"payment_method_id": str(uuid4())}]):
        fake_llm(monkeypatch, ready(h, **changes))
        response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐", client_message_id=f"missing-{index}"))
        assert response[2].payload["status"] == "needs_clarification"
        assert response[2].payload["questions"]
        assert "记好了" not in response[2].content
        assert response[3] == []
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0


@pytest.mark.asyncio
async def test_questions_prevent_partial_autosave_and_followup_saves_once(ledger_db, monkeypatch):
    h = ledger_db
    first_result = ready(h)
    first_result["records"].append({**first_result["records"][0], "amount_cent": None, "note": "晚餐"})
    calls = fake_llm(monkeypatch, first_result)
    first = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐 28，晚餐忘了金额", client_message_id="first"))
    assert first[3] == []
    complete = ready(h)
    complete["records"].append({**complete["records"][0], "amount_cent": 3600, "note": "晚餐"})
    calls = fake_llm(monkeypatch, complete)
    second = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="晚餐 36", client_message_id="second"))
    context = json.loads(calls[0][0]["content"].split("上下文：\n")[1])
    assert len(context["history"]) == 2
    assert len(second[3]) == 2
    calls = fake_llm(monkeypatch, ready(h))
    await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="另记午餐 28", client_message_id="third"))
    context = json.loads(calls[0][0]["content"].split("上下文：\n")[1])
    assert context["history"] == []
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 3


@pytest.mark.asyncio
async def test_ready_with_questions_never_saves_and_existing_category_name_maps(ledger_db, monkeypatch):
    h = ledger_db
    result = ready(h)
    result["questions"] = ["这笔发生在昨天还是今天？"]
    fake_llm(monkeypatch, result)
    response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐 28", client_message_id="question"))
    assert response[3] == []
    fake_llm(monkeypatch, ready(h, category_id=None, category_name="餐饮"))
    response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="今天", client_message_id="answer"))
    assert response[3][0].category_id == h.expense.id


@pytest.mark.asyncio
async def test_failure_after_record_flush_rolls_back_messages_records_logs_and_references(ledger_db, monkeypatch):
    h = ledger_db
    fake_llm(monkeypatch, ready(h))
    save_message = LedgerMateService._save_message

    async def fail_assistant(db, user_id, session_id, role, content, payload=None):
        if role == "assistant":
            raise RuntimeError("simulated write failure")
        return await save_message(db, user_id, session_id, role, content, payload)

    monkeypatch.setattr(LedgerMateService, "_save_message", fail_assistant)
    data = AiMessageCreate(content="午餐 28", client_message_id="retry-after-failure")
    with pytest.raises(RuntimeError):
        await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    for model in (LedgerMateRecord, LedgerMateOperationLog, LedgerMateAiMessage, LedgerMateAiRecordReference):
        assert await h.db.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(LedgerMateService, "_save_message", save_message)
    response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert len(response[3]) == 1


@pytest.mark.asyncio
async def test_bad_model_output_does_not_persist(ledger_db, monkeypatch):
    h = ledger_db
    for index, response in enumerate(["not json", '{"status":"ready","records":[{"amount_cent":12.5}]}']):
        monkeypatch.setattr(services, "generate_chat", AsyncMock(return_value=response))
        with pytest.raises(HTTPException) as bad:
            await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, AiMessageCreate(content="午餐", client_message_id=f"invalid-{index}"))
        assert bad.value.status_code == 502
        assert bad.value.detail == "AI 返回的记账结构无效，请重试"
    for model in (LedgerMateRecord, LedgerMateOperationLog, LedgerMateAiMessage, LedgerMateAiRecordReference):
        assert await h.db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "diagnostics"), [
    ("configuration", {}),
    ("timeout", {}),
    ("connection", {}),
    ("http", {"http_status": 401, "message_category": "invalid_credentials", "error_message": "Invalid API key [redacted]"}),
    ("invalid_json", {}),
    ("invalid_response", {}),
    ("empty_response", {}),
])
async def test_known_model_failure_exposes_safe_reason_and_can_retry(ledger_db, monkeypatch, caplog, kind, diagnostics):
    h = ledger_db
    success_result = ready(h)
    error = ChatGenerationError(kind, diagnostics)
    monkeypatch.setattr(services, "generate_chat", AsyncMock(side_effect=error))
    data = AiMessageCreate(content="午餐 28", client_message_id="retry-known-failure")
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as unavailable:
        await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert unavailable.value.status_code == 502
    assert unavailable.value.detail == str(error)
    assert unavailable.value.detail != "记账助手暂时没有回应，请稍后重试这条消息"
    assert str(h.session_id) in caplog.text
    assert kind in caplog.text
    if kind == "http":
        assert "401" in unavailable.value.detail
        assert diagnostics["error_message"] in unavailable.value.detail
        assert diagnostics["message_category"] in caplog.text
    for model in (LedgerMateRecord, LedgerMateOperationLog, LedgerMateAiMessage, LedgerMateAiRecordReference):
        assert await h.db.scalar(select(func.count()).select_from(model)) == 0
    calls = fake_llm(monkeypatch, success_result)
    response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert len(response[3]) == 1 and len(calls) == 1
    assert response[1].payload["client_message_id"] == data.client_message_id
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateAiMessage)) == 2


@pytest.mark.asyncio
async def test_unknown_model_failure_does_not_expose_details_and_can_retry(ledger_db, monkeypatch, caplog):
    h = ledger_db
    success_result = ready(h)
    private_detail = "provider diagnostic details mock-private-marker"
    monkeypatch.setattr(services, "generate_chat", AsyncMock(side_effect=RuntimeError(private_detail)))
    data = AiMessageCreate(content="午餐 28", client_message_id="retry-unknown-failure")
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as unavailable:
        await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert unavailable.value.status_code == 502
    assert unavailable.value.detail == "记账助手暂时没有回应，请稍后重试这条消息"
    assert private_detail not in unavailable.value.detail + caplog.text
    assert "mock-private-marker" not in caplog.text
    assert "RuntimeError" in caplog.text and str(h.session_id) in caplog.text
    for model in (LedgerMateRecord, LedgerMateOperationLog, LedgerMateAiMessage, LedgerMateAiRecordReference):
        assert await h.db.scalar(select(func.count()).select_from(model)) == 0
    calls = fake_llm(monkeypatch, success_result)
    response = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data)
    assert len(response[3]) == 1 and len(calls) == 1
    assert response[1].payload["client_message_id"] == data.client_message_id
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateAiMessage)) == 2


@pytest.mark.asyncio
async def test_foreign_session_is_inaccessible_and_never_calls_llm(ledger_db, monkeypatch):
    h = ledger_db
    calls = fake_llm(monkeypatch, ready(h))
    with pytest.raises(HTTPException) as denied:
        await LedgerMateService.chat_with_ai(h.db, uuid4(), h.session_id, AiMessageCreate(content="午餐 28", client_message_id="foreign"))
    assert denied.value.status_code == 404 and calls == []
    with pytest.raises(HTTPException):
        await LedgerMateService.get_ai_messages(h.db, uuid4(), h.session_id)


@pytest.mark.asyncio
async def test_latest_messages_limit_is_chronological_and_links_are_owner_scoped(ledger_db):
    h = ledger_db
    start = datetime(2026, 9, 26, tzinfo=timezone.utc)
    for index in range(6):
        h.db.add(LedgerMateAiMessage(user_id=h.user, session_id=h.session_id, role="user" if index % 2 == 0 else "assistant", content=str(index), created_at=start + timedelta(seconds=index)))
    await h.db.commit()
    history = await LedgerMateService.get_ai_messages(h.db, h.user, h.session_id, limit=3)
    assert [message.content for message in history] == ["3", "4", "5"]
    assert await LedgerMateService._message_records(h.db, uuid4(), history[-1].id) == []


@pytest.mark.asyncio
async def test_session_lock_and_record_advisory_lock_are_owner_scoped():
    db = SimpleNamespace(scalar=AsyncMock(return_value=object()))
    owner, session_id = uuid4(), uuid4()
    await LedgerMateService.get_ai_session(db, owner, session_id, for_update=True)
    stmt = db.scalar.call_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql and "user_id" in sql and "is_deleted" in sql
    assert owner in stmt.compile().params.values()
    fake_db = SimpleNamespace(get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name="postgresql")), execute=AsyncMock())
    await LedgerMateService._lock_request(fake_db, owner, "request")
    first_key = next(iter(fake_db.execute.call_args.args[0].compile().params.values()))
    await LedgerMateService._lock_request(fake_db, uuid4(), "request")
    other_key = next(iter(fake_db.execute.call_args.args[0].compile().params.values()))
    assert first_key != other_key and -(2 ** 63) <= first_key < 2 ** 63


@pytest.mark.asyncio
async def test_legacy_batch_confirmation_is_atomic(ledger_db):
    h = ledger_db
    first = ready(h)["records"][0]
    second = {**first, "category_id": str(uuid4())}
    with pytest.raises(HTTPException):
        await LedgerMateService.confirm_ai_drafts(h.db, h.user, AiConfirmRequest(drafts=[first, second], idempotency_key="legacy-batch"))
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0
