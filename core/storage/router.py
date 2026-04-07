"""
资源存储路由 —— 仅解析请求，调用 service
"""
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel
from core.storage.schemas import ResourceResponse
from core.storage.services import StorageService
from core.users.dependencies import get_current_user
from core.users.models import User

router = APIRouter(prefix="/storage", tags=["资源存储"])


@router.post("/upload", response_model=ResponseModel[ResourceResponse])
async def upload_file(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """上传文件（支持秒传去重，图片自动生成缩略图）"""
    resource = await StorageService.upload(
        db=db,
        file=file,
        owner_id=current_user.id,
    )
    return ResponseModel(data=resource)


@router.get("/{resource_id}", response_model=ResponseModel[ResourceResponse])
async def get_resource(
    resource_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取资源详情（含动态签发的预签名下载 URL，有效期 1 小时）"""
    resource = await StorageService.get_resource(db=db, resource_id=resource_id)
    return ResponseModel(data=resource)
