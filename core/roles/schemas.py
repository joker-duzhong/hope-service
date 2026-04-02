"""
角色相关的 Pydantic 进出参模型
"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RoleBase(BaseModel):
    scope: str = Field(
        "global",
        max_length=50,
        description="作用域，区分不同业务产线。全局角色用 'global'，具体 APP 用对应 app code",
    )
    name: str = Field(..., min_length=1, max_length=50, description="角色名称")
    code: str = Field(..., min_length=1, max_length=50, description="角色编码，同 scope 内唯一")
    description: Optional[str] = Field(None, max_length=200, description="角色描述")


class RoleCreate(RoleBase):
    """创建角色"""
    pass


class RoleUpdate(BaseModel):
    """更新角色"""
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = Field(None, max_length=200)
    is_active: Optional[bool] = None


class RoleResponse(BaseModel):
    """角色响应"""
    id: int
    scope: str
    name: str
    code: str
    description: Optional[str] = None
    is_active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
