"""
资源存储路由 —— 仅解析请求，调用 service
"""
from uuid import UUID

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.dependencies import get_app_key
from core.response import ResponseModel
from core.storage.schemas import ResourceResponse, ConfirmUploadRequest, TokenResponse
from core.storage.services import StorageService
from core.users.dependencies import get_current_user
from core.users.models import User

router = APIRouter(prefix="/storage", tags=["资源存储"])


@router.get("/upload-token", response_model=ResponseModel[TokenResponse])
async def get_upload_token():
    """获取七牛云直传 Token"""
    token_resp = StorageService.get_upload_token()
    return ResponseModel(data=token_resp)


@router.post("/confirm-upload", response_model=ResponseModel[ResourceResponse])
async def confirm_upload(
    data: ConfirmUploadRequest,
    current_user: User = Depends(get_current_user),
    scope: str = Depends(get_app_key),
    db: AsyncSession = Depends(get_db),
):
    """确认文件上传（落库元数据并支持秒传）"""
    resource = await StorageService.confirm_upload(
        db=db,
        data=data,
        owner_id=current_user.id,
        scope=scope,
    )
    return ResponseModel(data=resource)


@router.delete("/delete/{resource_id}", response_model=ResponseModel[bool])
async def delete_resource(
    resource_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """逻辑删除文件并触发 OSS 物理删除异步任务"""
    success = await StorageService.delete_resource(
        db=db,
        resource_id=resource_id,
        owner_id=current_user.id
    )
    return ResponseModel(data=success)


@router.get("/{resource_id}", response_model=ResponseModel[ResourceResponse])
async def get_resource(
    resource_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取资源详情（含拼接好的 CDN 访问 URL）"""
    resource = await StorageService.get_resource(db=db, resource_id=resource_id)
    return ResponseModel(data=resource)
