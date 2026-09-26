from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from apps.ledger_mate.dates import SHANGHAI, midnight, resolve_range
from apps.ledger_mate.models import LedgerMateRecord
from apps.ledger_mate.schemas import AiMessageCreate, RecordCreate, RecordOut, RecordUpdate
from apps.ledger_mate.services import LedgerMateService


def record_data(category_id, **changes):
    return {"record_type": "expense", "amount_cent": 1250, "category_id": category_id, "occurred_date": "2026-09-26", **changes}


def test_date_only_creates_midnight_and_legacy_time_remains_compatible():
    data = RecordCreate(**record_data(uuid4()))
    assert data.occurred_at.isoformat() == "2026-09-26T00:00:00+08:00"
    legacy = RecordCreate(**record_data(uuid4(), occurred_date=None, occurred_at="2026-09-25T17:30:00Z"))
    assert legacy.occurred_at.isoformat() == "2026-09-26T01:30:00+08:00"
    update = RecordUpdate(occurred_date="2026-09-27")
    values = update.model_dump(exclude_unset=True, exclude={"occurred_date"})
    assert values["occurred_at"] == midnight(date(2026, 9, 27))


@pytest.mark.parametrize("changes", [
    {"occurred_date": "2026-09-26T00:00:00"}, {"occurred_date": "2026-02-30"},
    {"occurred_date": None}, {"occurred_at": "2026-09-27T00:00:00+08:00"},
    {"amount_cent": 12.5}, {"amount_cent": True}, {"amount_cent": "1250"},
])
def test_invalid_date_or_non_integer_money_is_rejected(changes):
    with pytest.raises(ValidationError):
        RecordCreate(**record_data(uuid4(), **changes))


def test_update_cannot_clear_required_fields_but_can_clear_optional_values():
    for values in ({}, {"amount_cent": None}, {"category_id": None}, {"occurred_date": None}):
        with pytest.raises(ValidationError):
            RecordUpdate(**values)
    assert RecordUpdate(note=None, payment_method_id=None).model_dump(exclude_unset=True) == {"note": None, "payment_method_id": None}


def test_output_derives_date_in_shanghai_from_historical_utc_time():
    values = dict(id=uuid4(), record_type="expense", amount_cent=1250, category_id=uuid4(), payment_method_id=None,
                  occurred_at=datetime(2026, 9, 25, 17, 30, tzinfo=timezone.utc), note=None, source="manual", created_at=datetime.now(timezone.utc))
    assert RecordOut.model_validate(SimpleNamespace(**values)).occurred_date == date(2026, 9, 26)
    assert RecordOut.model_validate(values).model_dump(mode="json")["occurred_date"] == "2026-09-26"


def test_date_range_is_exclusive_and_rejects_ambiguous_aliases():
    start, end = resolve_range(start_date=date(2026, 9, 1), end_date=date(2026, 10, 1), required=True)
    assert start.isoformat() == "2026-09-01T00:00:00+08:00"
    assert end.isoformat() == "2026-10-01T00:00:00+08:00"
    for kwargs in ({"start_date": date(2026, 9, 1)}, {"start_date": date(2026, 9, 1), "end_date": date(2026, 9, 1)}, {"start_date": date(2026, 9, 1), "start_at": datetime(2026, 9, 2), "end_date": date(2026, 10, 1)}):
        with pytest.raises(ValueError):
            resolve_range(**kwargs, required=True)


def test_client_id_and_content_are_required_and_trimmed():
    assert AiMessageCreate(content="  早餐 12  ", client_message_id=" req-1 ").client_message_id == "req-1"
    for values in ({"content": "午餐"}, {"content": " ", "client_message_id": "id"}, {"content": "午餐", "client_message_id": " "}):
        with pytest.raises(ValidationError):
            AiMessageCreate(**values)


@pytest.mark.asyncio
async def test_date_edit_and_list_order_use_occurred_date_with_exclusive_filter(ledger_db):
    h = ledger_db
    old = await LedgerMateService.create_record(h.db, h.user, RecordCreate(**record_data(h.expense.id, occurred_date="2026-09-26")))
    latest = await LedgerMateService.create_record(h.db, h.user, RecordCreate(**record_data(h.expense.id, occurred_date="2026-09-01")))
    old.created_at = datetime(2026, 9, 25, tzinfo=SHANGHAI)
    latest.created_at = datetime(2026, 9, 26, tzinfo=SHANGHAI)
    await h.db.commit()
    records, total = await LedgerMateService.list_records(h.db, h.user, 1, 20, midnight(date(2026, 9, 1)), midnight(date(2026, 9, 27)), None, None, None)
    assert total == 2
    assert [record.id for record in records] == [old.id, latest.id]
    edited = await LedgerMateService.update_record(h.db, h.user, latest.id, RecordUpdate(occurred_date="2026-09-27", note="已修改"))
    assert RecordOut.model_validate(edited).occurred_date == date(2026, 9, 27)
    records, total = await LedgerMateService.list_records(h.db, h.user, 1, 20, midnight(date(2026, 9, 1)), midnight(date(2026, 9, 27)), None, None, None)
    assert total == 1 and records[0].id == old.id
    records, total = await LedgerMateService.list_records(h.db, h.user, 1, 20, midnight(date(2026, 9, 1)), midnight(date(2026, 9, 28)), None, None, None)
    assert total == 2 and [record.id for record in records] == [latest.id, old.id]


@pytest.mark.asyncio
async def test_record_and_payment_authorization_and_deleted_idempotency(ledger_db):
    h = ledger_db
    with pytest.raises(HTTPException):
        await LedgerMateService.create_record(h.db, h.user, RecordCreate(**record_data(h.expense.id, payment_method_id=uuid4())))
    data = RecordCreate(**record_data(h.expense.id, idempotency_key="manual-once"))
    record = await LedgerMateService.create_record(h.db, h.user, data)
    assert (await LedgerMateService.create_record(h.db, h.user, data)).id == record.id
    with pytest.raises(HTTPException) as denied:
        await LedgerMateService.get_record(h.db, uuid4(), record.id)
    assert denied.value.status_code == 404
    await LedgerMateService.delete_record(h.db, h.user, record.id)
    with pytest.raises(HTTPException) as deleted:
        await LedgerMateService.create_record(h.db, h.user, data)
    assert deleted.value.status_code == 409
    assert await h.db.scalar(select(func.count()).select_from(LedgerMateRecord)) == 1


@pytest.mark.asyncio
async def test_statistics_include_income_categories_counts_and_daily_balances(ledger_db):
    h = ledger_db
    for values in [record_data(h.expense.id), record_data(h.income.id, record_type="income", amount_cent=5000), record_data(h.expense.id, occurred_date="2026-10-01")]:
        await LedgerMateService.create_record(h.db, h.user, RecordCreate(**values))
    stats = await LedgerMateService.statistics(h.db, h.user, midnight(date(2026, 9, 1)), midnight(date(2026, 10, 1)))
    assert (stats["income_cent"], stats["expense_cent"], stats["balance_cent"]) == (5000, 1250, 3750)
    assert (stats["record_count"], stats["income_count"], stats["expense_count"]) == (2, 1, 1)
    assert stats["category_incomes"][0]["name"] == "工资"
    assert stats["category_expenses"][0]["count"] == 1
    assert stats["daily"] == [{"date": "2026-09-26", "income_cent": 5000, "expense_cent": 1250, "count": 2, "balance_cent": 3750}]
    empty = await LedgerMateService.statistics(h.db, uuid4(), midnight(date(2026, 9, 1)), midnight(date(2026, 10, 1)))
    assert empty["record_count"] == 0 and empty["category_incomes"] == []
