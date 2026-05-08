"""
LLM 核心驱动模块，使用纯 HTTP 请求调用 LLM API
"""
import asyncio
import json
import logging
import re
from typing import Any, AsyncGenerator, Optional
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


IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*]\((https?://[^)\s]+)\)")
DOWNLOAD_MARKDOWN_RE = re.compile(r"\[[^\]]*(?:下载|download)[^\]]*]\((https?://[^)\s]+)\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)]+")


def extract_image_result_from_content(content: str) -> dict[str, Optional[str]]:
    """从第三方流式文本中提取图片和下载链接。"""
    image_match = IMAGE_MARKDOWN_RE.search(content)
    download_match = DOWNLOAD_MARKDOWN_RE.search(content)
    image_url = image_match.group(1) if image_match else None
    download_url = download_match.group(1) if download_match else None

    if not image_url:
        url_match = URL_RE.search(content)
        image_url = url_match.group(0) if url_match else None
    if not download_url:
        download_url = image_url

    return {"image_url": image_url, "download_url": download_url}


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
        raise ValueError(f"未配置 LLM 提供商: {provider},{settings}")

    api_key = config.get("api_key")
    base_url = config.get("base_url")
    default_model = config.get("default_model", "gpt-3.5-turbo")
    timeout = config.get("timeout", 60.0)

    # 注入基础合规 Prompt
    full_messages = get_base_messages() + messages

    # 合并连续的 system 消息为一条（部分 LLM API 不支持多条 system 消息）
    merged = []
    for msg in full_messages:
        if msg["role"] == "system" and merged and merged[-1]["role"] == "system":
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg.copy())
    full_messages = merged

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
        if not content:
            raise Exception(f"LLM 返回空内容, 完整响应: {result}")
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

    # 合并连续的 system 消息为一条（部分 LLM API 不支持多条 system 消息）
    merged = []
    for msg in full_messages:
        if msg["role"] == "system" and merged and merged[-1]["role"] == "system":
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg.copy())
    full_messages = merged

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


async def generate_stream_image_chat(
    messages: list[dict],
    provider: Optional[str] = None,
    model: Optional[str] = None,
    size: Optional[str] = None,
    quality: Optional[str] = None,
    background: Optional[str] = None,
    output_format: Optional[str] = None,
    output_compression: Optional[int] = None,
    n: Optional[int] = None,
    temperature: float = 0.7,
    top_p: float = 1.0,
    timeout: Optional[float] = None,
    extra_body: Optional[dict[str, Any]] = None,
    **kwargs,
) -> dict[str, Optional[str]]:
    """
    使用 OpenAI Chat Completions 兼容流式接口生成图片。

    适配部分 OneAPI 服务商：图片模型通过 /chat/completions 以 SSE 返回，
    最终图片地址出现在 delta.content 的 Markdown 图片链接中。
    """
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider}")

    api_key = config.get("api_key")
    base_url = config.get("image_chat_url") or config.get("base_url")
    default_model = config.get("default_image_model", "gpt-image-2")
    request_timeout = timeout or config.get("image_timeout") or config.get("timeout", 180.0)

    if not base_url:
        raise ValueError(f"提供商 {provider} 缺少图片流式生成 URL 配置。")

    full_messages = get_base_messages() + messages
    merged = []
    for msg in full_messages:
        if msg["role"] == "system" and merged and merged[-1]["role"] == "system":
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(msg.copy())
    full_messages = merged

    payload: dict[str, Any] = {
        "model": model or default_model,
        "messages": full_messages,
        "stream": True,
        "temperature": temperature,
        "top_p": top_p,
    }
    if size is not None:
        payload["size"] = size
    if quality is not None:
        payload["quality"] = quality
    if background is not None:
        payload["background"] = background
    if output_format is not None:
        payload["output_format"] = output_format
    if output_compression is not None:
        payload["output_compression"] = output_compression
    if n is not None:
        payload["n"] = n
    if extra_body:
        payload.update(extra_body)
    for key, value in kwargs.items():
        if key not in {"model", "messages", "stream"} and value is not None:
            payload[key] = value

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    logger.info(f"[ImageStream] 提交流式图片生成 - 提供商: {provider}, 模型: {payload['model']}, URL: {base_url}")

    full_content = ""
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        async with client.stream("POST", base_url, json=payload, headers=headers) as response:
            if response.status_code != 200:
                error_text = await response.aread()
                error_msg = f"Image stream API 返回错误 {response.status_code}: {error_text.decode(errors='replace')}"
                logger.error(f"[ImageStream] {error_msg}")
                raise Exception(error_msg)

            async for line in response.aiter_lines():
                if not line.strip() or not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if data_str == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    logger.warning(f"[ImageStream] 无法解析 SSE JSON: {data_str}")
                    continue

                choices = data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}
                content = delta.get("content")
                if content:
                    full_content += content

    links = extract_image_result_from_content(full_content)
    if not links["image_url"]:
        raise Exception(f"图片生成完成但未提取到图片链接，响应内容: {full_content}")

    return {
        "content": full_content,
        "image_url": links["image_url"],
        "download_url": links["download_url"],
    }

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def generate_image(
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    **kwargs,
) -> str:
    """
    异步图像生成（仅提交任务，返回 task_id）
    """
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider},{settings}")

    api_key = config.get("api_key")
    
    # 手动配置的生成和查询URL
    generation_url = config.get("base_url")
    
    if not generation_url:
        raise ValueError(
            f"提供商 {provider} 缺少base_url 配置。请在 LLM_PROVIDERS 设定中补充 "
            f"'base_url'。"
        )

    # 默认模型名
    default_model = config.get("default_image_model", "fluxpro11ultra")
    timeout = config.get("timeout", 60.0)

    # 构建请求体
    payload = {
        "model": model or default_model,
        "prompt": prompt,
    }

    # 提取在问 API 支持的可选参数
    ratio = kwargs.pop("ratio", None)
    image_url = kwargs.pop("image_url", None)
    translate = kwargs.pop("translate", None)

    if ratio:
        payload["ratio"] = ratio
    if image_url:
        payload["image_url"] = image_url
    if translate is not None:
        payload["translate"] = translate

    # 添加其他可能会传的额外参数
    for key, value in kwargs.items():
        payload[key] = value

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    logger.info(f"[Image] 提交生成任务 - 提供商: {provider}, 模型: {payload['model']}, URL: {generation_url}")

    async with httpx.AsyncClient(timeout=timeout) as client:
        # 1. 提交生成任务
        response = await client.post(generation_url, json=payload, headers=headers)
        
        if response.status_code != 200:
            error_msg = f"Image API 提交失败返回 {response.status_code}: {response.text}"
            logger.error(f"[Image] {error_msg}")
            raise Exception(error_msg)

        try:
            result = response.json()
        except Exception as e:
            logger.error(f"[Image] JSON 解析失败: {str(e)}, 响应体: {response.text}")
            raise

        task_id = result.get("task_id")
        if not task_id:
            raise Exception(f"请求成功但未返回 task_id，响应内容: {result}")
        
        logger.info(f"[Image] 任务已提交成功，task_id: {task_id}")
        return task_id

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)
async def fetch_image_result(
    task_id: str,
    provider: Optional[str] = None,
) -> dict:
    """
    查询图像生成任务状态。
    调用方可根据返回的 'status' 字段主动决定是否继续轮询。
    返回示例:
    - {"status": "SUCCESS", "image_urls": [...]}
    - {"status": "FAILURE", "msg": "..."}
    - {"status": "PENDING"} (或 "RUNNING" 等其他上游状态)
    """
    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider},{settings}")

    api_key = config.get("api_key")
    fetch_url = config.get("image_fetch_url")
    
    if not fetch_url:
        raise ValueError(f"提供商 {provider} 缺少 'image_fetch_url' 配置。")

    headers = {
        "Authorization": f"Bearer {api_key}",
    }
    timeout = config.get("timeout", 60.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        # GET 方法拉取进度
        fetch_resp = await client.get(fetch_url, params={"task_id": task_id}, headers=headers)
        if fetch_resp.status_code != 200:
            error_msg = f"Image API 查询失败，状态码 {fetch_resp.status_code}: {fetch_resp.text}"
            logger.error(f"[Image] {error_msg}")
            raise Exception(error_msg)
            
        try:
            fetch_res = fetch_resp.json()
        except Exception as e:
            logger.error(f"[Image] 轮询解析 JSON 失败: {str(e)}，响应体: {fetch_resp.text}")
            raise

        # 解析嵌套或扁平的状态字段
        info = fetch_res.get("info", {})
        status = info.get("status") or fetch_res.get("status")

        if status == "SUCCESS":
            image_urls = info.get("imageUrl") or []
            return {"status": "SUCCESS", "image_urls": image_urls}
        elif status == "FAILURE":
            status_dict = fetch_res.get("status") if isinstance(fetch_res.get("status"), dict) else {}
            msg = info.get("msg") or status_dict.get("msg") or "上游拉取报错"
            return {"status": "FAILURE", "msg": msg}
        else:
            return {"status": status}
