"""
用户 ORM 表结构
表名前缀: core_
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.associations import user_roles_table
from core.database import CoreModel

if TYPE_CHECKING:
    from core.roles.models import Role


class User(CoreModel):
    """用户表"""
    __tablename__ = "core_users"

    # 微信公众号（主要标识）
    openid: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )
    unionid: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, index=True, nullable=True
    )

    # 基本信息
    nickname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 可选的其他登录方式
    username: Mapped[Optional[str]] = mapped_column(
        String(50), unique=True, index=True, nullable=True
    )
    email: Mapped[Optional[str]] = mapped_column(
        String(100), unique=True, index=True, nullable=True
    )
    phone: Mapped[Optional[str]] = mapped_column(
        String(20), unique=True, index=True, nullable=True
    )
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)
    token_version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # 来源标识
    source: Mapped[str] = mapped_column(String(50), default="default")

    # 多对多：用户拥有的角色
    roles: Mapped[List["Role"]] = relationship(
        "Role",
        secondary=user_roles_table,
        back_populates="users",
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return f"<User id={self.id} openid={self.openid}>"


class UserIdentity(CoreModel):
    """An external login identity, scoped to the provider application."""
    __tablename__ = "core_user_identities"
    __table_args__ = (
        UniqueConstraint("provider", "provider_app_id", "subject", name="uq_user_identity_provider_subject"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("core_users.id"), index=True, nullable=False
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="wechat")
    provider_app_id: Mapped[str] = mapped_column(String(64), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    unionid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    verified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_data: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
