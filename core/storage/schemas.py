"""
资源存储相关的 Pydantic 进出参模型
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ResourceResponse(BaseModel):
    """资源响应模型（url 和 thumb_url 为拼接后的 CDN 地址）"""
    id: UUID
    name: str
    url: str
    thumb_url: Optional[str] = None
    size: int
    type: str
    scope: Optional[str] = None
    hash: str
    owner: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConfirmUploadRequest(BaseModel):
    """确认文件上传请求参数"""
    name: str = Field(..., description="文件名")
    url: str = Field(..., description="OSS 内部路径/Key")
    thumb_url: Optional[str] = Field(None, description="缩略图内部路径/Key")
    size: int = Field(..., description="文件大小（Byte）")
    type: str = Field(..., description="MIME 类型")
    hash: str = Field(..., description="文件哈希")


class TokenResponse(BaseModel):
    """上传 Token 响应"""
    token: str
    domain: str
