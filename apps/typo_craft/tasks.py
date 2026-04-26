"""
TypoCraft 纯后台自动打标任务

约束: 利用 Celery 进行异步打标
"""
import asyncio
import logging
from sqlalchemy import select

from apps.typo_craft.models import TypoCraftAsset
from apps.typo_craft.ai_clients import generate_prompt_from_agent
from apps.typo_craft.prompts import Tagging_Agent

from core.database import async_session_maker
import json
from celery import shared_task
import asyncio

logger = logging.getLogger(__name__)

async def _auto_tag_successful_assets():
    """纯异步逻辑，处理打标"""
    async with async_session_maker() as db:
        query = select(TypoCraftAsset)\
            .where(TypoCraftAsset.status == 'SUCCESS')\
            .where(TypoCraftAsset.tags.is_(None))\
            .limit(30)

        
        res = await db.execute(query)
        assets = res.scalars().all()
        
        if not assets:
            return "No assets to tag."

        tagged_count = 0
        # 逐个获取 tags，这里可用 asyncio.gather 加速
        for asset in assets:
            try:
                ai_reply = await generate_prompt_from_agent(
                    system_prompt=Tagging_Agent,
                    user_input=asset.final_ai_prompt
                )
                # LLM 应返回 JSON 数组 `["标签1", "标签2"...]`
                if ai_reply.startswith("```json"):
                    ai_reply = ai_reply[7:-3].strip()
                elif ai_reply.startswith("```"):
                    ai_reply = ai_reply[3:-3].strip()
                    
                tags = json.loads(ai_reply)
                if isinstance(tags, list):
                    asset.tags = tags
                    tagged_count += 1
            except Exception as e:
                logger.error(f"[TypoCraft Tagging] Failed for Asset ID {asset.id}: {str(e)}")
            
            # 不论成功失败，每个周期只处理有限数量。如果不规范可以设置重试计数字段或抛弃。

        if tagged_count > 0:
            db.add_all(assets)
            await db.commit()
            
    return f"Tagged {tagged_count} assets."


@shared_task(name="typo_craft.auto_tag_successful_assets")
def auto_tag_successful_assets():
    """Celery Worker 入口函数包装异步协程"""
    # 强制同步化 async 代码，避免在 Celery Worker 里死锁
    return asyncio.get_event_loop().run_until_complete(_auto_tag_successful_assets())
