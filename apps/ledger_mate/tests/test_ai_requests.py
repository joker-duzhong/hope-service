"""独立后台会话、幂等、租约恢复与安全失败的隔离回归。"""
import json
from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.ledger_mate import ai_requests, services
from apps.ledger_mate.ai_requests import AiRequestService
from apps.ledger_mate.dates import SHANGHAI
from apps.ledger_mate.models import LedgerMateAiMessage, LedgerMateAiRecordReference, LedgerMateCategory, LedgerMateOperationLog, LedgerMateRecord
from apps.ledger_mate.schemas import AiMessageCreate
from apps.ledger_mate.services import LedgerMateService


def data(client_id="request"):
    return AiMessageCreate(content="午餐 28 元", client_message_id=client_id)


def ready(h):
    return json.dumps({"status": "ready", "records": [{"record_type": "expense", "amount_cent": 2800, "category_id": str(h.expense.id), "occurred_date": "2026-09-30"}], "questions": []})


async def expire(db, message_id, **updates):
    message = await db.get(LedgerMateAiMessage, message_id, populate_existing=True)
    message.payload = {**message.payload, **updates}
    await db.commit()


@pytest.mark.asyncio
async def test_accept_commits_before_model_and_independent_worker_saves_once(ledger_db, monkeypatch):
    h = ledger_db
    generate = AsyncMock(return_value=ready(h))
    monkeypatch.setattr(services, "generate_chat", generate)
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    assert accepted.status == "queued" and accepted.assistant_message is None
    generate.assert_not_awaited()
    maker = async_sessionmaker(h.db.bind, expire_on_commit=False)
    async with maker() as worker:
        assert (await AiRequestService.get(worker, h.user, h.session_id, "request")).status == "queued"
        assert await AiRequestService.process(worker, message_id) == "completed"
    async with maker() as reopened:
        state = await AiRequestService.get(reopened, h.user, h.session_id, "request")
        assert state.status == "completed"
        assert state.assistant_message.payload["auto_saved"] is True
        repeated = await AiRequestService.accept(reopened, h.user, h.session_id, data())
        assert repeated.assistant_message.id == state.assistant_message.id
        assert await AiRequestService.process(reopened, message_id) == "completed"
        assert await reopened.scalar(select(func.count()).select_from(LedgerMateRecord)) == 1
        assert await reopened.scalar(select(func.count()).select_from(LedgerMateAiMessage)) == 2
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_pending_is_owner_scoped_and_new_messages_conflict_across_old_and_new_routes(ledger_db):
    h = ledger_db
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    again = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    assert again.user_message.id == accepted.user_message.id
    assert len(await AiRequestService.list_pending(h.db, h.user)) == 1
    assert await AiRequestService.list_pending(h.db, uuid4()) == []
    for operation in (
        lambda: AiRequestService.accept(h.db, h.user, h.session_id, data("next")),
        lambda: LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data("legacy-next")),
        lambda: LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data()),
        lambda: AiRequestService.accept(h.db, h.user, h.session_id, AiMessageCreate(content="晚餐 36", client_message_id="request")),
    ):
        with pytest.raises(HTTPException) as error:
            await operation()
        assert error.value.status_code == 409
    with pytest.raises(HTTPException) as denied:
        await AiRequestService.get(h.db, uuid4(), h.session_id, "request")
    assert denied.value.status_code == 404


@pytest.mark.asyncio
async def test_processing_is_readable_without_transaction_and_duplicate_worker_cannot_claim(ledger_db, monkeypatch):
    h = ledger_db
    result = ready(h)
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    maker = async_sessionmaker(h.db.bind, expire_on_commit=False)
    async with maker() as worker:
        async def generate(*args, **kwargs):
            assert not worker.in_transaction()
            async with maker() as reader:
                state = await AiRequestService.get(reader, h.user, h.session_id, "request")
                assert state.status == "processing"
                assert await AiRequestService.process(reader, message_id) == "processing"
                assert await AiRequestService.recover(reader) == []
            return result

        monkeypatch.setattr(services, "generate_chat", generate)
        assert await AiRequestService.process(worker, message_id) == "completed"


@pytest.mark.asyncio
async def test_failures_are_bounded_persisted_and_same_request_can_retry(ledger_db, monkeypatch, caplog):
    h = ledger_db
    result = ready(h)
    generate = AsyncMock(side_effect=RuntimeError("mock-private-secret"))
    monkeypatch.setattr(services, "generate_chat", generate)
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    for expected in ("queued", "queued", "failed"):
        assert await AiRequestService.process(h.db, message_id) == expected
        if expected == "queued":
            assert await AiRequestService.recover(h.db) == []
            await expire(h.db, message_id, retry_after="2020-01-01T00:00:00+08:00")
            assert await AiRequestService.recover(h.db) == [message_id]
    failed = await AiRequestService.get(h.db, h.user, h.session_id, "request")
    assert failed.error_message
    assert "mock-private-secret" not in failed.error_message + caplog.text
    assert failed.user_message.payload["attempts"] == 3
    assert await AiRequestService.process(h.db, message_id) == "failed"
    assert await AiRequestService.recover(h.db) == []
    assert generate.await_count == 3
    monkeypatch.setattr(services, "generate_chat", AsyncMock(return_value=result))
    retried = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    assert retried.user_message.id == message_id
    assert await AiRequestService.process(h.db, message_id) == "completed"
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 1


@pytest.mark.asyncio
async def test_recovery_finds_missed_dispatch_and_expired_leases_and_stops_exhausted_work(ledger_db):
    h = ledger_db
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    assert await AiRequestService.recover(h.db) == [message_id]
    await expire(h.db, message_id, request_status="processing", attempts=3, lease_token="lost-worker", lease_expires_at="2020-01-01T00:00:00+08:00")
    assert await AiRequestService.recover(h.db) == [message_id]
    assert await AiRequestService.process(h.db, message_id) == "failed"
    assert await AiRequestService.recover(h.db) == []


@pytest.mark.asyncio
async def test_atomic_completion_failure_keeps_accepted_message_and_does_not_partially_save(ledger_db, monkeypatch):
    h = ledger_db
    monkeypatch.setattr(services, "generate_chat", AsyncMock(return_value=ready(h)))
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    save_message = LedgerMateService._save_message

    async def fail_assistant(db, user_id, session_id, role, content, payload=None):
        if role == "assistant":
            raise RuntimeError("simulated persistence failure")
        return await save_message(db, user_id, session_id, role, content, payload)

    monkeypatch.setattr(LedgerMateService, "_save_message", fail_assistant)
    assert await AiRequestService.process(h.db, message_id) == "queued"
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateAiMessage)) == 1
    for model in (LedgerMateRecord, LedgerMateOperationLog, LedgerMateAiRecordReference):
        assert await h.db.scalar(select(func.count()).select_from(model)) == 0
    monkeypatch.setattr(LedgerMateService, "_save_message", save_message)
    await expire(h.db, message_id, retry_after="2020-01-01T00:00:00+08:00")
    assert await AiRequestService.process(h.db, message_id) == "completed"
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 1


@pytest.mark.asyncio
async def test_prompt_uses_acceptance_date_and_excludes_current_or_failed_input(ledger_db, monkeypatch):
    h = ledger_db
    result = ready(h)
    h.db.add(LedgerMateAiMessage(user_id=h.user, session_id=h.session_id, role="user", content="失败的旧消息", payload={"request_status": "failed"}, created_at=datetime.now(SHANGHAI) - timedelta(days=1)))
    await h.db.commit()
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    await expire(h.db, message_id, accepted_at="2026-09-29T23:59:59+08:00")
    calls = []

    async def generate(messages, **kwargs):
        calls.append(messages)
        return result

    monkeypatch.setattr(services, "generate_chat", generate)
    assert await AiRequestService.process(h.db, message_id) == "completed"
    context = json.loads(calls[0][0]["content"].split("上下文：\n")[1])
    assert context["current_date"] == "2026-09-29"
    assert context["history"] == []
    assert context["user_input"] == data().content


@pytest.mark.asyncio
async def test_stale_worker_cannot_commit_after_another_lease_claim(ledger_db, monkeypatch):
    h = ledger_db
    result = ready(h)
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    message_id = accepted.user_message.id
    maker = async_sessionmaker(h.db.bind, expire_on_commit=False)

    async def generate(*args, **kwargs):
        async with maker() as replacement:
            await expire(replacement, message_id, lease_token="replacement-worker")
        return result

    monkeypatch.setattr(services, "generate_chat", generate)
    assert await AiRequestService.process(h.db, message_id) == "processing"
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0


@pytest.mark.asyncio
async def test_existing_synchronous_result_is_returned_as_completed(ledger_db, monkeypatch):
    h = ledger_db
    generate = AsyncMock(return_value=ready(h))
    monkeypatch.setattr(services, "generate_chat", generate)
    legacy = await LedgerMateService.chat_with_ai(h.db, h.user, h.session_id, data())
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    assert accepted.status == "completed"
    assert accepted.assistant_message.id == legacy[2].id
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_clarification_completes_without_records_and_unlocks_next_message(ledger_db, monkeypatch):
    h = ledger_db
    monkeypatch.setattr(services, "generate_chat", AsyncMock(return_value=json.dumps({"status": "needs_clarification", "records": [], "questions": ["金额是多少？"]})))
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    assert await AiRequestService.process(h.db, accepted.user_message.id) == "completed"
    state = await AiRequestService.get(h.db, h.user, h.session_id, "request")
    assert state.assistant_message.payload["status"] == "needs_clarification"
    assert await AiRequestService.list_pending(h.db, h.user) == []
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0
    assert (await AiRequestService.accept(h.db, h.user, h.session_id, data("followup"))).status == "queued"


@pytest.mark.asyncio
async def test_category_disabled_during_model_processing_prevents_commit(ledger_db, monkeypatch):
    h = ledger_db
    result = ready(h)
    category_id = h.expense.id
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    maker = async_sessionmaker(h.db.bind, expire_on_commit=False)

    async def generate(*args, **kwargs):
        async with maker() as admin:
            category = await admin.get(LedgerMateCategory, category_id)
            category.is_enabled = False
            await admin.commit()
        return result

    monkeypatch.setattr(services, "generate_chat", generate)
    assert await AiRequestService.process(h.db, accepted.user_message.id) == "queued"
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0


@pytest.mark.asyncio
async def test_model_timeout_releases_processing_state_for_recovery(ledger_db, monkeypatch):
    h = ledger_db

    async def slow_generate(*args, **kwargs):
        await ai_requests.asyncio.sleep(1)

    monkeypatch.setattr(services, "generate_chat", slow_generate)
    monkeypatch.setattr(ai_requests, "PROCESS_TIMEOUT_SECONDS", 0.001)
    accepted = await AiRequestService.accept(h.db, h.user, h.session_id, data())
    assert await AiRequestService.process(h.db, accepted.user_message.id) == "queued"
    state = await AiRequestService.get(h.db, h.user, h.session_id, "request")
    assert "lease_token" not in state.user_message.payload
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 0
