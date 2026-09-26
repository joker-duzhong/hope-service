"""
LLM 核心驱动模块，使用纯 HTTP 请求调用 LLM API
"""
import asyncio
import json
import logging
import re
from time import monotonic
from typing import Any, AsyncGenerator, Literal, Optional
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from core.config import settings
from core.llm.errors import ChatErrorKind, ChatGenerationError
from core.llm.prompts import get_base_messages

logger = logging.getLogger(__name__)


IMAGE_MARKDOWN_RE = re.compile(r"!\[[^\]]*]\((https?://[^)\s]+)\)")
DOWNLOAD_MARKDOWN_RE = re.compile(r"\[[^\]]*(?:下载|download)[^\]]*]\((https?://[^)\s]+)\)", re.IGNORECASE)
URL_RE = re.compile(r"https?://[^\s)]+")
DATA_URL_IMAGE_RE = re.compile(r"^data:(image/[a-zA-Z0-9.+-]+);base64,(.+)$", re.DOTALL)
IMAGE_ERROR_BODY_LIMIT = 16 * 1024
IMAGE_ERROR_MESSAGE_CATEGORIES = (
    ("invalid_credentials", r"invalid.{0,20}(?:api.?key|token)|incorrect.{0,20}api.?key|unauthori[sz]ed|无效.{0,8}(?:令牌|密钥)|(?:令牌|密钥).{0,8}(?:无效|错误)"),
    ("quota_exceeded", r"quota|insufficient.{0,16}(?:credit|balance)|余额不足|额度|配额"),
    ("rate_limited", r"rate.?limit|too many requests|限流|请求.{0,8}频繁"),
    ("channel_unavailable", r"no.{0,24}(?:channel|provider)|(?:channel|provider).{0,16}unavailable|无.{0,8}(?:渠道|通道)|(?:渠道|通道).{0,8}不可用"),
    ("model_unavailable", r"model.{0,32}(?:not found|not exist|not available|unavailable|not support)|(?:unknown|unsupported).{0,16}model|模型.{0,12}(?:不存在|不可用|不支持)"),
    ("invalid_image", r"(?:invalid|unsupported|corrupt).{0,24}image|image.{0,24}(?:invalid|unsupported|too large)|(?:图片|图像|参考图).{0,16}(?:格式|无效|大小|过大|损坏)"),
    ("unsupported_parameter", r"(?:unsupported|unknown|unrecognized).{0,24}(?:parameter|argument|field)|不支持.{0,12}参数|未知参数"),
    ("content_policy", r"content.?policy|safety.{0,16}(?:system|filter)|moderation|内容.{0,8}(?:安全|违规)|敏感内容"),
    ("upstream_timeout", r"timed? out|timeout|超时"),
    ("upstream_unavailable", r"(?:service|upstream).{0,24}unavailable|overload|bad gateway|服务.{0,12}(?:不可用|繁忙)"),
    ("access_denied", r"access denied|forbidden|permission|权限|禁止访问|拒绝访问"),
    ("invalid_request", r"invalid.{0,24}(?:request|parameter|argument)|参数.{0,8}(?:错误|无效)|请求格式"),
)


class ImageGenerationError(RuntimeError):
    """上游 HTTP 错误；失败文案仅附加经过脱敏的错误说明。"""

    def __init__(self, status_code: int, diagnostics: dict[str, Any]):
        reason = f"Image generation API 返回错误 HTTP {status_code}"
        if diagnostics.get("error_message"):
            reason += f": {diagnostics['error_message']}"
        super().__init__(reason)
        self.diagnostics = diagnostics


def _safe_image_error_message(message: str, sensitive_values: tuple[str, ...]) -> str:
    for secret in sorted(sensitive_values, key=len, reverse=True):
        message = message.replace(secret, "[redacted]")
    message = re.sub(r"(?i)data:[^\s,]*;base64,[A-Za-z0-9+/=\r\n]+", "[image data]", message)
    message = re.sub(r"https?://[^\s<>\"']+", "[url]", message, flags=re.IGNORECASE)
    message = re.sub(r"(?i)\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+", "[redacted]", message)
    message = re.sub(r"\bsk-[A-Za-z0-9_-]+", "[redacted]", message, flags=re.IGNORECASE)
    message = re.sub(
        r"(?i)([\"']?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|authorization|password|secret)[\"']?\s*[:=]\s*)"
        r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1[redacted]", message,
    )
    message = re.sub(r"[A-Za-z0-9+/=_-]{80,}", "[redacted data]", message)
    message = re.sub(r"[\x00-\x1f\x7f-\x9f\u200b-\u200f\u2028-\u202e\u2060-\u206f]", " ", message)
    message = " ".join(message.split())
    return message[:1000] + ("…" if len(message) > 1000 else "")


def _safe_image_diagnostic_token(value: Any, sensitive_values: tuple[str, ...]) -> Optional[str]:
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) <= 1_000_000_000:
        value = str(value)
    if not isinstance(value, str) or not value:
        return None
    if any(secret in value or value in secret for secret in sensitive_values):
        return "[redacted]"
    if (
        len(value) > 128
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]*", value)
        or "://" in value
        or re.search(r"(?i)(?:sk-|bearer|authorization|eyJ|iVBORw0KGgo|R0lGOD|UklGR)", value)
    ):
        return "[omitted]"
    return value


def _image_error_diagnostics(
    response: httpx.Response, *, sensitive_values: tuple[str, ...],
) -> dict[str, Any]:
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    diagnostics: dict[str, Any] = {
        "http_status": response.status_code,
        "response_content_type": content_type if content_type in {
            "application/json", "text/html", "text/plain",
        } else "other",
    }
    for name in ("x-request-id", "request-id", "x-oneapi-request-id", "x-new-api-request-id"):
        request_id = _safe_image_diagnostic_token(response.headers.get(name), sensitive_values)
        if request_id:
            diagnostics["upstream_request_id"] = request_id
            break

    if len(response.content) > IMAGE_ERROR_BODY_LIMIT:
        diagnostics["error_body"] = "omitted_oversize"
        return diagnostics
    try:
        body = response.json()
    except (ValueError, UnicodeError, RecursionError):
        diagnostics["error_body"] = "non_json"
        return diagnostics
    if not isinstance(body, dict):
        diagnostics["error_body"] = "non_object_json"
        return diagnostics

    diagnostics["error_body"] = "json_object"
    error = body.get("error")
    fields = error if isinstance(error, dict) else body
    for name in ("code", "type", "param"):
        value = _safe_image_diagnostic_token(fields.get(name), sensitive_values)
        if value is not None:
            diagnostics[f"error_{name}"] = value
    message = fields.get("message") if not isinstance(error, str) else error
    if isinstance(message, str):
        diagnostics["error_message"] = _safe_image_error_message(message, sensitive_values)
        diagnostics["message_category"] = next(
            (category for category, pattern in IMAGE_ERROR_MESSAGE_CATEGORIES
             if re.search(pattern, message, re.IGNORECASE)),
            "unclassified",
        )
    return diagnostics


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


def _coerce_progress(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip().rstrip("%")
    try:
        progress = int(float(value))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, progress))


def _image_generation_url_from_config(config: dict[str, Any]) -> Optional[str]:
    generation_url = config.get("image_generation_url") or config.get("image_generations_url")
    if generation_url:
        return generation_url

    for key in ("image_chat_url", "base_url"):
        base_url = config.get(key)
        if not base_url:
            continue
        trimmed = str(base_url).rstrip("/")
        if trimmed.endswith("/images/generations"):
            return trimmed
        if trimmed.endswith("/chat/completions"):
            return f"{trimmed[:-len('/chat/completions')]}/images/generations"
        if trimmed.endswith("/v1"):
            return f"{trimmed}/images/generations"
    return None


def _mime_type_from_output_format(output_format: Optional[str]) -> str:
    normalized = (output_format or "png").strip().lower()
    if normalized in {"jpg", "jpeg"}:
        return "image/jpeg"
    if normalized == "webp":
        return "image/webp"
    return "image/png"


def parse_image_generation_result(result: dict[str, Any], *, output_format: Optional[str] = None) -> dict[str, Any]:
    if not isinstance(result, dict):
        raise ValueError("图片生成响应必须是 JSON 对象")
    data = result.get("data")
    if not isinstance(data, list) or not data:
        raise ValueError("图片生成响应缺少 data[0]")

    first_item = data[0] if isinstance(data[0], dict) else {}
    b64_json = first_item.get("b64_json")
    image_url = first_item.get("url")
    mime_type = _mime_type_from_output_format(output_format)

    if isinstance(b64_json, str) and b64_json:
        data_url_match = DATA_URL_IMAGE_RE.match(b64_json.strip())
        if data_url_match:
            mime_type = data_url_match.group(1)
            b64_json = data_url_match.group(2)
        return {
            "content": "",
            "b64_json": b64_json,
            "image_url": None,
            "download_url": None,
            "mime_type": mime_type,
            "created": result.get("created"),
            "model": result.get("model"),
            "object": result.get("object"),
            "usage": result.get("usage"),
        }

    if isinstance(image_url, str) and image_url:
        return {
            "content": "",
            "b64_json": None,
            "image_url": image_url,
            "download_url": image_url,
            "mime_type": mime_type,
            "created": result.get("created"),
            "model": result.get("model"),
            "object": result.get("object"),
            "usage": result.get("usage"),
        }

    raise ValueError("图片生成响应未包含 b64_json 或 url")


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
    *,
    diagnostic_sensitive_values: tuple[str, ...] = (),
    **kwargs,
) -> str:
    """
    非流式对话生成
    """
    started_at = monotonic()

    def failure(kind: ChatErrorKind, diagnostics: Optional[dict[str, Any]] = None) -> ChatGenerationError:
        error = ChatGenerationError(kind, {
            **(diagnostics or {}),
            "elapsed_ms": max(0, round((monotonic() - started_at) * 1000)),
        })
        logger.warning("[LLM] 对话失败 diagnostics=%s", json.dumps(error.diagnostics, ensure_ascii=True, sort_keys=True))
        return error

    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not isinstance(config, dict) or not config:
        raise failure("configuration", {"field": "LLM_PROVIDERS"})

    api_key = config.get("api_key")
    base_url = config.get("base_url")
    default_model = config.get("default_model", "gpt-3.5-turbo")
    timeout = config.get("timeout", 60.0)
    for field, value in (("api_key", api_key), ("base_url", base_url), ("model", model or default_model)):
        if not isinstance(value, str) or not value.strip():
            raise failure("configuration", {"field": field})
    try:
        url = httpx.URL(base_url)
        if url.scheme not in {"http", "https"} or not url.host:
            raise ValueError("invalid endpoint")
    except (httpx.InvalidURL, ValueError):
        raise failure("configuration", {"field": "base_url"}) from None

    # 注入基础合规 Prompt
    source_messages = get_base_messages() + messages

    # 合并连续的 system 消息为一条（部分 LLM API 不支持多条 system 消息）
    merged = []
    for msg in source_messages:
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

    sensitive_values: set[str] = set()

    def collect_sensitive(value: Any) -> None:
        if isinstance(value, str):
            for candidate in (value, value.strip()):
                if candidate:
                    sensitive_values.update((candidate, json.dumps(candidate, ensure_ascii=True)[1:-1], json.dumps(candidate, ensure_ascii=False)[1:-1]))
        elif isinstance(value, dict):
            for item in value.values():
                collect_sensitive(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                collect_sensitive(item)

    # 上游可能在错误中回显完整请求，也可能只回显原文或 JSON 转义后的用户输入。
    collect_sensitive((api_key, base_url, provider, payload["model"], diagnostic_sensitive_values))
    for message in source_messages + full_messages:
        collect_sensitive(message.get("content"))
    logger.info("[LLM] 提交对话请求")

    try:
        client = httpx.AsyncClient(timeout=timeout)
    except (httpx.InvalidURL, TypeError, ValueError):
        raise failure("configuration", {"field": "http_client"}) from None
    try:
        async with client:
            response = await client.post(base_url, json=payload, headers=headers)
    except httpx.TimeoutException:
        raise failure("timeout") from None
    except httpx.InvalidURL:
        raise failure("configuration", {"field": "base_url"}) from None
    except httpx.RequestError:
        raise failure("connection") from None

    logger.info(f"[LLM] 响应状态码: {response.status_code}")

    if response.status_code != 200:
        raise failure("http", _image_error_diagnostics(response, sensitive_values=tuple(sensitive_values)))

    try:
        result = response.json()
    except (ValueError, UnicodeError, RecursionError):
        raise failure("invalid_json", {"http_status": response.status_code}) from None

    # 解析响应
    choices = result.get("choices") if isinstance(result, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise failure("invalid_response", {"field": "choices"})
    message = choices[0].get("message")
    if not isinstance(message, dict) or "content" not in message:
        raise failure("invalid_response", {"field": "message.content"})
    content = message["content"]
    if content is None:
        raise failure("empty_response")
    if not isinstance(content, str):
        raise failure("invalid_response", {"field": "message.content"})

    # 处理 markdown 代码块包装的 JSON（兼容两种情况）
    if "```" in content:
        parts = content.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                content = part[4:].strip()
                break
            elif part.startswith("{"):
                content = part
                break

    content = content.strip()
    if not content:
        raise failure("empty_response")
    logger.info("[LLM] 成功获取回复，长度: %s", len(content))
    return content


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


async def generate_image_generation(
    prompt: str,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    n: Literal[1] = 1,
    response_format: Literal["b64_json"] = "b64_json",
    timeout: Optional[float] = None,
    *,
    image: Optional[tuple[str, bytes, str]] = None,
    diagnostic_sensitive_values: tuple[str, ...] = (),
) -> dict[str, Any]:
    """无参考图调用 Generations；有参考图按文件上传到 Edits。"""
    if n != 1 or response_format != "b64_json":
        raise ValueError("图片生成仅支持 n=1、response_format=b64_json")

    provider = provider or settings.LLM_DEFAULT_PROVIDER
    config = settings.LLM_PROVIDERS.get(provider)

    if not config:
        raise ValueError(f"未配置 LLM 提供商: {provider}")

    api_key = config.get("api_key")
    generation_url = _image_generation_url_from_config(config)
    default_model = config.get("default_image_model", "gpt-image-2")
    request_timeout = timeout or config.get("image_timeout") or config.get("timeout", 180.0)

    if not generation_url:
        raise ValueError(f"提供商 {provider} 缺少图片生成 URL 配置。")

    request_url = httpx.URL(generation_url)
    if image is not None:
        path = request_url.path.rstrip("/")
        if not path.endswith("/images/generations"):
            raise ValueError("参考图请求需要从 /images/generations 地址确定 /images/edits 接口")
        request_url = request_url.copy_with(path=path[:-len("generations")] + "edits")

    payload: dict[str, Any] = {
        "model": model or default_model,
        "prompt": prompt,
        "n": n,
        "response_format": response_format,
    }

    headers = {"Authorization": f"Bearer {api_key}"}

    sensitive_values = tuple({
        candidate
        for value in (api_key, prompt, image[0] if image else None, *diagnostic_sensitive_values)
        if isinstance(value, str)
        for candidate in (value, value.strip())
        if candidate
    })
    request_diagnostics = {
        "provider": _safe_image_diagnostic_token(provider, sensitive_values),
        "model": _safe_image_diagnostic_token(payload["model"], sensitive_values),
        "request_format": "multipart" if image is not None else "json",
        "has_reference_image": image is not None,
        "reference_bytes": len(image[1]) if image is not None else 0,
        "reference_mime": _safe_image_diagnostic_token(image[2], sensitive_values) if image else None,
    }
    logger.info("[ImageGeneration] 提交图片生成 request=%s", json.dumps(request_diagnostics, ensure_ascii=True, sort_keys=True))

    started_at = monotonic()
    async with httpx.AsyncClient(timeout=request_timeout) as client:
        if image is None:
            response = await client.post(request_url, json=payload, headers=headers)
        else:
            response = await client.post(
                request_url,
                data={key: str(value) for key, value in payload.items()},
                files={"image": image},
                headers=headers,
            )

    if response.status_code != 200:
        diagnostics = {
            **request_diagnostics,
            **_image_error_diagnostics(response, sensitive_values=sensitive_values),
            "elapsed_ms": max(0, round((monotonic() - started_at) * 1000)),
        }
        raise ImageGenerationError(response.status_code, diagnostics)

    try:
        result = response.json()
    except ValueError:
        raise ValueError("图片生成接口返回了无效的 JSON") from None

    return parse_image_generation_result(result)


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

        raw_progress = info.get("progress")
        if raw_progress is None:
            raw_progress = fetch_res.get("progress")
        progress = _coerce_progress(raw_progress)

        if status == "SUCCESS":
            image_urls = info.get("imageUrl") or []
            result = {"status": "SUCCESS", "image_urls": image_urls}
        elif status == "FAILURE":
            status_dict = fetch_res.get("status") if isinstance(fetch_res.get("status"), dict) else {}
            msg = info.get("msg") or status_dict.get("msg") or "上游拉取报错"
            result = {"status": "FAILURE", "msg": msg}
        else:
            result = {"status": status}
        if progress is not None:
            result["progress"] = progress
        return result
