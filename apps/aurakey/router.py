import time
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Path, Query

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func

from core.database import get_db
from core.users.dependencies import get_current_user, get_optional_user
from core.users.models import User
from core.response import ResponseModel, PaginatedResponse, PaginatedData
from core.storage.services import StorageService

from apps.aurakey.schemas import (
    DictModelOption, GalleryCategorySchema, GalleryItemSchema, GalleryDetailSchema,
    TaskGenerateRequest, TaskStreamGenerateRequest, TaskGenerateResponse, TaskStatusResponse, TaskOptionsResponse,
    UserProfileResponse, AssetLogItem, ProductItem,
    OrderCreateRequest, OrderCreateResponse, OrderStatusResponse,
    TaskHistoryItem, InviteInfoResponse, BindInviteRequest, BindInviteResponse,
    SignInResponse, UserEntitlementResponse, PurchaseOrderItem,
    AurakeySystemConfigResponse, TaskPublishUpdateRequest, TaskPublishStateResponse,
)
from apps.aurakey.services import AurakeyService
from apps.aurakey.models import AurakeyGalleryCategory, AurakeyTask, AurakeyAssetLog, AurakeyProduct, AurakeyOrder, AurakeyUserAsset, AurakeyModelOption, AurakeyAspectRatioOption

from core.pay.schemas import WechatPayMiniRequest
from core.pay.wechat_pay import WechatPayClient

router = APIRouter()

# 1. 发现页 / 画廊模块

@router.get(
    "/gallery/list",
    response_model=PaginatedResponse[GalleryItemSchema],
    summary="获取画廊列表",
    description="发现页画廊列表，允许未登录访问。已登录时会返回当前用户的点赞状态，可按分类筛选。",
)
async def get_gallery_list(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    categoryId: Optional[uuid.UUID] = Query(default=None, description="画廊分类 ID，不传则返回全部分类作品"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    user_id = current_user.id if current_user else None
    total, items = await AurakeyService.get_gallery_list(db, page, pageSize, user_id, categoryId)
    total_pages = (total + pageSize - 1) // pageSize if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=pageSize, total_pages=total_pages)
    )

@router.get(
    "/gallery/categories",
    response_model=ResponseModel[List[GalleryCategorySchema]],
    summary="获取画廊分类列表",
    description="获取画廊可用分类，允许未登录访问。返回的分类 ID 可用于画廊列表 categoryId 参数。",
)
async def get_gallery_categories(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AurakeyGalleryCategory)
        .where(AurakeyGalleryCategory.is_deleted == False)
        .order_by(desc(AurakeyGalleryCategory.sort))
    )
    items = result.scalars().all()
    return ResponseModel(data=[GalleryCategorySchema.model_validate(item, from_attributes=True) for item in items])


@router.get(
    "/gallery/{id}",
    response_model=ResponseModel[GalleryDetailSchema],
    summary="获取画廊作品详情",
    description="获取单个画廊作品详情，允许未登录访问。访问成功后浏览量会增加。",
)
async def get_gallery_detail(
    id: uuid.UUID = Path(..., description="作品 ID"),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_optional_user),
):
    user_id = current_user.id if current_user else None
    detail = await AurakeyService.get_gallery_detail(db, id, user_id)
    return ResponseModel(data=detail)

@router.post(
    "/gallery/{id}/like",
    response_model=ResponseModel[dict],
    summary="点赞或取消点赞画廊作品",
    description="登录用户切换作品点赞状态。已点赞时再次调用会取消点赞。",
)
async def toggle_gallery_like(
    id: uuid.UUID = Path(..., description="作品 ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.toggle_like(db, id, current_user.id)
    return ResponseModel(data=res)


@router.put(
    "/gallery/{id}/publish",
    response_model=ResponseModel[TaskPublishStateResponse],
    summary="变更作品公开状态",
    description="登录用户可切换自己任务作品的公开状态，并可同步设置分类。",
)
async def update_gallery_publish_state(
    req: TaskPublishUpdateRequest,
    id: uuid.UUID = Path(..., description="作品 ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await AurakeyService.update_task_publish_state(db, id, current_user.id, req.is_published, req.category_id)
    return ResponseModel(data=TaskPublishStateResponse(**res))


# 2. 核心创作与扣费模块

@router.post(
    "/task/generate",
    response_model=ResponseModel[TaskGenerateResponse],
    summary="提交生图任务",
    description="登录用户提交生图任务，系统会校验模型权限和算力余额，并返回任务 ID。",
)
async def generate_task(
    req: TaskGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.submit_generate_task(db, req, current_user.id)
    return ResponseModel(data=res)

@router.post(
    "/task/generate-stream",
    response_model=ResponseModel[TaskGenerateResponse],
    summary="提交流式生图任务",
    description="登录用户提交流式生图任务，后端后台接收上游流式结果并更新任务状态。",
)
async def generate_stream_task(
    req: TaskStreamGenerateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.submit_stream_generate_task(db, req, current_user.id)
    return ResponseModel(data=res)

@router.get(
    "/task/status/{task_id}",
    response_model=ResponseModel[TaskStatusResponse],
    summary="查询生图任务状态",
    description="查询当前登录用户的生图任务进度、结果图或失败原因。",
)
async def check_task_status(
    task_id: uuid.UUID = Path(..., description="任务 ID"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.get_task_status(db, task_id, current_user.id)
    return ResponseModel(data=res)

@router.get(
    "/task/options",
    response_model=ResponseModel[TaskOptionsResponse],
    summary="获取生图配置项",
    description="获取可用模型和图片比例配置，允许未登录访问。",
)
async def get_task_options(db: AsyncSession = Depends(get_db)):
    models_result = await db.execute(select(AurakeyModelOption).where(AurakeyModelOption.status == "on"))
    ratios_result = await db.execute(
        select(AurakeyAspectRatioOption)
        .where(AurakeyAspectRatioOption.status == "on", AurakeyAspectRatioOption.is_deleted == False)
        .order_by(desc(AurakeyAspectRatioOption.sort))
    )
    
    models = [DictModelOption.model_validate(m) for m in models_result.scalars().all()]
    ratios = [r.ratio for r in ratios_result.scalars().all()]
    
    res = {
        "models": models if models else [{"model_id": "pro_1", "name": "专业版 v1.0", "cost": 10, "is_vip_only": False}],
        "aspect_ratios": ratios if ratios else ["1:1", "4:3", "3:4", "16:9", "9:16"]
    }
    return ResponseModel(data=res)

# 3. 资产与钱包模块

@router.get("/user/profile", response_model=ResponseModel[UserProfileResponse])
async def get_user_profile(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    entitlement = await AurakeyService.get_user_entitlement(db, current_user.id)
    res = {
        "user_id": current_user.id,
        "openid": current_user.openid,
        "nickname": current_user.nickname,
        "avatar": current_user.avatar,
        "phone": current_user.phone,
        "balance": entitlement["remaining_points"],
        "is_vip": entitlement["is_vip"],
        "type": entitlement["vip_type"],
        "vip_expire_time": entitlement["vip_expire_time"],
        "vip_level": entitlement["vip_level"],
    }
    return ResponseModel(data=res)


@router.get("/user/entitlement", response_model=ResponseModel[UserEntitlementResponse])
async def get_user_entitlement(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entitlement = await AurakeyService.get_user_entitlement(db, current_user.id)
    return ResponseModel(data=UserEntitlementResponse(**entitlement))

@router.get("/asset/logs", response_model=PaginatedResponse[AssetLogItem])
async def get_asset_logs(
    page: int = 1,
    pageSize: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(AurakeyAssetLog).where(AurakeyAssetLog.user_id == current_user.id).order_by(desc(AurakeyAssetLog.created_at)).offset((page-1)*pageSize).limit(pageSize)
    logs = (await db.execute(stmt)).scalars().all()
    total = await db.scalar(select(func.count()).select_from(AurakeyAssetLog).where(AurakeyAssetLog.user_id == current_user.id))
    
    items = [AssetLogItem.model_validate(l) for l in logs]
    
    total_pages = (total + pageSize - 1) // pageSize if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=pageSize, total_pages=total_pages)
    )

# 4. 商品与订单模块

@router.get("/products", response_model=ResponseModel[List[ProductItem]])
async def get_products(db: AsyncSession = Depends(get_db)):
    stmt = select(AurakeyProduct).where(AurakeyProduct.is_deleted == False)
    products = (await db.execute(stmt)).scalars().all()
    # Use Pydantic's from_attributes functionality to serialize the ORM models
    items = [ProductItem.model_validate(p) for p in products]
    return ResponseModel(data=items)

@router.post("/order/create", response_model=ResponseModel[OrderCreateResponse])
async def create_order(
    req: OrderCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    product = await db.get(AurakeyProduct, req.product_id)
    if not product:
        raise HTTPException(status_code=404, detail="商品不存在")
    if product.type not in {"point_pack", "vip"}:
        raise HTTPException(status_code=400, detail="未知的商品类型")

    order_no = f"OD{int(time.time()*1000)}{current_user.id.hex[:4]}"
    config = await AurakeyService.get_system_config(db)
    valid_days = AurakeyService._resolve_product_valid_days(product, config=config)

    order = AurakeyOrder(
        user_id=current_user.id,
        order_no=order_no,
        product_id=product.id,
        amount=product.price,
        product_name=product.name,
        product_type=product.type,
        vip_type=(product.vip_type or product.tag or product.name or "VIP") if product.type == "vip" else None,
        vip_level=product.vip_level or 0,
        point_amount=product.point_amount or 0,
        bonus_amount=product.bonus_amount or 0,
        valid_days=valid_days,
        granted_points=(product.point_amount or 0) + (product.bonus_amount or 0),
    )
    db.add(order)
    await db.commit()

    wechat_client = WechatPayClient()
    wechat_req = WechatPayMiniRequest(
        order_id=order_no,
        amount=product.price,
        subject=product.name,
        openid=req.openid
    )
    pay_res = await wechat_client.create_mini_program_order(wechat_req)
    if not pay_res.success:
        import logging
        logger = logging.getLogger(__name__)
        logger.error(f"微信支付下单失败 - 订单号: {order_no}, 用户ID: {current_user.id}, 商品ID: {product.id}, openid: {req.openid}, 错误信息: {pay_res.message}")
        raise HTTPException(status_code=400, detail=pay_res.message)

    return ResponseModel(data={"order_no": order_no, "pay_params": pay_res.pay_data})

@router.get("/order/status/{order_no}", response_model=ResponseModel[OrderStatusResponse])
async def get_order_status(
    order_no: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    order = await db.scalar(select(AurakeyOrder).where(AurakeyOrder.order_no == order_no, AurakeyOrder.user_id == current_user.id))
    if not order:
        raise HTTPException(status_code=404, detail="订单不存在")
        
    return ResponseModel(data={"order_no": order.order_no, "status": order.status})


@router.get("/orders", response_model=PaginatedResponse[PurchaseOrderItem])
async def get_purchase_orders(
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    pageSize: int = Query(20, ge=1, le=100, description="每页条数，最大 100"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    total, items = await AurakeyService.get_purchase_orders(db, current_user.id, page, pageSize)
    total_pages = (total + pageSize - 1) // pageSize if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(items=[PurchaseOrderItem(**item) for item in items], total=total, page=page, page_size=pageSize, total_pages=total_pages)
    )


@router.get("/system/config", response_model=ResponseModel[AurakeySystemConfigResponse])
async def get_public_system_config(db: AsyncSession = Depends(get_db)):
    config = await AurakeyService.get_system_config(db)
    return ResponseModel(data=AurakeySystemConfigResponse(**config))

# 5. 个人中心与裂变模块

@router.get("/user/history", response_model=PaginatedResponse[TaskHistoryItem])
async def get_user_history(
    page: int = 1,
    pageSize: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(AurakeyTask).where(AurakeyTask.user_id == current_user.id, AurakeyTask.is_deleted == False).order_by(desc(AurakeyTask.created_at)).offset((page-1)*pageSize).limit(pageSize)
    tasks = (await db.execute(stmt)).scalars().all()
    total = await db.scalar(select(func.count()).select_from(AurakeyTask).where(AurakeyTask.user_id == current_user.id, AurakeyTask.is_deleted == False))
    resource_map = await StorageService.get_resources_by_ids(
        db,
        [task.image_resource_id for task in tasks if task.image_resource_id],
    )
    reference_resource_map = await AurakeyService._get_task_reference_image_map(db, tasks)
    average_duration_seconds = await AurakeyService._get_recent_average_task_duration_seconds(db, current_user.id)
    progress_changed = False
    for task in tasks:
        old_progress = task.progress
        await AurakeyService.resolve_task_progress(
            db,
            task,
            average_duration_seconds=average_duration_seconds,
        )
        progress_changed = progress_changed or task.progress != old_progress
    if progress_changed:
        await db.commit()
    
    items = [{
        "task_id": t.id,
        "resource": resource_map.get(t.image_resource_id) if t.image_resource_id else None,
        "reference_images_ids": AurakeyService._task_reference_image_ids(t),
        "reference_images": [
            reference_resource_map[resource_id]
            for resource_id in AurakeyService._task_reference_image_ids(t)
            if resource_id in reference_resource_map
        ],
        "prompt": t.prompt,
        "status": t.status,
        "progress": t.progress,
        "cost": t.cost,
        "is_published": t.is_published,
        "publish_status": t.publish_status,
        "category_id": t.category_id,
        "aspect_ratio": t.aspect_ratio,
        "model_name": t.model_name,
    } for t in tasks]
    total_pages = (total + pageSize - 1) // pageSize if total > 0 else 0
    return PaginatedResponse(
        data=PaginatedData(items=items, total=total, page=page, page_size=pageSize, total_pages=total_pages)
    )

@router.post("/user/history/{task_id}/publish", response_model=ResponseModel[dict])
async def publish_history(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.publish_history_task(db, task_id, current_user.id, current_user.username, current_user.avatar)
    return ResponseModel(data=res)


@router.put("/user/history/{task_id}/publish", response_model=ResponseModel[TaskPublishStateResponse])
async def update_history_publish_state(
    task_id: uuid.UUID,
    req: TaskPublishUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    res = await AurakeyService.update_task_publish_state(db, task_id, current_user.id, req.is_published, req.category_id)
    return ResponseModel(data=TaskPublishStateResponse(**res))

@router.delete("/user/history/{task_id}", response_model=ResponseModel[bool])
async def delete_history(
    task_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    task = await db.get(AurakeyTask, task_id)
    if task and task.user_id == current_user.id:
        task.is_deleted = True
        await db.commit()
    return ResponseModel(data=True)

@router.get("/user/invite-info", response_model=ResponseModel[InviteInfoResponse])
async def get_invite_info(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    asset = await AurakeyService.get_or_create_user_asset(db, current_user.id)
    config = await AurakeyService.get_system_config(db)
    invite_reward = int(config.get("invite_reward_points", 50) or 0)
    res = {
        "invite_code": asset.invite_code,
        "invited_count": asset.invited_count,
        "total_reward_points": asset.total_reward_points,
        "rule_text": f"每邀请1位新用户注册，双方各得 {invite_reward} 点算力"
    }
    return ResponseModel(data=res)

@router.post("/user/bind-invite", response_model=ResponseModel[BindInviteResponse])
async def bind_invite(
    req: BindInviteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    asset = await AurakeyService.get_or_create_user_asset(db, current_user.id)
    if asset.invited_by_id or asset.invite_code == req.invite_code:
        return ResponseModel(data={"is_success": False, "reward_points": 0}, message="无法绑定或已绑定过")

    inviter = await db.scalar(select(AurakeyUserAsset).where(AurakeyUserAsset.invite_code == req.invite_code))
    if not inviter:
        return ResponseModel(data={"is_success": False, "reward_points": 0}, message="邀请码无效")

    config = await AurakeyService.get_system_config(db)
    reward_points = int(config.get("invite_reward_points", 50) or 0)
    asset.invited_by_id = inviter.user_id
    await AurakeyService._credit_points(
        db,
        asset,
        reward_points,
        description="填写邀请码奖励",
        source_type="invite",
        source_id=inviter.id,
    )

    inviter.invited_count += 1
    inviter.total_reward_points += reward_points
    await AurakeyService._credit_points(
        db,
        inviter,
        reward_points,
        description="邀请新用户奖励",
        source_type="invite",
        source_id=asset.id,
    )

    db.add(AurakeyAssetLog(user_id=asset.user_id, type=5, amount=reward_points, balance_after=asset.balance, description="填写邀请码奖励"))
    db.add(AurakeyAssetLog(user_id=inviter.user_id, type=5, amount=reward_points, balance_after=inviter.balance, description="邀请新用户奖励"))

    await db.commit()
    return ResponseModel(data={"is_success": True, "reward_points": reward_points})


# 6. 签到模块

@router.post("/user/sign-in", response_model=ResponseModel[SignInResponse])
async def daily_sign_in(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    res = await AurakeyService.daily_sign_in(db, current_user.id)
    return ResponseModel(data=res)

# ================= Admin Router Include =================
from apps.aurakey.admin_router import router as admin_router
router.include_router(admin_router)
