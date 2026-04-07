"""
资源存储 —— 核心业务逻辑（不含 HTTP 请求处理）
"""
import hashlib
import os
from typing import Optional
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import BadRequestException, NotFoundException
from core.storage.minio_service import MinioService
from core.storage.models import Resource
from core.storage.schemas import ResourceResponse


class StorageService:
    """资源存储服务：上传（含秒传去重）、查询（含动态 URL 签发）"""

    # ==================== 上传 ====================

    @staticmethod
    async def upload(
        db: AsyncSession,
        file: UploadFile,
        owner_id: Optional[UUID] = None,
    ) -> ResourceResponse:
        """
        上传文件到 MinIO 并记录到数据库。
        - 计算 MD5 实现秒传去重：hash 已存在则直接返回旧记录
        - 图片类型自动生成缩略图
        """
        # 1. 读取文件内容
        data = await file.read()
        if not data:
            raise BadRequestException(message="上传文件不能为空")

        # 2. 计算 MD5
        file_hash = hashlib.md5(data).hexdigest()

        # 3. 秒传去重：查找相同 hash 的已有资源
        result = await db.execute(
            select(Resource).where(
                Resource.hash == file_hash,
                Resource.is_deleted == False,
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            return await StorageService._build_response(existing)

        # 4. 解析文件信息
        original_name = file.filename or "unknown"
        content_type = file.content_type or "application/octet-stream"
        ext = original_name.rsplit(".", 1)[-1].lower() if "." in original_name else "bin"
        file_size = len(data)

        # 5. 上传到 MinIO（含缩略图）
        object_path, thumb_path = await MinioService.upload_file(
            data=data,
            ext=ext,
            content_type=content_type,
        )

        # 6. 写入数据库
        resource = Resource(
            name=original_name,
            url=object_path,
            thumb_url=thumb_path,
            size=file_size,
            type=content_type,
            hash=file_hash,
            owner=owner_id,
        )
        db.add(resource)
        await db.commit()
        await db.refresh(resource)

        return await StorageService._build_response(resource)

    # ==================== 查询 ====================

    @staticmethod
    async def get_resource(
        db: AsyncSession,
        resource_id: UUID,
    ) -> ResourceResponse:
        """查询资源详情，动态签发预签名 URL（1 小时有效）"""
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

    # ==================== 内部工具 ====================

    @staticmethod
    async def _build_response(resource: Resource) -> ResourceResponse:
        """将 ORM 对象转为响应模型，动态签发预签名 URL"""
        # 签发主文件 URL
        presigned_url = await MinioService.get_presigned_url(
            object_path=resource.url,
            original_name=resource.name,
        )

        # 签发缩略图 URL（如有）
        presigned_thumb_url = None
        if resource.thumb_url:
            presigned_thumb_url = await MinioService.get_presigned_url(
                object_path=resource.thumb_url,
                original_name=f"thumb_{resource.name}",
            )

        return ResourceResponse(
            id=resource.id,
            name=resource.name,
            url=presigned_url,
            thumb_url=presigned_thumb_url,
            size=resource.size,
            type=resource.type,
            hash=resource.hash,
            owner=resource.owner,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )
