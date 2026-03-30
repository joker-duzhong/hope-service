"""
用户模型
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class User(Base):
    """用户表"""
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)

    # 微信公众号（主要标识）
    openid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)
    unionid: Mapped[Optional[str]] = mapped_column(String(64), unique=True, index=True, nullable=True)

    # 基本信息
    nickname: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    avatar: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    # 可选的其他登录方式
    username: Mapped[Optional[str]] = mapped_column(String(50), unique=True, index=True, nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(100), unique=True, index=True, nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(20), unique=True, index=True, nullable=True)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # 状态
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False)

    # 来源标识（用于区分不同项目的用户）
    source: Mapped[str] = mapped_column(String(50), default="wechat")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now()
    )

    def __repr__(self) -> str:
        return f"<User openid={self.openid}>"
