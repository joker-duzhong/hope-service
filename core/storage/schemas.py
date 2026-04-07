"""
资源存储相关的 Pydantic 进出参模型
"""
from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ResourceResponse(BaseModel):
    """资源响应模型（url 和 thumb_url 为动态签发的预签名地址）"""
    id: UUID
    name: str
    url: str
    thumb_url: Optional[str] = None
    size: int
    type: str
    hash: str
    owner: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
