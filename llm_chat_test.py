"""非流式 LLM 对话的 HTTP 契约、失败分类与脱敏测试。"""

import importlib.util
import json
import logging
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from tenacity import wait_none

from core.llm.errors import ChatGenerationError


TEST_API_KEY = "synthetic-chat-key-not-a-real-credential"
TEST_URL = "https://chat.example.test/v1/chat/completions"
USER_MESSAGE = "今天午饭35元，私有消息标记"


@pytest.fixture
def chat_engine(monkeypatch):
    fake_config = ModuleType("core.config")
    fake_config.settings = SimpleNamespace(
        LLM_DEFAULT_PROVIDER="chat-test",
        LLM_PROVIDERS={
            "chat-test": {
                "api_key": TEST_API_KEY,
                "base_url": TEST_URL,
                "default_model": "chat-test-model",
                "timeout": 23.0,
            },
        },
        private_settings_marker="synthetic-unrelated-settings-secret",
    )
    # 独立模块只在加载期间使用假配置，避免读取 .env 或替换其他测试的 engine。
    spec = importlib.util.spec_from_file_location(
        "_llm_chat_test_engine", Path(__file__).parent / "core" / "llm" / "engine.py"
    )
    module = importlib.util.module_from_spec(spec)
    with monkeypatch.context() as loading_patch:
        loading_patch.setitem(sys.modules, "core.config", fake_config)
        spec.loader.exec_module(module)
    module.generate_chat = module.generate_chat.retry_with(wait=wait_none())
    return module


@pytest.fixture
def mock_chat_api(chat_engine, monkeypatch):
    async_client = httpx.AsyncClient

    def install(response):
        requests = []

        def handle(request):
            requests.append(request)
            if isinstance(response, Exception):
                raise response
            return response

        transport = httpx.MockTransport(handle)
        monkeypatch.setattr(
            chat_engine.httpx, "AsyncClient",
            lambda **kwargs: async_client(transport=transport, **kwargs),
        )
        return requests

    return install


def captured_error(error, caplog):
    return caplog.text + repr(error) + json.dumps(error.diagnostics, ensure_ascii=False)


@pytest.mark.asyncio
async def test_invalid_proxy_configuration_is_safe_and_never_sends_request(chat_engine, monkeypatch, caplog):
    private_proxy = "http://synthetic-private-proxy.example.test:not-a-port"
    monkeypatch.setenv("HTTP_PROXY", private_proxy)
    post = AsyncMock(side_effect=AssertionError("network request must not occur"))
    monkeypatch.setattr(chat_engine.httpx.AsyncClient, "post", post)

    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert error.value.kind == "configuration"
    assert error.value.diagnostics["field"] == "http_client"
    post.assert_not_awaited()
    captured = captured_error(error.value, caplog)
    assert private_proxy not in captured and "synthetic-private-proxy" not in captured


@pytest.mark.asyncio
async def test_success_preserves_json_mode_merges_system_messages_and_strips_fences(
    chat_engine, mock_chat_api, monkeypatch, caplog,
):
    base_messages = [{"role": "system", "content": "基础提示词"}]
    messages = [
        {"role": "system", "content": "记账规则"},
        {"role": "system", "content": "输出 JSON"},
        {"role": "user", "content": USER_MESSAGE},
    ]
    original_messages = [message.copy() for message in messages]
    monkeypatch.setattr(chat_engine, "get_base_messages", lambda: base_messages)
    private_reply = '{"reply":"私有模型回复标记","amount":3500}'
    requests = mock_chat_api(httpx.Response(200, json={
        "choices": [{"message": {"content": f"```json\n{private_reply}\n```"}}],
    }))

    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__):
        result = await chat_engine.generate_chat(
            messages, model="custom-chat-model", response_format={"type": "json_object"},
            temperature=0.2, stream=True, diagnostic_sensitive_values=("原始用户输入标记",),
        )

    assert result == private_reply
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == TEST_URL
    assert request.headers["authorization"] == f"Bearer {TEST_API_KEY}"
    assert request.extensions["timeout"]["read"] == 23.0
    assert json.loads(request.content) == {
        "model": "custom-chat-model",
        "messages": [
            {"role": "system", "content": "基础提示词\n\n记账规则\n\n输出 JSON"},
            {"role": "user", "content": USER_MESSAGE},
        ],
        "stream": False,
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    assert messages == original_messages
    assert base_messages == [{"role": "system", "content": "基础提示词"}]
    for private in (USER_MESSAGE, "私有模型回复标记", TEST_API_KEY, TEST_URL):
        assert private not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403, 429, 503])
async def test_http_errors_retain_actionable_diagnostics_after_retries(
    chat_engine, mock_chat_api, status_code,
):
    requests = mock_chat_api(httpx.Response(status_code, json={"error": {
        "message": "Unsupported parameter: response_format",
        "code": "unsupported_parameter", "type": "invalid_request_error",
        "param": "response_format",
    }}, headers={"x-request-id": "req-chat-123"}))

    with pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert len(requests) == 3
    assert error.value.kind == "http"
    diagnostics = error.value.diagnostics
    assert diagnostics["http_status"] == status_code
    assert diagnostics["error_message"] == "Unsupported parameter: response_format"
    assert diagnostics["error_code"] == "unsupported_parameter"
    assert diagnostics["error_type"] == "invalid_request_error"
    assert diagnostics["error_param"] == "response_format"
    assert diagnostics["upstream_request_id"] == "req-chat-123"
    assert str(status_code) in str(error.value)
    assert "response_format" in str(error.value)
    assert not hasattr(error.value, "response")


@pytest.mark.asyncio
@pytest.mark.parametrize("body, content_type, expected_body", [
    (b"<html>private-upstream-body</html>", "text/html", "non_json"),
    (b'["private-upstream-body"]', "application/json", "non_object_json"),
    (b"private-upstream-body" * 2000, "text/plain", "omitted_oversize"),
], ids=["html", "non-object-json", "oversize"])
async def test_http_errors_omit_raw_non_json_and_large_bodies(
    chat_engine, mock_chat_api, caplog, body, content_type, expected_body,
):
    requests = mock_chat_api(httpx.Response(503, content=body, headers={
        "content-type": content_type, "x-request-id": "req-gateway-503",
    }))
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert len(requests) == 3
    assert error.value.kind == "http"
    assert error.value.diagnostics["error_body"] == expected_body
    assert error.value.diagnostics["upstream_request_id"] == "req-gateway-503"
    assert "private-upstream-body" not in captured_error(error.value, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("exception_type, expected_kind", [
    (httpx.ReadTimeout, "timeout"),
    (httpx.ConnectTimeout, "timeout"),
    (httpx.ConnectError, "connection"),
])
async def test_transport_errors_are_classified_without_exposing_exception_text(
    chat_engine, mock_chat_api, caplog, exception_type, expected_kind,
):
    private_text = f"transport-private-detail {TEST_URL} {TEST_API_KEY} {USER_MESSAGE}"
    requests = mock_chat_api(exception_type(private_text))
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert len(requests) == 3
    assert error.value.kind == expected_kind
    captured = captured_error(error.value, caplog)
    for private in ("transport-private-detail", TEST_URL, TEST_API_KEY, USER_MESSAGE):
        assert private not in captured


@pytest.mark.asyncio
async def test_success_status_with_non_json_body_is_invalid_json(
    chat_engine, mock_chat_api, caplog,
):
    requests = mock_chat_api(httpx.Response(200, text="private-invalid-json-body"))
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert len(requests) == 3
    assert error.value.kind == "invalid_json"
    assert "private-invalid-json-body" not in captured_error(error.value, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("body", [
    [],
    {"private-upstream-body": "private-response-marker"},
    {"choices": []},
    {"choices": {"0": {"message": {"content": "private-response-marker"}}}},
    {"choices": [None]},
    {"choices": [{"message": None}]},
    {"choices": [{"message": {"content": ["private-response-marker"]}}]},
])
async def test_invalid_response_shapes_raise_typed_errors_without_body(
    chat_engine, mock_chat_api, caplog, body,
):
    requests = mock_chat_api(httpx.Response(200, json=body))
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert len(requests) == 3
    assert error.value.kind == "invalid_response"
    assert "private-response-marker" not in captured_error(error.value, caplog)


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, "", " \n\t ", "```json\n \n```"])
async def test_empty_content_is_detected_after_normalization(chat_engine, mock_chat_api, content):
    requests = mock_chat_api(httpx.Response(200, json={
        "choices": [{"message": {"content": content}}],
    }))
    with pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])
    assert len(requests) == 3
    assert error.value.kind == "empty_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_field", ["provider", "api_key", "base_url"])
async def test_missing_configuration_fails_before_network_without_settings_repr(
    chat_engine, mock_chat_api, caplog, missing_field,
):
    requests = mock_chat_api(httpx.Response(200, json={}))
    if missing_field == "provider":
        chat_engine.settings.LLM_PROVIDERS = {}
    else:
        chat_engine.settings.LLM_PROVIDERS["chat-test"].pop(missing_field)
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([{"role": "user", "content": USER_MESSAGE}])

    assert requests == []
    assert error.value.kind == "configuration"
    captured = captured_error(error.value, caplog)
    for private in ("synthetic-unrelated-settings-secret", TEST_API_KEY, TEST_URL, USER_MESSAGE):
        assert private not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("escaped", [False, True], ids=["plain-echo", "escaped-echo"])
async def test_all_diagnostics_and_logs_redact_credentials_urls_and_message_echoes(
    chat_engine, mock_chat_api, caplog, escaped,
):
    private_key = 'synthetic-key-with-"quotes"-and-\\slashes'
    private_message = '私有记账内容"引号"\n换行标记'
    private_original_input = "私有原始输入：午饭35元"
    private_url = "https://private.example.test/v1/chat/completions?token=synthetic-url-secret"
    chat_engine.settings.LLM_PROVIDERS["chat-test"].update(
        api_key=private_key, base_url=private_url,
    )

    def echo(value):
        return json.dumps(value, ensure_ascii=True)[1:-1] if escaped else value

    requests = mock_chat_api(httpx.Response(400, json={"error": {
        "code": "prefix-" + echo(private_key),
        "type": echo(private_message),
        "param": echo(private_url),
        "message": "Invalid parameter response_format; echoed input: "
        + " | ".join(echo(value) for value in (
            private_key, private_message, private_original_input, private_url,
        )),
    }}, headers={
        "x-request-id": "synthetic-credential-request-id",
        "x-ignored-header": "private-unused-header",
    }))
    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat(
            [{"role": "user", "content": private_message}],
            diagnostic_sensitive_values=(private_original_input,),
        )

    assert len(requests) == 3
    assert error.value.kind == "http"
    assert "response_format" in error.value.diagnostics["error_message"]
    captured = captured_error(error.value, caplog)
    for private in (
        private_key, private_message, private_original_input, private_url, "private-unused-header",
    ):
        assert private not in captured
        assert echo(private) not in captured
    assert "synthetic-url-secret" not in captured


@pytest.mark.asyncio
async def test_original_system_message_echo_is_redacted_after_messages_are_merged(
    chat_engine, mock_chat_api, caplog,
):
    private_system_rule = "合成私有系统规则：synthetic-system-secret-marker"
    requests = mock_chat_api(httpx.Response(400, json={"error": {
        "message": "Unsupported parameter response_format; echoed system: " + private_system_rule,
    }}))

    with caplog.at_level(logging.DEBUG, logger=chat_engine.__name__), pytest.raises(ChatGenerationError) as error:
        await chat_engine.generate_chat([
            {"role": "system", "content": private_system_rule},
            {"role": "system", "content": "返回账单 JSON 数据"},
            {"role": "user", "content": USER_MESSAGE},
        ])

    assert len(requests) == 3
    assert error.value.kind == "http"
    assert "Unsupported parameter response_format" in error.value.diagnostics["error_message"]
    captured = captured_error(error.value, caplog)
    assert private_system_rule not in captured
    assert "synthetic-system-secret-marker" not in captured
