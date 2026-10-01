from sqlalchemy import select
import httpx

from apps.ledger_mate.models import LedgerMateCategory, LedgerMatePaymentMethod, LedgerMateRecord
from apps.ledger_mate.schemas import ImportConfirmRequest, ImportPreviewRequest, RecordCreate
from apps.ledger_mate.services import LedgerMateService
from test_http_contract import test_app


def reference_csv():
    return "时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-09-08,曼谷人民币,支出,旅行,市内交通,14.10,现金,打车\n2026-09-08,曼谷人民币,支出,旅行,市内交通,14.10,现金,打车\n"


async def rows_for(db, model, user_id):
    return (await db.scalars(select(model).where(model.user_id == user_id))).all()


async def test_import_preview_creates_unknown_names_on_confirm_and_preserves_metadata(ledger_db):
    h = ledger_db
    preview = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="账单.csv", content=reference_csv()))
    assert preview["total"] == 2
    assert preview["valid_count"] == 1
    assert preview["error_count"] == 0
    assert preview["duplicate_count"] == 1
    assert preview["rows"][0]["warnings"] == ["确认时将创建此分类"]
    result = await LedgerMateService.confirm_import(h.db, h.user, preview["batch_id"], ImportConfirmRequest(skip_duplicates=True))
    assert result["imported_count"] == 1 and result["skipped_count"] == 1
    records = await rows_for(h.db, LedgerMateRecord, h.user)
    assert len(records) == 1 and records[0].import_batch_id == preview["batch_id"]
    assert "[账本:曼谷人民币]" in records[0].note and "[二级分类:市内交通]" in records[0].note
    categories = await rows_for(h.db, LedgerMateCategory, h.user)
    methods = await rows_for(h.db, LedgerMatePaymentMethod, h.user)
    assert any(item.name == "旅行" for item in categories)
    assert any(item.name == "现金" for item in methods)
    repeated = await LedgerMateService.confirm_import(h.db, h.user, preview["batch_id"], ImportConfirmRequest(skip_duplicates=True))
    assert repeated == result


async def test_import_rejects_errors_and_duplicate_confirmation_without_writing(ledger_db):
    h = ledger_db
    preview = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="bad.csv", content="时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-02-30,,收入,工资,,0,,"))
    assert preview["error_count"] == 1
    try:
        await LedgerMateService.confirm_import(h.db, h.user, preview["batch_id"], ImportConfirmRequest())
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("invalid import must not be confirmed")
    valid = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="dup.csv", content=reference_csv()))
    try:
        await LedgerMateService.confirm_import(h.db, h.user, valid["batch_id"], ImportConfirmRequest())
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
    else:
        raise AssertionError("duplicate import must require explicit skip")
    assert len(await rows_for(h.db, LedgerMateRecord, h.user)) == 0


async def test_import_preview_marks_duplicate_of_existing_record(ledger_db):
    h = ledger_db
    await LedgerMateService.create_record(h.db, h.user, RecordCreate(record_type="expense", amount_cent=1410, category_id=h.expense.id, payment_method_id=h.payment.id, occurred_date="2026-09-08", note="打车"))
    content = "时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-09-08,我的账本,支出,餐饮,,14.10,现金,打车"
    preview = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="dup.csv", content=content))
    assert preview["duplicate_count"] == 1 and preview["rows"][0]["duplicate"] is True


async def test_export_uses_reference_csv_columns_and_decimal_amounts(ledger_db):
    h = ledger_db
    preview = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="one.csv", content="时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-09-08,我的账本,支出,餐饮,,14.10,现金,午餐"))
    await LedgerMateService.confirm_import(h.db, h.user, preview["batch_id"], ImportConfirmRequest())
    exported = await LedgerMateService.export_records(h.db, h.user, None, None, None, None, None, "csv")
    assert exported["content"].startswith("\ufeff时间,账本,类型,分类,二级分类,金额,账户,备注")
    assert "2026-09-08,我的账本,支出,餐饮,,14.10,现金,午餐" in exported["content"]
    exported_json = await LedgerMateService.export_records(h.db, h.user, None, None, None, None, None, "json")
    assert '"amount":"14.10"' in exported_json["content"]


async def test_exported_import_file_is_detected_as_duplicate(ledger_db):
    h = ledger_db
    initial = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name="one.csv", content="时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-09-08,曼谷人民币,支出,餐饮,市内,14.10,现金,午餐"))
    await LedgerMateService.confirm_import(h.db, h.user, initial["batch_id"], ImportConfirmRequest())
    exported = await LedgerMateService.export_records(h.db, h.user, None, None, None, None, None, "csv")
    replay = await LedgerMateService.preview_import(h.db, h.user, ImportPreviewRequest(file_name=exported["file_name"], content=exported["content"]))
    assert replay["duplicate_count"] == 1 and replay["rows"][0]["duplicate"] is True


async def test_http_import_export_contract(ledger_db):
    h = ledger_db
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=test_app(h)), base_url="http://test") as client:
        preview = await client.post("/ledger-mate/import/preview", json={"file_name": "one.csv", "content": "时间,账本,类型,分类,二级分类,金额,账户,备注\n2026-09-08,我的账本,支出,餐饮,,14.10,现金,午餐"})
        assert preview.status_code == 200
        batch_id = preview.json()["data"]["batch_id"]
        confirmed = await client.post(f"/ledger-mate/import/{batch_id}/confirm", json={})
        assert confirmed.status_code == 200
        exported = await client.get("/ledger-mate/export", params={"format": "csv"})
        assert exported.status_code == 200
        assert exported.json()["data"]["record_count"] == 1
