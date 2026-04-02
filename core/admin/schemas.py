"""
管理后台 Pydantic 进出参模型
"""
from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from core.users.schemas import RoleInfo


# ==================== 管理后台用户模型 ====================

class AdminUserListItem(BaseModel):
    """用户列表条目（简洁版）"""
    id: int
    nickname: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    openid: Optional[str] = None
    source: str
    is_active: bool
    roles: List[RoleInfo] = []
    created_at: datetime

    class Config:
        from_attributes = True


class AdminUserDetail(BaseModel):
    """用户详情（完整版）"""
    id: int
    nickname: Optional[str] = None
    avatar: Optional[str] = None
    username: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    openid: Optional[str] = None
    unionid: Optional[str] = None
    source: str
    is_active: bool
    is_superuser: bool
    roles: List[RoleInfo] = []
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AdminFreezeRequest(BaseModel):
    """冻结/解冻请求"""
    is_active: bool = Field(..., description="True=解冻，False=冻结")
    reason: Optional[str] = Field(None, max_length=200, description="操作原因（可选）")


class AdminAssignRolesRequest(BaseModel):
    """分配角色请求"""
    role_ids: List[int] = Field(..., description="角色 ID 列表，传空列表则清除所有角色")


# ==================== 查询参数 ====================

class UserQueryParams(BaseModel):
    """用户列表查询参数"""
    keyword: Optional[str] = Field(None, description="关键词（昵称/用户名/手机号/openid）")
    is_active: Optional[bool] = Field(None, description="筛选状态：True=正常，False=已冻结")
    role_code: Optional[str] = Field(None, description="按角色编码筛选")
    source: Optional[str] = Field(None, description="按来源筛选")
    page: int = Field(1, ge=1, description="页码")
    page_size: int = Field(20, ge=1, le=100, description="每页数量")
