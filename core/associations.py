"""
数据库多对多关联表定义
将关联表单独抽离，避免 models 之间的循环导入
"""
from sqlalchemy import Column, ForeignKey, Integer, Table

from core.database import Base

# 用户-角色 多对多关联表
user_roles_table = Table(
    "core_user_roles",
    Base.metadata,
    Column(
        "user_id",
        Integer,
        ForeignKey("core_users.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "role_id",
        Integer,
        ForeignKey("core_roles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)
