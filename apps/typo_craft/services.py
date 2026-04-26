"""
TypoCraft 核心业务逻辑
"""
import logging
from uuid import UUID
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from apps.typo_craft.models import TypoCraftProject, TypoCraftAsset
from apps.typo_craft.schemas import (
    ProjectCreateIn, AssetGeneratePosterIn, AssetGenerateUIIn,
    AssetStatusUpdateIn
)
from apps.typo_craft.ai_clients import generate_prompt_from_agent, submit_image_generation, check_image_status
from apps.typo_craft.prompts import Visual_Director_App, Visual_designers_and_AI_drawing_experts, UI_Illustrator

from core.exceptions import NotFoundException, BadRequestException

logger = logging.getLogger(__name__)

async def create_project(data: ProjectCreateIn, user_id: UUID, db: AsyncSession) -> TypoCraftProject:
    """创建 App 视觉方案（借助 Agent 1 提取基调锚点提示词）"""
    # 1. 组合用户需求交给 Agent 1
    user_input = f"项目名称：{data.project_name}\n场景描述：{data.scene_desc}"
    try:
        base_style_prompt = await generate_prompt_from_agent(
            system_prompt=Visual_Director_App,
            user_input=user_input
        )
    except Exception as e:
        logger.error(f"[TypoCraft] Error generating base style prompt: {str(e)}")
        raise BadRequestException(f"无法生成视觉锚点: {str(e)}")

    # 2. 建档入库
    project = TypoCraftProject(
        user_id=user_id,
        project_name=data.project_name,
        scene_desc=data.scene_desc,
        base_style_prompt=base_style_prompt
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)
    return project

async def submit_generation(
    asset_type: str, 
    user_prompt: str, 
    user_id: UUID, 
    db: AsyncSession, 
    project_id: UUID | None = None,
    aspect_ratio: str = "1:1"
) -> TypoCraftAsset:
    """提交成图请求并建立占位 Asset"""
    # 1. 使用对应 Agent 处理输入得到最终提示词
    if asset_type == "POSTER":
        try:
            final_ai_prompt = await generate_prompt_from_agent(
                system_prompt=Visual_designers_and_AI_drawing_experts,
                user_input=user_prompt
            )
        except Exception as e:
            raise BadRequestException(f"无法生成海报提示词: {str(e)}")
    elif asset_type == "UI_ILLUSTRATION":
        if not project_id:
            raise BadRequestException("项目ID不能为空（UI 插图必须关联项目）。")
        # 提取全局画风，由系统填入
        query = select(TypoCraftProject).where(TypoCraftProject.id == project_id, TypoCraftProject.user_id == user_id)
        res = await db.execute(query)
        project = res.scalar_one_or_none()
        if not project:
            raise NotFoundException("未找到对应的视觉方案。")

        system_prompt = UI_Illustrator.replace("{这里由系统动态填入 Agent 1 生成的 base_prompt}", project.base_style_prompt)
        try:
            final_ai_prompt = await generate_prompt_from_agent(
                system_prompt=system_prompt,
                user_input=user_prompt
            )
        except Exception as e:
            raise BadRequestException(f"无法生成 UI 提示词: {str(e)}")
    else:
        raise BadRequestException("非法的任务类型")

    # 2. 调用 API 提交生图任务获取 provider_task_id
    try:
        provider_task_id = await submit_image_generation(prompt=final_ai_prompt, ratio=aspect_ratio)
    except Exception as e:
        logger.error(f"[TypoCraft] Error submitting image task: {str(e)}")
        raise BadRequestException(f"生图任务提交失败: {str(e)}")

    # 3. 落库：状态为 PENDING
    asset = TypoCraftAsset(
        user_id=user_id,
        project_id=project_id,
        asset_type=asset_type,
        status="PENDING",
        provider_task_id=provider_task_id,
        user_prompt=user_prompt,
        final_ai_prompt=final_ai_prompt
    )
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    return asset

async def sync_asset_status(asset_id: UUID, user_id: UUID, db: AsyncSession) -> TypoCraftAsset:
    """轮询数据库更新"""
    query = select(TypoCraftAsset).where(TypoCraftAsset.id == asset_id, TypoCraftAsset.user_id == user_id)
    res = await db.execute(query)
    asset = res.scalar_one_or_none()
    
    if not asset:
        raise NotFoundException("未找到记录")

    if asset.status in ["SUCCESS", "FAILED", "REJECTED"]:
        return asset

    # 从上游查询状态
    if not asset.provider_task_id:
        return asset

    try:
        remote_res = await check_image_status(asset.provider_task_id)
        status = remote_res.get("status")
        if status == "SUCCESS":
            asset.status = "SUCCESS"
            urls = remote_res.get("image_urls", [])
            if urls:
                asset.image_url = urls[0]
        elif status == "FAILURE":
            asset.status = "FAILED"
        
        if asset.status != "PENDING":
            db.add(asset)
            await db.commit()
            await db.refresh(asset)

    except Exception as e:
        logger.error(f"Error syncing asset {asset_id}: {str(e)}")
        # 当作异常依然保持 PENDING 即可

    return asset

async def admin_update_status(asset_id: UUID, data: AssetStatusUpdateIn, db: AsyncSession) -> TypoCraftAsset:
    """强制更新可见状态（可用于人工复核等）"""
    query = select(TypoCraftAsset).where(TypoCraftAsset.id == asset_id)
    res = await db.execute(query)
    asset = res.scalar_one_or_none()
    if not asset:
        raise NotFoundException("记录不存在")
    
    # 这里一般会检查当前用户是否具有权限，但在需求中只做记录兜底，通常 router 加上超管拦截即可。
    if data.status is not None:
        asset.status = data.status
    if data.is_public is not None:
        asset.is_public = data.is_public
    
    db.add(asset)
    await db.commit()
    await db.refresh(asset)
    
    return asset
