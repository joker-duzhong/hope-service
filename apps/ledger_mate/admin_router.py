"""账伴管理端路由：维护全局分类模板。"""
import uuid

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ledger_mate.schemas import (
    CategoryTemplateCreate,
    CategoryTemplateOut,
    CategoryTemplateUpdate,
)
from apps.ledger_mate.services import LedgerMateService
from core.database import get_db
from core.dependencies import bind_admin_app
from core.response import ResponseModel
from core.users.dependencies import require_roles
from core.users.models import User


router = APIRouter(
    prefix="/admin",
    tags=["账伴管理"],
    dependencies=[Depends(bind_admin_app("hope_ledger_mate"))],
)


@router.get(
    "/categories",
    response_model=ResponseModel[list[CategoryTemplateOut]],
    summary="获取账伴分类模板",
)
async def list_category_templates(
    include_deleted: bool = Query(False, description="是否包含已删除模板"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("ledger_mate_admin")),
):
    items = await LedgerMateService.list_category_templates(db, include_deleted=include_deleted)
    return ResponseModel(data=[CategoryTemplateOut.model_validate(item) for item in items])


@router.post(
    "/categories",
    response_model=ResponseModel[CategoryTemplateOut],
    status_code=status.HTTP_201_CREATED,
    summary="创建账伴分类模板",
)
async def create_category_template(
    data: CategoryTemplateCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("ledger_mate_admin")),
):
    item = await LedgerMateService.create_category_template(db, data)
    return ResponseModel(data=CategoryTemplateOut.model_validate(item), message="分类模板已创建")


@router.put(
    "/categories/{template_id}",
    response_model=ResponseModel[CategoryTemplateOut],
    summary="更新账伴分类模板",
)
async def update_category_template(
    template_id: uuid.UUID,
    data: CategoryTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("ledger_mate_admin")),
):
    item = await LedgerMateService.update_category_template(db, template_id, data)
    return ResponseModel(data=CategoryTemplateOut.model_validate(item), message="分类模板已更新")


@router.delete(
    "/categories/{template_id}",
    response_model=ResponseModel[None],
    summary="删除账伴分类模板",
)
async def delete_category_template(
    template_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("ledger_mate_admin")),
):
    await LedgerMateService.delete_category_template(db, template_id)
    return ResponseModel(message="分类模板已删除")
