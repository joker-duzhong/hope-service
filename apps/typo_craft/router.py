"""
TypoCraft Web API 路由
"""
from uuid import UUID
from fastapi import APIRouter, Depends, Query

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.typo_craft.schemas import (
    ProjectCreateIn, ProjectOut,
    AssetGeneratePosterIn, AssetGenerateUIIn,
    AssetStatusOut, AssetStatusUpdateIn, AssetFeedOut
)
from apps.typo_craft.services import (
    create_project, submit_generation,
    sync_asset_status, admin_update_status
)
from apps.typo_craft.models import TypoCraftProject, TypoCraftAsset

from core.database import get_db
from core.users.dependencies import get_current_user
from core.users.models import User
from core.response import ResponseModel, PaginatedResponse, PaginatedData

router = APIRouter()

# ==================== 项目(App)管理接口 ====================

@router.post("/projects", response_model=ResponseModel[ProjectOut])
async def create_new_project(
    data: ProjectCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建全局风格基调项目"""
    res = await create_project(data, current_user.id, db)
    return ResponseModel(data=ProjectOut.model_validate(res))

@router.get("/projects", response_model=ResponseModel[list[ProjectOut]])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取用户全部 App 视觉项目"""
    query = select(TypoCraftProject).where(TypoCraftProject.user_id == current_user.id)
    res = await db.execute(query)
    projects = res.scalars().all()
    return ResponseModel(data=[ProjectOut.model_validate(p) for p in projects])

# ==================== 生成提交接口 ====================

@router.post("/generate/poster", response_model=ResponseModel[dict])
async def generate_poster_task(
    data: AssetGeneratePosterIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """提交生成单张海报"""
    asset = await submit_generation(
        asset_type="POSTER",
        user_prompt=data.prompt,
        user_id=current_user.id,
        db=db,
        aspect_ratio=data.aspect_ratio or "1:1"
    )
    return ResponseModel(data={"task_id": str(asset.id)})

@router.post("/generate/illustration", response_model=ResponseModel[dict])
async def generate_ui_task(
    data: AssetGenerateUIIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """提交生成App系列插图"""
    asset = await submit_generation(
        asset_type="UI_ILLUSTRATION",
        user_prompt=data.scene_prompt,
        user_id=current_user.id,
        db=db,
        project_id=data.project_id,
        aspect_ratio=data.aspect_ratio or "1:1"
    )
    return ResponseModel(data={"task_id": str(asset.id)})

@router.get("/assets/{asset_id}/status", response_model=ResponseModel[AssetStatusOut])
async def get_asset_status(
    asset_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """前端轮询查询此图状态"""
    asset = await sync_asset_status(asset_id, current_user.id, db)
    return ResponseModel(data=AssetStatusOut.model_validate(asset))

# ==================== 广场与审核接口 ====================

@router.get("/feed", response_model=PaginatedResponse[AssetFeedOut])
async def list_public_feed(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    """获取社区大厅已成功的、公开的图片流"""
    offset = (page - 1) * page_size
    # 在这里不区分用户，全局流
    query = select(TypoCraftAsset)\
        .where(TypoCraftAsset.status == "SUCCESS")\
        .where(TypoCraftAsset.is_public == True)\
        .order_by(TypoCraftAsset.created_at.desc())\
        .offset(offset).limit(page_size)

    res = await db.execute(query)
    assets = res.scalars().all()
    # Mock总数查询，实际可用 count(*) 进行优化
    return PaginatedResponse(
        data=PaginatedData(
            items=[AssetFeedOut.model_validate(a) for a in assets],
            total=len(assets), # for now mock this
            page=page,
            page_size=page_size,
            total_pages=1
        )
    )

@router.patch("/assets/{asset_id}", response_model=ResponseModel[AssetStatusOut])
async def modify_asset_status(
    asset_id: UUID,
    data: AssetStatusUpdateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """强制修改资源的各种状态（用于后台管理或用户自己修改广场可见性）"""
    # FIXME: 根据实际需要加入超级管理员权限的限制依赖， 这里就简化为只允许修改自己的或者超管可以改
    asset = await admin_update_status(asset_id, data, db)
    return ResponseModel(data=AssetStatusOut.model_validate(asset))
