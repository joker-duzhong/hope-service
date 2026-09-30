"""队列调度隔离测试：不加载真实配置、不连接 Redis 或业务数据库。"""

import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from celery.exceptions import Retry
from sqlalchemy import event, literal, select
from sqlalchemy.pool import NullPool

from apps.ledger_mate import tasks
from core.apps_config import REGISTERED_APPS


@pytest.fixture
def celery_stub(monkeypatch):
    app = SimpleNamespace(send_task=Mock(), conf=SimpleNamespace())
    module = ModuleType("worker.celery_app")
    module.celery_app = app
    monkeypatch.setitem(sys.modules, "worker.celery_app", module)
    return app


def test_enqueue_sends_only_persisted_message_identifier(celery_stub):
    message_id = uuid4()
    assert tasks.enqueue_ai_request(message_id) is True
    celery_stub.send_task.assert_called_once_with(tasks.PROCESS_TASK_NAME, args=[str(message_id)], retry=False)


def test_enqueue_failure_is_safe_and_allows_database_recovery(celery_stub, caplog):
    celery_stub.send_task.side_effect = ConnectionError("private-broker-connection-details")
    message_id = uuid4()
    with caplog.at_level(logging.WARNING):
        assert tasks.enqueue_ai_request(message_id) is False
    assert str(message_id) in caplog.text
    assert "ConnectionError" in caplog.text
    assert "private-broker-connection-details" not in caplog.text


@pytest.mark.parametrize("status", ["completed", "failed", "missing", "processing"])
def test_terminal_or_leased_request_does_not_reschedule(monkeypatch, status):
    process = AsyncMock(return_value=status)
    publish = Mock()
    monkeypatch.setattr(tasks, "_process_ai_request", process)
    monkeypatch.setattr(tasks.process_ai_request, "apply_async", publish)
    message_id = uuid4()
    assert tasks.process_ai_request.run(str(message_id)) == status
    process.assert_awaited_once_with(message_id)
    publish.assert_not_called()


def test_retryable_persisted_request_is_rescheduled_after_backoff(monkeypatch):
    monkeypatch.setattr(tasks, "_process_ai_request", AsyncMock(return_value="queued"))
    publish = Mock()
    monkeypatch.setattr(tasks.process_ai_request, "apply_async", publish)
    message_id = str(uuid4())
    assert tasks.process_ai_request.run(message_id) == "queued"
    publish.assert_called_once_with(args=[message_id], countdown=20, retry=False)


def test_retry_publish_failure_leaves_persisted_request_for_beat(monkeypatch, caplog):
    monkeypatch.setattr(tasks, "_process_ai_request", AsyncMock(return_value="queued"))
    publish = Mock(side_effect=ConnectionError("private-broker-connection-details"))
    monkeypatch.setattr(tasks.process_ai_request, "apply_async", publish)
    with caplog.at_level(logging.WARNING):
        assert tasks.process_ai_request.run(str(uuid4())) == "queued"
    assert "private-broker-connection-details" not in caplog.text


def test_unexpected_worker_failure_has_bounded_safe_retry(monkeypatch, caplog):
    monkeypatch.setattr(tasks, "_process_ai_request", AsyncMock(side_effect=RuntimeError("private-db-details")))
    retry = Mock(side_effect=Retry())
    monkeypatch.setattr(tasks.process_ai_request, "retry", retry)
    with caplog.at_level(logging.WARNING), pytest.raises(Retry):
        tasks.process_ai_request.run(str(uuid4()))
    assert retry.call_args.kwargs["countdown"] == 20
    assert str(retry.call_args.kwargs["exc"]) == "记账任务暂时无法处理"
    assert tasks.process_ai_request.max_retries == 3
    assert tasks.process_ai_request.acks_late is True
    assert tasks.process_ai_request.reject_on_worker_lost is True
    assert "private-db-details" not in caplog.text


def test_invalid_job_identifier_is_not_retried(monkeypatch):
    process = AsyncMock()
    monkeypatch.setattr(tasks, "_process_ai_request", process)
    assert tasks.process_ai_request.run("not-a-message-id") == "invalid"
    process.assert_not_awaited()


def test_recovery_continues_when_one_dispatch_fails(monkeypatch):
    ids = [uuid4(), uuid4(), uuid4()]
    recover = AsyncMock(return_value=ids)
    dispatch = Mock(side_effect=[True, False, True])
    monkeypatch.setattr(tasks, "_recover_ai_requests", recover)
    monkeypatch.setattr(tasks, "enqueue_ai_request", dispatch)
    assert tasks.recover_ai_requests.run() == {"pending": 3, "dispatched": 2}
    recover.assert_awaited_once_with(100)
    assert [call.args[0] for call in dispatch.call_args_list] == ids


def test_recovery_infrastructure_failure_does_not_expose_connection_details(monkeypatch, caplog):
    monkeypatch.setattr(tasks, "_recover_ai_requests", AsyncMock(side_effect=RuntimeError("private-db-details")))
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="恢复扫描暂时不可用") as error:
        tasks.recover_ai_requests.run()
    assert error.value.__context__ is None
    assert "private-db-details" not in caplog.text


@pytest.mark.asyncio
async def test_each_worker_uses_own_nullpool_engine_and_disposes_it(monkeypatch):
    config = ModuleType("core.config")
    config.settings = SimpleNamespace(DATABASE_URL="sqlite+aiosqlite:///:memory:")
    monkeypatch.setitem(sys.modules, "core.config", config)
    engines = []
    for _ in range(2):
        async with tasks._worker_session() as db:
            engine = db.bind
            engines.append(engine)
            assert isinstance(engine.pool, NullPool)
            assert await db.scalar(select(literal(1))) == 1
            disposed = Mock()
            event.listen(engine.sync_engine, "engine_disposed", disposed)
        disposed.assert_called_once_with(engine.sync_engine)
    assert engines[0] is not engines[1]


@pytest.mark.asyncio
async def test_worker_engine_is_disposed_after_failure(monkeypatch):
    config = ModuleType("core.config")
    config.settings = SimpleNamespace(DATABASE_URL="sqlite+aiosqlite:///:memory:")
    monkeypatch.setitem(sys.modules, "core.config", config)
    with pytest.raises(RuntimeError, match="simulated failure"):
        async with tasks._worker_session() as db:
            engine = db.bind
            disposed = Mock()
            event.listen(engine.sync_engine, "engine_disposed", disposed)
            raise RuntimeError("simulated failure")
    disposed.assert_called_once_with(engine.sync_engine)


def load_schedule():
    service_root = Path(os.environ.get("LEDGER_TEST_SERVICE_ROOT", str(Path(__file__).resolve().parents[3])))
    spec = importlib.util.spec_from_file_location("ledger_test_scheduler", service_root / "worker" / "scheduler.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def test_tasks_registered_and_recovery_scheduled_each_minute(celery_stub):
    assert "apps.ledger_mate.tasks" in REGISTERED_APPS["hope_ledger_mate"].task_modules
    load_schedule()
    entry = celery_stub.conf.beat_schedule["ledger_mate_recover_ai_requests"]
    assert entry["task"] == tasks.RECOVER_TASK_NAME
    assert entry["schedule"].minute == set(range(60))


def test_recovery_schedule_respects_disabled_application(celery_stub, monkeypatch):
    monkeypatch.setattr(REGISTERED_APPS["hope_ledger_mate"], "is_active", False)
    load_schedule()
    assert "ledger_mate_recover_ai_requests" not in celery_stub.conf.beat_schedule
