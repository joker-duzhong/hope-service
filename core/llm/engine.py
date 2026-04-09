"""
LLM 核心驱动模块，封装基于 OpenAI 协议的通用异步客户端
"""
from typing import AsyncGenerator, Optional

from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import settings
from core.llm.prompts import get_base_messages


class LLMClientManager:
    """管理多个 LLM 客户端的单例类"""

    _clients: dict[str, AsyncOpenAI] = {}

    @classmethod
    def get_client(cls, provider: str) -> AsyncOpenAI:
        """根据提供商获取 OpenAI 客户端"""
        if provider not in cls._clients:
            config = settings.LLM_PROVIDERS.get(provider)
            if not config:
                raise ValueError(f"未配置 LLM 提供商: {provider}")

            cls._clients[provider] = AsyncOpenAI(
                api_key=config.get("api_key"),
                base_url=config.get("base_url"),
                timeout=config.get("timeout", 60.0),
                max_retries=0,  # 使用 tenacity 统一处理重试
            )
        return cls._clients[provider]


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
    client = LLMClientManager.get_client(provider)
    config = settings.LLM_PROVIDERS.get(provider, {})

    # 注入基础合规 Prompt
    full_messages = get_base_messages() + messages

    response = await client.chat.completions.create(
        model=model or config.get("default_model", "gpt-3.5-turbo"),
        messages=full_messages,
        stream=False,
        **kwargs,
    )
    return response.choices[0].message.content


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
    client = LLMClientManager.get_client(provider)
    config = settings.LLM_PROVIDERS.get(provider, {})

    # 注入基础合规 Prompt
    full_messages = get_base_messages() + messages

    response = await client.chat.completions.create(
        model=model or config.get("default_model", "gpt-3.5-turbo"),
        messages=full_messages,
        stream=True,
        **kwargs,
    )

    async for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
