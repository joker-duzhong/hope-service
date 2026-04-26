"""
TypoCraft (言图) 模型定义
"""
import uuid
from sqlalchemy import String, Text, ForeignKey, JSON, Boolean
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import CoreModel

class TypoCraftProject(CoreModel):
    """App 视觉方案项目表提取全局画风基调"""
    __tablename__ = "typo_craft_projects"

    user_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    project_name: Mapped[str] = mapped_column(String(100), nullable=False)
    scene_desc: Mapped[str] = mapped_column(Text, nullable=False)
    base_style_prompt: Mapped[str] = mapped_column(Text, nullable=False)


class TypoCraftAsset(CoreModel):
    """图片资产库 (单张海报 / 系列插图)"""
    __tablename__ = "typo_craft_assets"

    user_id: Mapped[uuid.UUID] = mapped_column(index=True, nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("typo_craft_projects.id"), index=True, nullable=True)
    asset_type: Mapped[str] = mapped_column(String(20), nullable=False, comment="POSTER, UI_ILLUSTRATION")
    status: Mapped[str] = mapped_column(String(20), default="PENDING", comment="PENDING, SUCCESS, FAILED, REJECTED")
    provider_task_id: Mapped[str | None] = mapped_column(String(100), index=True, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    user_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    final_ai_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)
    is_public: Mapped[bool] = mapped_column(Boolean, default=False)
