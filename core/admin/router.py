"""
管理后台路由
所有接口均需要超级管理员权限（is_superuser=True）
"""
import math

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.admin.schemas import (
    AdminAssignRolesRequest,
    AdminFreezeRequest,
    AdminUserDetail,
    AdminUserListItem,
)
from core.admin.services import AdminUserService, RoleService
from core.database import get_db
from core.response import PaginatedData, PaginatedResponse, ResponseModel
from core.roles.schemas import RoleCreate, RoleResponse, RoleUpdate
from core.users.dependencies import get_current_superuser
from core.users.models import User

router = APIRouter(prefix="/admin", tags=["管理后台"])


# ==================== 用户管理 ====================

@router.get("/users", response_model=PaginatedResponse[AdminUserListItem])
async def list_users(
    keyword: str | None = Query(None, description="关键词：昵称/用户名/手机号/openid"),
    is_active: bool | None = Query(None, description="状态筛选"),
    role_code: str | None = Query(None, description="按角色编码筛选"),
    source: str | None = Query(None, description="来源筛选"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(20, ge=1, le=100, description="每页数量"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """
    用户列表（分页）

    - **keyword**: 模糊匹配昵称、用户名、手机号、openid
    - **is_active**: `true` 正常 / `false` 已冻结
    - **role_code**: 精确匹配角色编码
    - **source**: 来源（default / wechat …）
    """
    users, total = await AdminUserService.list_users(
        db,
        keyword=keyword,
        is_active=is_active,
        role_code=role_code,
        source=source,
        page=page,
        page_size=page_size,
    )
    items = [AdminUserListItem.model_validate(u) for u in users]
    return PaginatedResponse(
        data=PaginatedData(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=math.ceil(total / page_size) if total else 0,
        )
    )


@router.get("/users/{user_id}", response_model=ResponseModel[AdminUserDetail])
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """获取用户详情"""
    user = await AdminUserService.get_user_detail(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    return ResponseModel(data=AdminUserDetail.model_validate(user))


@router.patch("/users/{user_id}/freeze", response_model=ResponseModel[AdminUserDetail])
async def freeze_user(
    user_id: int,
    body: AdminFreezeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_superuser),
):
    """
    冻结 / 解冻用户

    - `is_active: false` → 冻结（用户将无法登录）
    - `is_active: true`  → 解冻
    """
    user = await AdminUserService.get_user_detail(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")
    if user.id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="不能对自己执行此操作"
        )

    user = await AdminUserService.set_active(db, user, body.is_active)
    action = "解冻" if body.is_active else "冻结"
    return ResponseModel(
        message=f"用户已{action}",
        data=AdminUserDetail.model_validate(user),
    )


@router.put("/users/{user_id}/roles", response_model=ResponseModel[AdminUserDetail])
async def assign_user_roles(
    user_id: int,
    body: AdminAssignRolesRequest,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """
    覆盖式分配用户角色

    传入 `role_ids` 列表，将完全替换该用户的当前角色。
    传空列表 `[]` 则清除所有角色。
    """
    user = await AdminUserService.get_user_detail(db, user_id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="用户不存在")

    user = await AdminUserService.assign_roles(db, user, body.role_ids)
    return ResponseModel(
        message="角色更新成功",
        data=AdminUserDetail.model_validate(user),
    )


# ==================== 角色管理 ====================

@router.get("/roles", response_model=ResponseModel[list[RoleResponse]])
async def list_roles(
    scope: str | None = Query(None, description="按 scope 筛选，不传则返回全部"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """获取角色列表（可按 scope 筛选）"""
    roles = await RoleService.get_all(db, scope=scope)
    return ResponseModel(data=[RoleResponse.model_validate(r) for r in roles])


@router.post("/roles", response_model=ResponseModel[RoleResponse], status_code=status.HTTP_201_CREATED)
async def create_role(
    body: RoleCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """创建角色"""
    existing = await RoleService.get_by_scope_code(db, body.scope, body.code)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"scope '{body.scope}' 下角色编码 '{body.code}' 已存在",
        )
    role = await RoleService.create(
        db, name=body.name, code=body.code, scope=body.scope, description=body.description
    )
    return ResponseModel(data=RoleResponse.model_validate(role))


@router.patch("/roles/{role_id}", response_model=ResponseModel[RoleResponse])
async def update_role(
    role_id: int,
    body: RoleUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """更新角色信息"""
    role = await RoleService.get_by_id(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    role = await RoleService.update(
        db,
        role,
        name=body.name,
        description=body.description,
        is_active=body.is_active,
    )
    return ResponseModel(data=RoleResponse.model_validate(role))


@router.delete("/roles/{role_id}", response_model=ResponseModel[None])
async def delete_role(
    role_id: int,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    """删除角色（同时移除所有用户与该角色的关联）"""
    role = await RoleService.get_by_id(db, role_id)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="角色不存在")

    await RoleService.delete(db, role)
    return ResponseModel(message="角色已删除")
