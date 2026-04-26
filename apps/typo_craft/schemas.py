"""
TypoCraft 数据校验与序列化 Pydantic 模型
"""
from typing import Optional, List
from uuid import UUID
from pydantic import BaseModel, Field

# ==================== Project ====================

class ProjectCreateIn(BaseModel):
    project_name: str = Field(..., max_length=100)
    scene_desc: str = Field(..., max_length=500)

class ProjectOut(BaseModel):
    id: UUID
    project_name: str
    scene_desc: str
    base_style_prompt: str

    class Config:
        from_attributes = True

# ==================== Asset Generation ====================

class AssetGeneratePosterIn(BaseModel):
    prompt: str = Field(..., description="用户想要的海报描述")
    aspect_ratio: Optional[str] = Field("1:1", description="宽高比")

class AssetGenerateUIIn(BaseModel):
    project_id: UUID = Field(..., description="关联的项目ID")
    scene_prompt: str = Field(..., description="当前所需页面的描述")
    aspect_ratio: Optional[str] = Field("1:1", description="宽高比")

class AssetStatusOut(BaseModel):
    id: UUID
    status: str
    image_url: Optional[str]
    final_ai_prompt: str

    class Config:
        from_attributes = True

# ==================== Asset Feed & Update ====================

class AssetStatusUpdateIn(BaseModel):
    status: Optional[str] = Field(None, description="状态覆写 (如 REJECTED)")
    is_public: Optional[bool] = Field(None, description="是否展示在广场")

class AssetFeedOut(BaseModel):
    id: UUID
    image_url: str
    user_prompt: str
    tags: Optional[List[str]]

    class Config:
        from_attributes = True
