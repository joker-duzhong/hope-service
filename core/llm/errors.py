"""可安全展示给调用方的聊天模型异常。"""
from typing import Any, Literal, Optional


ChatErrorKind = Literal[
    "configuration", "timeout", "connection", "http",
    "invalid_json", "invalid_response", "empty_response",
]

CHAT_ERROR_MESSAGES: dict[ChatErrorKind, str] = {
    "configuration": "AI 服务配置不完整或无效，请联系管理员检查",
    "timeout": "AI 服务连接或响应超时，请稍后重试",
    "connection": "无法连接 AI 服务，请稍后重试",
    "http": "AI 服务请求失败",
    "invalid_json": "AI 服务返回了无效的 JSON，请稍后重试",
    "invalid_response": "AI 服务返回的响应格式异常，请稍后重试",
    "empty_response": "AI 服务返回了空回复，请稍后重试",
}


class ChatGenerationError(RuntimeError):
    """diagnostics 仅接收固定字段及已经脱敏的上游诊断。"""

    def __init__(self, kind: ChatErrorKind, diagnostics: Optional[dict[str, Any]] = None):
        self.kind = kind
        self.diagnostics = {**(diagnostics or {}), "kind": kind}
        message = CHAT_ERROR_MESSAGES[kind]
        if kind == "http":
            status = self.diagnostics.get("http_status")
            message = {
                401: "AI 服务认证失败",
                403: "AI 服务拒绝访问",
                404: "AI 服务请求地址或模型不存在",
                429: "AI 服务请求受限，请稍后重试",
                503: "AI 服务暂不可用，请稍后重试",
            }.get(status, message)
            if self.diagnostics.get("message_category") == "quota_exceeded":
                message = "AI 服务额度不足，请联系管理员检查"
            if isinstance(status, int):
                message += f"（上游 HTTP {status}）"
            if self.diagnostics.get("error_message"):
                message += f"：{self.diagnostics['error_message']}"
        super().__init__(message)
