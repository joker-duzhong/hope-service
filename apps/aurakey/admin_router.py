import uuid
import math
from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from core.database import get_db
from core.users.models import User
from core.users.dependencies import require_roles
from core.response import ResponseModel, PaginatedData, PaginatedResponse

from apps.aurakey.schemas import (
    AdminStatsResponse, AdminAdjustBalanceRequest, AdminAdjustBalanceResponse,
    AdminUserStatusUpdate, AdminRefundRequest, AdminRefundResponse, AdminHistoryListItem,
    AdminGalleryCategoryCreate,
    AdminOptionModelResponse, AdminOptionModelCreate,
    AdminOptionRatioResponse, AdminOptionRatioCreate,
    AdminUserListItem, AdminUserDetail,
    AdminProductCreate, AdminProductUpdate, AdminProductResponse,
    AurakeySystemConfigResponse, AurakeySystemConfigUpdate,
    AdminGalleryListItem, AdminTaskPublishBatchResponse, AdminTaskPublishBatchUpdateRequest,
    AdminTaskPublishStatusUpdate, AdminTaskPublishUpdateRequest, TaskPublishStateResponse,
)
from apps.aurakey.models import (
    AurakeyTask, AurakeyGalleryCategory, AurakeyModelOption, AurakeyAspectRatioOption, AurakeyUserAsset
)
from apps.aurakey.admin_services import AurakeyAdminService
from apps.aurakey.services import AurakeyService

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

@router.get("/gallery/list", response_model=PaginatedResponse[AdminGalleryListItem])
async def get_admin_gallery_list(
    publishStatus: Optional[str] = Query(default=None, description="审核状态：approved / blocked"),
    isPublished: Optional[bool] = Query(default=None, description="是否公开"),
    categoryId: Optional[uuid.UUID] = Query(default=None, description="画廊分类 ID"),
    userId: Optional[uuid.UUID] = Query(default=None, description="作者用户 ID"),
    keyword: Optional[str] = Query(default=None, description="关键词：提示词/模型/用户信息"),
    page: int = Query(1, ge=1, description="页码"),
    pageSize: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin")),
):
    total, items = await AurakeyService.get_admin_gallery_list(
        db,
        page,
        pageSize,
        publish_status=publishStatus,
        is_published=isPublished,
        category_id=categoryId,
        user_id=userId,
        keyword=keyword,
    )
    return PaginatedResponse(
        data=PaginatedData(
            items=[AdminGalleryListItem(**item) for item in items],
            total=total,
            page=page,
            page_size=pageSize,
            total_pages=math.ceil(total / pageSize) if total else 0,
        )
    )


@router.post("/gallery/categories", response_model=ResponseModel)
async def create_category(req: AdminGalleryCategoryCreate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    cat = AurakeyGalleryCategory(**req.model_dump())
    db.add(cat)
    await db.commit()
    return ResponseModel(data={"is_success": True})


@router.put("/gallery/{task_id}/status", response_model=ResponseModel)
async def update_gallery_status(
    task_id: uuid.UUID,
    req: AdminTaskPublishStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin")),
):
    res = await AurakeyService.update_task_publish_review_status(db, task_id, req.publish_status)
    return ResponseModel(data=TaskPublishStateResponse(**res))


@router.put("/gallery/{task_id}/publish", response_model=ResponseModel[TaskPublishStateResponse])
async def update_gallery_publish_state(
    task_id: uuid.UUID,
    req: AdminTaskPublishUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin")),
):
    res = await AurakeyService.update_task_publish_state_by_admin(db, task_id, req.is_published, req.category_id)
    return ResponseModel(data=TaskPublishStateResponse(**res))


@router.put("/gallery/publish/batch", response_model=ResponseModel[AdminTaskPublishBatchResponse])
async def batch_update_gallery_publish_state(
    req: AdminTaskPublishBatchUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_roles("aurakey_admin")),
):
    res = await AurakeyService.batch_update_task_publish_state_by_admin(
        db,
        req.task_ids,
        req.is_published,
        req.category_id,
    )
    return ResponseModel(data=AdminTaskPublishBatchResponse(**res))


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


@router.get("/system/config", response_model=ResponseModel)
async def get_system_config(db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    config = await AurakeyService.get_system_config(db)
    return ResponseModel(data=AurakeySystemConfigResponse(**config))


@router.put("/system/config", response_model=ResponseModel)
async def update_system_config(req: AurakeySystemConfigUpdate, db: AsyncSession = Depends(get_db), _: User = Depends(require_roles("aurakey_admin"))):
    current = await AurakeyService.get_system_config(db)
    update_data = req.model_dump(exclude_unset=True)
    current.update(update_data)
    config = await AurakeyService.save_system_config(db, current)
    return ResponseModel(data=AurakeySystemConfigResponse(**config))
