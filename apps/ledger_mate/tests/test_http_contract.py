"""加载实际响应类型与异常处理器；鉴权、配置和模型请求均隔离。"""
import importlib.util
import json
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from apps.ledger_mate import services
from core.llm.errors import ChatGenerationError


async def placeholder_dependency():
    raise AssertionError("test must override dependency")


async def placeholder_database():
    raise AssertionError("test must override database")


class TestUser(BaseModel):
    id: UUID


sys.modules["core.database"].get_db = placeholder_database
dependencies = types.ModuleType("core.users.dependencies")
dependencies.get_current_user = placeholder_dependency
user_models = types.ModuleType("core.users.models")
user_models.User = TestUser
sys.modules.update({"core.users.dependencies": dependencies, "core.users.models": user_models})
service_root = Path(os.environ.get("LEDGER_TEST_SERVICE_ROOT", str(Path(__file__).resolve().parents[3])))
spec = importlib.util.spec_from_file_location("core.response", service_root / "core" / "response.py")
response_types = importlib.util.module_from_spec(spec)
sys.modules["core.response"] = response_types
spec.loader.exec_module(response_types)
exception_spec = importlib.util.spec_from_file_location("core.exceptions", service_root / "core" / "exceptions.py")
exception_handlers = importlib.util.module_from_spec(exception_spec)
sys.modules["core.exceptions"] = exception_handlers
exception_spec.loader.exec_module(exception_handlers)
from apps.ledger_mate import router as routes


def test_app(h):
    app = FastAPI()
    exception_handlers.register_exception_handlers(app)
    app.include_router(routes.router, prefix="/ledger-mate")

    async def user():
        return SimpleNamespace(id=h.user)

    async def database():
        yield h.db

    app.dependency_overrides[routes.get_current_user] = user
    app.dependency_overrides[routes.get_db] = database
    return app


test_app.__test__ = False
TestUser.__test__ = False


@pytest.mark.asyncio
async def test_http_date_only_create_filter_statistics_and_legacy_dates(ledger_db):
    h = ledger_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app(h)), base_url="http://test") as client:
        created = await client.post("/ledger-mate/records", json={"record_type": "expense", "amount_cent": 2800, "category_id": str(h.expense.id), "occurred_date": "2026-09-26"})
        assert created.status_code == 200
        assert created.json()["data"]["occurred_date"] == "2026-09-26"
        record_id = created.json()["data"]["id"]
        edited = await client.put(f"/ledger-mate/records/{record_id}", json={"occurred_date": "2026-09-25"})
        assert edited.status_code == 200 and edited.json()["data"]["occurred_date"] == "2026-09-25"
        response = await client.get("/ledger-mate/records", params={"start_date": "2026-09-25", "end_date": "2026-09-26"})
        assert response.status_code == 200 and response.json()["data"]["total"] == 1
        stats = await client.get("/ledger-mate/statistics", params={"start_date": "2026-09-25", "end_date": "2026-09-26"})
        assert stats.status_code == 200
        assert stats.json()["data"]["record_count"] == 1
        assert stats.json()["data"]["start_date"] == "2026-09-25"
        legacy = await client.get("/ledger-mate/statistics", params={"start_at": "2026-09-25T00:00:00+08:00", "end_at": "2026-09-26T00:00:00+08:00"})
        assert legacy.status_code == 200 and legacy.json()["data"]["expense_cent"] == 2800
        missing = await client.get("/ledger-mate/statistics")
        assert missing.status_code == 400
        invalid = await client.get("/ledger-mate/statistics", params={"start_date": "2026-09-25T00:00:00", "end_date": "2026-09-26"})
        assert invalid.status_code == 422


@pytest.mark.asyncio
async def test_http_ai_message_shape_and_latest_history(ledger_db, monkeypatch):
    h = ledger_db
    calls = []

    async def llm(messages, **kwargs):
        calls.append(messages)
        return json.dumps({"status": "ready", "records": [{"record_type": "expense", "amount_cent": 1250, "category_id": str(h.expense.id), "occurred_date": "2026-09-26", "note": "早餐"}], "questions": []})

    monkeypatch.setattr(services, "generate_chat", llm)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app(h)), base_url="http://test") as client:
        path = f"/ledger-mate/ai/sessions/{h.session_id}/messages"
        invalid = await client.post(path, json={"content": "早餐 12.5"})
        assert invalid.status_code == 422
        request = {"content": "早餐 12.5", "client_message_id": "http-once"}
        first = await client.post(path, json=request)
        assert first.status_code == 200
        data = first.json()["data"]
        assert data["assistant_message"]["records"][0]["occurred_date"] == "2026-09-26"
        assert data["user_message"]["payload"]["client_message_id"] == "http-once"
        repeated = await client.post(path, json=request)
        assert repeated.json()["data"]["assistant_message"]["id"] == data["assistant_message"]["id"]
        history = await client.get(path, params={"limit": 1})
        assert history.status_code == 200 and len(history.json()["data"]) == 1
        assert history.json()["data"][0]["role"] == "assistant"
        assert len(calls) == 1
        messages = calls[0]
        assert any(message["role"] == "system" for message in messages)
        user_messages = [message for message in messages if message["role"] == "user"]
        assert user_messages and request["content"] in user_messages[-1]["content"]
        assert any("json" in message["content"].lower() for message in messages if isinstance(message.get("content"), str))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["timeout", "http", None])
async def test_http_ai_failure_preserves_envelope_and_safe_message(ledger_db, monkeypatch, kind):
    h = ledger_db
    if kind is None:
        error = RuntimeError("mock-private-provider-detail")
        expected_message = "记账助手暂时没有回应，请稍后重试这条消息"
    else:
        diagnostics = {"http_status": 503, "message_category": "channel_unavailable", "error_message": "No available channel for this model."} if kind == "http" else {}
        error = ChatGenerationError(kind, diagnostics)
        expected_message = str(error)
    monkeypatch.setattr(services, "generate_chat", AsyncMock(side_effect=error))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app(h)), base_url="http://test") as client:
        response = await client.post(
            f"/ledger-mate/ai/sessions/{h.session_id}/messages",
            json={"content": "今天午饭35元", "client_message_id": "http-failed-message"},
        )
    assert response.status_code == 502
    assert response.json() == {"code": 502, "message": expected_message, "data": None}
    assert "mock-private-provider-detail" not in response.text
