"""
角色 ORM 表结构
表名前缀: core_
"""
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, String, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.associations import user_roles_table
from core.database import CoreModel

if TYPE_CHECKING:
    from core.users.models import User


class Role(CoreModel):
    """角色表"""
    __tablename__ = "core_roles"
    __table_args__ = (
        # 同一 scope 下，code 不能重复
        UniqueConstraint("scope", "code", name="uq_role_scope_code"),
    )

    # 作用域：区分不同业务产线的角色
    # "global"     — 全局角色（如后台管理员）
    # "hope_care"  — 业务产线 A 专属角色
    # "hope_trade" — 业务产线 B 专属角色
    scope: Mapped[str] = mapped_column(
        String(50), nullable=False, default="global", index=True, comment="作用域"
    )

    # 角色标识
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="角色名称")
    code: Mapped[str] = mapped_column(
        String(50), index=True, nullable=False, comment="角色编码（同 scope 内唯一）"
    )
    description: Mapped[Optional[str]] = mapped_column(
        String(200), nullable=True, comment="角色描述"
    )

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    # 多对多：拥有该角色的用户
    users: Mapped[List["User"]] = relationship(
        "User",
        secondary=user_roles_table,
        back_populates="roles",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<Role id={self.id} scope={self.scope} code={self.code}>"
