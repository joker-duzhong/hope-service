import pytest
from sqlalchemy import select

from apps.ledger_mate.models import LedgerMateCategory, LedgerMateCategoryTemplate
from apps.ledger_mate.schemas import CategoryTemplateCreate, CategoryTemplateUpdate
from apps.ledger_mate.services import LedgerMateService


@pytest.mark.asyncio
async def test_global_template_sync_preserves_user_category_id_and_updates_icon(ledger_db):
    h = ledger_db
    old_id = h.expense.id
    template = LedgerMateCategoryTemplate(
        record_type="expense",
        name="餐饮",
        icon="https://cdn.example.test/food.png",
        sort_order=9,
        is_enabled=True,
    )
    db = h.db
    db.add(template)
    await db.commit()

    await LedgerMateService.sync_category_templates(db, h.user)
    await db.commit()
    db.expire_all()
    category = await db.get(LedgerMateCategory, old_id)

    assert category.id == old_id
    assert category.icon == "https://cdn.example.test/food.png"
    assert category.sort_order == 9


@pytest.mark.asyncio
async def test_category_template_crud_and_disable_sync(ledger_db):
    h = ledger_db
    db = h.db
    created = await LedgerMateService.create_category_template(
        db,
        CategoryTemplateCreate(
            record_type="expense",
            name="宠物",
            icon="https://cdn.example.test/pet.png",
            sort_order=20,
        ),
    )
    assert created.name == "宠物"
    assert any(item.id == created.id for item in await LedgerMateService.list_category_templates(db))

    updated = await LedgerMateService.update_category_template(
        db,
        created.id,
        CategoryTemplateUpdate(is_enabled=False, sort_order=30),
    )
    assert updated.is_enabled is False

    await LedgerMateService.sync_category_templates(db, h.user)
    category = await db.scalar(
        select(LedgerMateCategory).where(
            LedgerMateCategory.user_id == h.user,
            LedgerMateCategory.name == "宠物",
        )
    )
    assert category is not None
    assert category.is_enabled is False

    await LedgerMateService.delete_category_template(db, created.id)
    assert all(item.id != created.id for item in await LedgerMateService.list_category_templates(db))
