"""
LLM 核心驱动模块，使用纯 HTTP 请求调用 LLM API
"""
import json
import logging
from typing import AsyncGenerator, Optional
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import settings
from core.llm.prompts import get_base_messages

logger = logging.getLogger(__name__)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def generate_chat(
    messages: list[dict],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> str:
    """
    非流式对话生成
    """
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider}")

    api_key = config.get("api_key")
    base_url = config.get("base_url")
    default_model = config.get("default_model", "gpt-3.5-turbo")
    timeout = config.get("timeout", 60.0)

    # 注入基础合规 Prompt
    full_messages = get_base_messages() + messages

    # 构建请求体
    payload = {
        "model": model or default_model,
        "messages": full_messages,
        "stream": False,
    }

    # 添加额外参数（如 response_format 等）
    for key, value in kwargs.items():
        if key not in ["stream", "messages", "model"]:
            payload[key] = value

    # 构建请求头
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info(f"[LLM] 调用 {provider} API - 模型: {payload['model']}, URL: {base_url}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(base_url, json=payload, headers=headers)

    logger.info(f"[LLM] 响应状态码: {response.status_code}")

    if response.status_code != 200:
        error_msg = f"LLM API 返回错误 {response.status_code}: {response.text}"
        logger.error(f"[LLM] {error_msg}")
        raise Exception(error_msg)

    # 手动解析 JSON，捕获更详细的错误信息
    try:
        result = response.json()
    except Exception as e:
        logger.error(f"[LLM] JSON 解析失败: {str(e)}, 响应体: {response.text}")
        raise

    logger.debug(f"[LLM] 解析后的响应: {result}")

    # 解析响应
    if "choices" in result and len(result["choices"]) > 0:
        content = result["choices"][0]["message"]["content"]
        logger.info(f"[LLM] 成功获取回复，长度: {len(content)}")

        # 处理 markdown 代码块包装的 JSON（兼容两种情况）
        if "```" in content:
            # 提取 markdown 代码块中的 JSON
            parts = content.split("```")
            # 取中间部分（第 1 或 2 个元素，取决于是否以 ``` 开头）
            for part in parts:
                part = part.strip()
                if part.startswith("json"):
                    content = part[4:].strip()  # 去掉 "json" 前缀
                    break
                elif part.startswith("{"):
                    # 这是 JSON 内容
                    content = part
                    break

        return content.strip()
    else:
        raise Exception(f"LLM 响应格式错误: {result}")


async def generate_stream_chat(
    messages: list[dict],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> AsyncGenerator[str, None]:
    """
    流式对话生成
    """
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider}")

    api_key = config.get("api_key")
    base_url = config.get("base_url")
    default_model = config.get("default_model", "gpt-3.5-turbo")
    timeout = config.get("timeout", 60.0)

    # 注入基础合规 Prompt
    full_messages = get_base_messages() + messages

    # 构建请求体
    payload = {
        "model": model or default_model,
        "messages": full_messages,
        "stream": True,
    }

    # 添加额外参数
    for key, value in kwargs.items():
        if key not in ["stream", "messages", "model"]:
            payload[key] = value

    # 构建请求头
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info(f"[LLM] 调用流式 {provider} API - 模型: {payload['model']}, URL: {base_url}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", base_url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                error_msg = f"LLM API 返回错误 {response.status_code}: {error_text.decode()}"
                logger.error(f"[LLM] {error_msg}")
                raise Exception(error_msg)

            async for line in response.aiter_lines():
                if not line.strip():
                    continue

                # 处理 SSE 格式的流式响应
                if line.startswith("data: "):
                    data_str = line[6:]  # 去掉 "data: " 前缀

                    if data_str == "[DONE]":
                        break

                    try:
                        data = json.loads(data_str)
                        if "choices" in data and len(data["choices"]) > 0:
                            delta = data["choices"][0].get("delta", {})
                            if "content" in delta and delta["content"]:
                                yield delta["content"]
                    except json.JSONDecodeError:
                        logger.warning(f"[LLM] 无法解析 JSON: {data_str}")
                        continue
