"""
资源存储 —— 核心业务逻辑（仅限七牛云）
"""
import hashlib
from typing import Optional, List, Dict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import BadRequestException, NotFoundException
from core.oss.qiniu_client import QiniuClient
from core.storage.models import Resource
from core.storage.schemas import ResourceResponse, ConfirmUploadRequest, TokenResponse
from core.storage.tasks import delete_oss_file_task


class StorageService:
    """资源存储服务：上传 Token 获取、确认上传、CDN URL 拼接、软删除触发异步物理删除"""

    @staticmethod
    def get_upload_token() -> TokenResponse:
        """获取前端直传 Token 和公网访问域名"""
        if not settings.QINIU_DOMAIN:
            raise BadRequestException(message="OSS 域名未配置")
        
        token = QiniuClient.generate_upload_token()
        if not token:
            raise BadRequestException(message="生成上传凭证失败，请检查 OSS 配置")
            
        return TokenResponse(token=token, domain=settings.QINIU_DOMAIN)

    @staticmethod
    async def confirm_upload(
        db: AsyncSession,
        data: ConfirmUploadRequest,
        owner_id: Optional[UUID] = None,
        scope: Optional[str] = None,
    ) -> ResourceResponse:
        """接收前端上传成功后的元数据并落库"""
        # 秒传检查：查找相同 hash 且未删除的记录
        result = await db.execute(
            select(Resource).where(
                Resource.hash == data.hash,
                Resource.is_deleted == False,
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            # 如果已有资源但没有 scope，可以考虑更新它，或者直接返回
            return await StorageService._build_response(existing)

        # 全局资源落库
        resource = Resource(
            name=data.name,
            url=data.url,
            thumb_url=data.thumb_url,
            size=data.size,
            type=data.type,
            hash=data.hash,
            owner=owner_id,
            scope=scope,
        )
        db.add(resource)
        await db.commit()
        await db.refresh(resource)
        return await StorageService._build_response(resource)

    @staticmethod
    async def delete_resource(
        db: AsyncSession,
        resource_id: UUID,
        owner_id: Optional[UUID] = None,
    ) -> bool:
        """
        逻辑删除资源记录 + 触发物理删除
        """
        result = await db.execute(
            select(Resource).where(
                Resource.id == resource_id,
                Resource.is_deleted == False,
            )
        )
        resource = result.scalar_one_or_none()

        if not resource:
            raise NotFoundException(message="资源未找到")

        # 权限检查（非管理员只能删自己的文件，此处简单实现，后续可结合角色系统）
        if owner_id and resource.owner and resource.owner != owner_id:
            raise BadRequestException(message="无权操作此资源")

        # 逻辑删除
        resource.is_deleted = True
        await db.commit()

        # 触发物理清理异步任务
        if resource.url:
            delete_oss_file_task.delay(resource.url)
        if resource.thumb_url:
            delete_oss_file_task.delay(resource.thumb_url)

        return True

    @staticmethod
    async def get_resource(
        db: AsyncSession,
        resource_id: UUID,
    ) -> ResourceResponse:
        """查询资源详情，拼接公网 URL"""
        result = await db.execute(
            select(Resource).where(
                Resource.id == resource_id,
                Resource.is_deleted == False,
            )
        )
        resource = result.scalar_one_or_none()

        if not resource:
            raise NotFoundException(message="资源不存在")

        return await StorageService._build_response(resource)

    @staticmethod
    async def get_file_urls_by_ids(
        db: AsyncSession,
        file_ids: List[UUID]
    ) -> Dict[UUID, str]:
        """
        根据文件 ID 列表批量获取拼接好的公网 URL
        """
        if not file_ids:
            return {}
            
        result = await db.execute(
            select(Resource).where(
                Resource.id.in_(file_ids),
                Resource.is_deleted == False
            )
        )
        resources = result.scalars().all()
        domain = settings.QINIU_DOMAIN.rstrip("/")
        
        return {
            res.id: f"{domain}/{res.url}" if res.url else ""
            for res in resources
        }

    # ==================== 内部工具 ====================

    @staticmethod
    async def _build_response(resource: Resource) -> ResourceResponse:
        """将 ORM 对象转为响应模型，拼接 CDN 地址"""
        domain = settings.QINIU_DOMAIN.rstrip("/") if settings.QINIU_DOMAIN else ""
        
        # 拼接 URL（如果 url 本身是完整的则不拼接，通常数据库只存 key）
        url = f"{domain}/{resource.url}" if resource.url and not resource.url.startswith("http") else resource.url
        thumb_url = None
        if resource.thumb_url:
            thumb_url = f"{domain}/{resource.thumb_url}" if not resource.thumb_url.startswith("http") else resource.thumb_url

        return ResourceResponse(
            id=resource.id,
            name=resource.name,
            url=url,
            thumb_url=thumb_url,
            size=resource.size,
            type=resource.type,
            hash=resource.hash,
            owner=resource.owner,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )
