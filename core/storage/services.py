"""
资源存储 —— 核心业务逻辑（仅限七牛云）
"""
import hashlib
import io
import mimetypes
import uuid
from datetime import datetime, timezone
from typing import Optional, List, Dict
from urllib.parse import unquote, urlparse
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from PIL import Image, UnidentifiedImageError

from core.config import settings
from core.exceptions import BadRequestException, NotFoundException
from core.oss.qiniu_client import QiniuClient
from core.storage.models import Resource
from core.storage.schemas import (
    ResourceResponse,
    ConfirmUploadRequest,
    ServerImageCompressionOptions,
    ServerImageThumbnailOptions,
    TokenResponse,
)
from core.storage.tasks import delete_oss_file_task


MIME_EXTENSION_MAP = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/heic": ".heic",
    "image/heif": ".heif",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "video/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "application/pdf": ".pdf",
}

COMPRESSIBLE_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


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
    async def upload_remote_file(
        db: AsyncSession,
        remote_url: str,
        owner_id: Optional[UUID] = None,
        scope: Optional[str] = None,
        name: Optional[str] = None,
        compression: Optional[ServerImageCompressionOptions] = None,
        thumbnail: Optional[ServerImageThumbnailOptions] = None,
        timeout: float = 60.0,
        max_bytes: int = 30 * 1024 * 1024,
    ) -> ResourceResponse:
        """下载远程文件并通过服务端上传到 OSS，返回资源记录。"""
        if not remote_url or not remote_url.startswith(("http://", "https://")):
            raise BadRequestException(message="远程文件地址无效")
        if not settings.QINIU_DOMAIN:
            raise BadRequestException(message="OSS 域名未配置")

        file_bytes, mime_type, file_name = await StorageService._download_remote_file(
            remote_url=remote_url,
            name=name,
            timeout=timeout,
            max_bytes=max_bytes,
        )
        compression_options = compression or ServerImageCompressionOptions()
        thumbnail_options = thumbnail or ServerImageThumbnailOptions()

        upload_bytes = file_bytes
        upload_mime_type = mime_type
        if StorageService._is_compressible_image(mime_type):
            upload_bytes, upload_mime_type = StorageService._compress_image(
                file_bytes,
                mime_type,
                compression_options,
            )

        file_hash = hashlib.md5(upload_bytes).hexdigest()
        existing = await StorageService._get_existing_by_hash(db, file_hash)
        if existing:
            return await StorageService._build_response(existing)

        object_key = StorageService._build_object_key(upload_mime_type, file_name)
        qiniu_result = QiniuClient.upload_bytes_to_oss(object_key, upload_bytes, upload_mime_type)
        finalized_key = StorageService._get_qiniu_key(qiniu_result, object_key)

        thumb_key = None
        if StorageService._is_compressible_image(upload_mime_type):
            thumb_bytes, thumb_mime_type = StorageService._create_thumbnail(
                upload_bytes,
                upload_mime_type,
                thumbnail_options,
            )
            if thumb_bytes:
                thumb_key_candidate = StorageService._build_object_key(
                    thumb_mime_type,
                    file_name,
                    suffix="thumb",
                )
                thumb_result = QiniuClient.upload_bytes_to_oss(
                    thumb_key_candidate,
                    thumb_bytes,
                    thumb_mime_type,
                )
                thumb_key = StorageService._get_qiniu_key(thumb_result, thumb_key_candidate)

        resource = Resource(
            name=file_name,
            url=finalized_key,
            thumb_url=thumb_key,
            size=len(upload_bytes),
            type=upload_mime_type,
            hash=file_hash,
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
        resource = await StorageService.get_resource_response_or_none(db, resource_id)

        if not resource:
            raise NotFoundException(message="资源不存在")

        return resource

    @staticmethod
    async def get_resource_response_or_none(
        db: AsyncSession,
        resource_id: UUID,
    ) -> Optional[ResourceResponse]:
        """查询资源详情，资源不存在时返回 None"""
        result = await db.execute(
            select(Resource).where(
                Resource.id == resource_id,
                Resource.is_deleted == False,
            )
        )
        resource = result.scalar_one_or_none()

        if not resource:
            return None

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

    @staticmethod
    async def get_resources_by_ids(
        db: AsyncSession,
        file_ids: List[UUID],
    ) -> Dict[UUID, ResourceResponse]:
        """根据资源 ID 列表批量获取资源结构。"""
        if not file_ids:
            return {}

        result = await db.execute(
            select(Resource).where(
                Resource.id.in_(file_ids),
                Resource.is_deleted == False,
            )
        )
        resources = result.scalars().all()
        return {
            resource.id: await StorageService._build_response(resource)
            for resource in resources
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
            scope=resource.scope,
            hash=resource.hash,
            owner=resource.owner,
            created_at=resource.created_at,
            updated_at=resource.updated_at,
        )

    @staticmethod
    async def _get_existing_by_hash(db: AsyncSession, file_hash: str) -> Optional[Resource]:
        result = await db.execute(
            select(Resource).where(
                Resource.hash == file_hash,
                Resource.is_deleted == False,
            )
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def _download_remote_file(
        remote_url: str,
        name: Optional[str],
        timeout: float,
        max_bytes: int,
    ) -> tuple[bytes, str, str]:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "hope-service-storage/1.0"},
        ) as client:
            async with client.stream("GET", remote_url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise BadRequestException(message="远程文件过大，无法上传")
                    chunks.append(chunk)

        file_bytes = b"".join(chunks)
        if not file_bytes:
            raise BadRequestException(message="远程文件为空")

        file_name = name or StorageService._guess_file_name(remote_url, content_type)
        mime_type = content_type or StorageService._guess_mime_type(file_name) or "application/octet-stream"
        return file_bytes, mime_type, file_name

    @staticmethod
    def _guess_file_name(remote_url: str, mime_type: str) -> str:
        path = unquote(urlparse(remote_url).path)
        file_name = path.rsplit("/", 1)[-1] if path else ""
        if not file_name or "." not in file_name:
            file_name = f"remote{StorageService._get_extension(mime_type)}"
        return file_name[:500]

    @staticmethod
    def _guess_mime_type(file_name: str) -> Optional[str]:
        mime_type, _encoding = mimetypes.guess_type(file_name)
        return mime_type

    @staticmethod
    def _is_compressible_image(mime_type: str) -> bool:
        return mime_type.lower() in COMPRESSIBLE_IMAGE_TYPES

    @staticmethod
    def _compress_image(
        file_bytes: bytes,
        mime_type: str,
        options: ServerImageCompressionOptions,
    ) -> tuple[bytes, str]:
        if not options.enabled:
            return file_bytes, mime_type

        output_mime_type = options.file_type or mime_type
        if output_mime_type not in COMPRESSIBLE_IMAGE_TYPES:
            output_mime_type = "image/jpeg"

        try:
            image = Image.open(io.BytesIO(file_bytes))
            image.load()
        except UnidentifiedImageError:
            return file_bytes, mime_type

        image = StorageService._resize_image(image, options.max_width_or_height)
        return StorageService._encode_image_under_limit(
            image,
            output_mime_type,
            options.initial_quality,
            int(options.max_size_mb * 1024 * 1024),
        ), output_mime_type

    @staticmethod
    def _create_thumbnail(
        file_bytes: bytes,
        mime_type: str,
        options: ServerImageThumbnailOptions,
    ) -> tuple[Optional[bytes], Optional[str]]:
        if not options.enabled:
            return None, None

        try:
            image = Image.open(io.BytesIO(file_bytes))
            image.load()
        except UnidentifiedImageError:
            return None, None

        max_side = max(image.size)
        if not max_side or max_side <= options.max_width_or_height:
            return None, None

        output_mime_type = options.file_type or mime_type
        if output_mime_type not in COMPRESSIBLE_IMAGE_TYPES:
            output_mime_type = "image/jpeg"
        image = StorageService._resize_image(image, options.max_width_or_height)
        return (
            StorageService._encode_image(image, output_mime_type, options.quality),
            output_mime_type,
        )

    @staticmethod
    def _resize_image(image: Image.Image, max_width_or_height: int) -> Image.Image:
        max_side = max(image.size)
        if max_side > max_width_or_height:
            image = image.copy()
            image.thumbnail((max_width_or_height, max_width_or_height), Image.Resampling.LANCZOS)
        return image

    @staticmethod
    def _encode_image_under_limit(
        image: Image.Image,
        mime_type: str,
        initial_quality: float,
        max_size_bytes: int,
    ) -> bytes:
        quality = int(initial_quality * 100)
        encoded = StorageService._encode_image(image, mime_type, quality / 100)
        while len(encoded) > max_size_bytes and quality > 45 and mime_type in {"image/jpeg", "image/webp"}:
            quality -= 10
            encoded = StorageService._encode_image(image, mime_type, quality / 100)
        return encoded

    @staticmethod
    def _encode_image(image: Image.Image, mime_type: str, quality: float) -> bytes:
        output = io.BytesIO()
        save_kwargs = {}
        if mime_type == "image/png":
            image = image.convert("RGBA") if image.mode not in {"RGB", "RGBA"} else image
            format_name = "PNG"
            save_kwargs["optimize"] = True
        else:
            image = image.convert("RGB") if image.mode not in {"RGB"} else image
            format_name = "WEBP" if mime_type == "image/webp" else "JPEG"
            save_kwargs.update({"quality": int(quality * 100), "optimize": True})
        image.save(output, format=format_name, **save_kwargs)
        return output.getvalue()

    @staticmethod
    def _build_object_key(mime_type: str, file_name: str, suffix: Optional[str] = None) -> str:
        now = datetime.now(timezone.utc)
        extension = StorageService._get_extension(mime_type) or StorageService._get_file_extension(file_name)
        variant = f"_{suffix}" if suffix else ""
        return f"{now:%Y/%m/%d}/{uuid.uuid4().hex}{variant}{extension}"

    @staticmethod
    def _get_extension(mime_type: str) -> str:
        return MIME_EXTENSION_MAP.get(mime_type.lower()) or mimetypes.guess_extension(mime_type) or ""

    @staticmethod
    def _get_file_extension(file_name: str) -> str:
        if "." not in file_name:
            return ""
        return file_name[file_name.rfind("."):].lower()

    @staticmethod
    def _get_qiniu_key(result: dict, fallback_key: str) -> str:
        key = result.get("key")
        return key if isinstance(key, str) and key else fallback_key
