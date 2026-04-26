"""
TypoCraft AI 客户端封装
封装大模型提示词生成和第三方画图调用
"""
import logging
from typing import Optional, Dict

from core.llm.engine import generate_chat, generate_image, fetch_image_result

logger = logging.getLogger(__name__)

async def generate_prompt_from_agent(system_prompt: str, user_input: str) -> str:
    """使用 LLM 生成结构化的 Prompt"""
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_input},
    ]
    # 调用底层封装的 generate_chat
    prompt_result = await generate_chat(messages=messages)
    return prompt_result

async def submit_image_generation(prompt: str, ratio: str = "1:1") -> str:
    """提交画图任务，返回 provider_task_id"""
    # 这里可将 kwargs（如 aspect_ratio）传递进去
    task_id = await generate_image(prompt=prompt, ratio=ratio)
    return task_id

async def check_image_status(provider_task_id: str) -> dict:
    """轮询云端任务进度"""
    # fetch_image_result 返回 dict: {'status': 'SUCCESS'/'FAILURE'/'PENDING', 'image_urls': [...], 'msg': '...'}
    res = await fetch_image_result(task_id=provider_task_id)
    return res
