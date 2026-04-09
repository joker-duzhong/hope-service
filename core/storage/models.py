"""
全局资源表 ORM 模型
表名前缀: core_
"""
import uuid
from typing import Optional

from sqlalchemy import BigInteger, String
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import CoreModel


class Resource(CoreModel):
    """全局资源表 —— 记录所有上传到 MinIO 的文件"""
    __tablename__ = "core_resources"

    # 原始文件名（用于下载时还原）
    name: Mapped[str] = mapped_column(String(500), nullable=False)

    # MinIO 内部存储路径（YYYY/MM/DD/{uuid}.{ext}）
    url: Mapped[str] = mapped_column(String(500), nullable=False)

    # 缩略图路径（仅图片类型有值）
    thumb_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 文件大小（字节）
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # MIME 类型（image/png, application/pdf 等）
    type: Mapped[str] = mapped_column(String(100), nullable=False)

    # 应用标识 (scope)
    scope: Mapped[Optional[str]] = mapped_column(String(50), index=True, nullable=True)

    # 文件 MD5 哈希（用于秒传去重）
    hash: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    # 上传者用户 ID（可选，匿名上传时为空）
    owner: Mapped[Optional[uuid.UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )

    def __repr__(self) -> str:
        return f"<Resource id={self.id} name={self.name}>"
