"""
MinIO 对象存储服务封装
- 上传文件（含缩略图生成）
- 动态签发预签名下载 URL（带原始文件名）
"""
import io
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import quote

from fastapi.concurrency import run_in_threadpool
from minio import Minio

from core.config import settings

# ==================== 全局单例客户端 ====================

minio_client = Minio(
    endpoint=settings.MINIO_ENDPOINT,
    access_key=settings.MINIO_ACCESS_KEY,
    secret_key=settings.MINIO_SECRET_KEY,
    secure=settings.MINIO_SECURE,
)


def _ensure_bucket() -> None:
    """确保存储桶存在（同步，仅在首次上传时调用）"""
    if not minio_client.bucket_exists(settings.MINIO_BUCKET):
        minio_client.make_bucket(settings.MINIO_BUCKET)


class MinioService:
    """MinIO 操作封装 —— 所有同步 IO 均通过 run_in_threadpool 执行"""

    @staticmethod
    def _generate_object_path(ext: str) -> str:
        """生成存储路径：YYYY/MM/DD/{uuid}.{ext}"""
        now = datetime.now()
        date_prefix = now.strftime("%Y/%m/%d")
        filename = f"{uuid.uuid4().hex}.{ext}"
        return f"{date_prefix}/{filename}"

    @staticmethod
    def _sync_upload(object_path: str, data: bytes, content_type: str) -> None:
        """同步上传文件到 MinIO（在线程池中执行）"""
        _ensure_bucket()
        minio_client.put_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_path,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    @staticmethod
    def _sync_generate_thumbnail(data: bytes, max_size: int = 200) -> bytes:
        """同步生成缩略图（在线程池中执行）"""
        from PIL import Image

        img = Image.open(io.BytesIO(data))
        img.thumbnail((max_size, max_size))

        # 统一输出为 JPEG（体积小、兼容好）
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        return buf.getvalue()

    @staticmethod
    def _sync_presign(object_path: str, original_name: str, expires: timedelta) -> str:
        """同步签发带原始文件名的预签名 URL（在线程池中执行）"""
        from urllib.parse import quote as url_quote

        # RFC 5987 编码，支持中文文件名
        encoded_name = url_quote(original_name, safe="")
        disposition = f"attachment; filename*=UTF-8''{encoded_name}"

        return minio_client.presigned_get_object(
            bucket_name=settings.MINIO_BUCKET,
            object_name=object_path,
            expires=expires,
            response_headers={"response-content-disposition": disposition},
        )

    # ==================== 异步公开 API ====================

    @staticmethod
    async def upload_file(
        data: bytes,
        ext: str,
        content_type: str,
    ) -> Tuple[str, Optional[str]]:
        """
        上传文件到 MinIO，如果是图片则同时生成缩略图。

        Returns:
            (object_path, thumb_path) — thumb_path 仅图片类型有值
        """
        object_path = MinioService._generate_object_path(ext)
        await run_in_threadpool(MinioService._sync_upload, object_path, data, content_type)

        # 图片类型 → 生成缩略图
        thumb_path: Optional[str] = None
        if content_type.startswith("image/"):
            try:
                thumb_data = await run_in_threadpool(
                    MinioService._sync_generate_thumbnail, data
                )
                thumb_path = MinioService._generate_object_path("jpg")
                await run_in_threadpool(
                    MinioService._sync_upload, thumb_path, thumb_data, "image/jpeg"
                )
            except Exception:
                # 缩略图生成失败不阻断主流程
                thumb_path = None

        return object_path, thumb_path

    @staticmethod
    async def get_presigned_url(
        object_path: str,
        original_name: str,
        expires: timedelta = timedelta(hours=1),
    ) -> str:
        """生成带原始文件名的预签名下载 URL"""
        return await run_in_threadpool(
            MinioService._sync_presign, object_path, original_name, expires
        )
