import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from core.database import get_db
from core.users.models import User
from core.users.dependencies import get_current_user, require_roles, get_current_superuser
from core.response import ResponseModel, PaginatedResponse

from apps.aurakey.schemas import (
    AdminStatsResponse, AdminAdjustBalanceRequest, AdminAdjustBalanceResponse,
    AdminUserStatusUpdate, AdminRefundRequest, AdminRefundResponse, AdminHistoryListItem,
    AdminGalleryCategoryResponse, AdminGalleryCategoryCreate,
    AdminOptionModelResponse, AdminOptionModelCreate,
    AdminOptionRatioResponse, AdminOptionRatioCreate,
    AdminUserListItem, AdminUserDetail,
    AdminProductCreate, AdminProductUpdate, AdminProductResponse
)
from apps.aurakey.models import (
    AurakeyTask, AurakeyGalleryCategory, AurakeyModelOption, AurakeyAspectRatioOption, AurakeyUserAsset
)
from apps.aurakey.admin_services import AurakeyAdminService

router = APIRouter(prefix="/admin", tags=["AuraKey B端管理"])

@router.get("/dashboard/stats", response_model=ResponseModel)
async def get_dashboard_stats(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin"))
):
    stats = await AurakeyAdminService.get_dashboard_stats(db)
    return ResponseModel(data=AdminStatsResponse(**stats))

@router.post("/user/adjust-balance", response_model=ResponseModel)
async def adjust_user_balance(
    req: AdminAdjustBalanceRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin"))
):
    res = await AurakeyAdminService.adjust_balance(db, req.user_id, req.amount, req.remark)
    return ResponseModel(data=AdminAdjustBalanceResponse(**res))

@router.put("/user/{user_id}/status", response_model=ResponseModel)
async def update_user_status(
    user_id: uuid.UUID,
    req: AdminUserStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin"))
):
    status = await AurakeyAdminService.toggle_ban(db, user_id, req.status)
    return ResponseModel(data={"currentStatus": status})

@router.post("/order/{order_no}/refund", response_model=ResponseModel)
async def refund_order(
    order_no: str,
    req: AdminRefundRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin"))
):
    res = await AurakeyAdminService.refund_order(db, order_no, req.remark)
    return ResponseModel(data=AdminRefundResponse(**res))

# ====== CRUD for Categories & Options ======

@router.get("/gallery/categories", response_model=ResponseModel)
async def get_categories(db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    result = await db.execute(select(AurakeyGalleryCategory).order_by(desc(AurakeyGalleryCategory.sort)))
    items = result.scalars().all()
    return ResponseModel(data=[AdminGalleryCategoryResponse.model_validate(item, from_attributes=True) for item in items])

@router.post("/gallery/categories", response_model=ResponseModel)
async def create_category(req: AdminGalleryCategoryCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    cat = AurakeyGalleryCategory(**req.model_dump())
    db.add(cat)
    await db.commit()
    return ResponseModel(data={"is_success": True})

@router.get("/task/options/models", response_model=ResponseModel)
async def get_models(db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    result = await db.execute(select(AurakeyModelOption))
    items = result.scalars().all()
    return ResponseModel(data=[AdminOptionModelResponse.model_validate(item, from_attributes=True) for item in items])

@router.post("/task/options/models", response_model=ResponseModel)
async def create_model(req: AdminOptionModelCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    mod = AurakeyModelOption(**req.model_dump(by_alias=False))
    db.add(mod)
    await db.commit()
    return ResponseModel(data={"is_success": True})

@router.get("/task/options/ratios", response_model=ResponseModel)
async def get_ratios(db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    result = await db.execute(select(AurakeyAspectRatioOption).order_by(desc(AurakeyAspectRatioOption.sort)))
    items = result.scalars().all()
    return ResponseModel(data=[AdminOptionRatioResponse.model_validate(item, from_attributes=True) for item in items])

@router.post("/task/options/ratios", response_model=ResponseModel)
async def create_ratio(req: AdminOptionRatioCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    r = AurakeyAspectRatioOption(**req.model_dump())
    db.add(r)
    await db.commit()
    return ResponseModel(data={"is_success": True})


@router.post("/products", response_model=ResponseModel)
async def create_product(req: AdminProductCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    product = await AurakeyAdminService.create_product(db, req)
    return ResponseModel(data=AdminProductResponse.model_validate(product, from_attributes=True))


@router.get("/products/{product_id}", response_model=ResponseModel)
async def get_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    product = await AurakeyAdminService.get_product(db, product_id)
    if not product:
        return ResponseModel(code=404, message="Product not found", data=None)
    return ResponseModel(data=AdminProductResponse.model_validate(product, from_attributes=True))


@router.put("/products/{product_id}", response_model=ResponseModel)
async def update_product(product_id: uuid.UUID, req: AdminProductUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    product = await AurakeyAdminService.update_product(db, product_id, req)
    if not product:
        return ResponseModel(code=404, message="Product not found", data=None)
    return ResponseModel(data=AdminProductResponse.model_validate(product, from_attributes=True))


@router.delete("/products/{product_id}", response_model=ResponseModel)
async def delete_product(product_id: uuid.UUID, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    success = await AurakeyAdminService.delete_product(db, product_id)
    if not success:
        return ResponseModel(code=404, message="Product not found", data=None)
    return ResponseModel(message="Delete successful", data={"is_success": True})
