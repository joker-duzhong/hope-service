"""OneAPI 图片生成请求与响应契约测试。"""

import json
import logging
from email import policy
from email.parser import BytesParser

import httpx
import pytest

from core.llm import engine


@pytest.fixture
def mock_image_api(monkeypatch):
    monkeypatch.setattr(engine.settings, "LLM_DEFAULT_PROVIDER", "image-test")
    monkeypatch.setattr(
        engine.settings,
        "LLM_PROVIDERS",
        {
            "image-test": {
                "base_url": "https://oneapi.example.com/v1",
                "api_key": "test-key-not-a-real-credential",
                "image_timeout": 75.0,
            }
        },
    )
    async_client = httpx.AsyncClient

    def install(response):
        requests = []

        def handle(request):
            requests.append(request)
            if isinstance(response, Exception):
                raise response
            return response

        transport = httpx.MockTransport(handle)

        def create_client(**kwargs):
            return async_client(transport=transport, **kwargs)

        monkeypatch.setattr(engine.httpx, "AsyncClient", create_client)
        return requests

    return install


@pytest.mark.asyncio
async def test_generation_without_reference_sends_documented_json_fields(mock_image_api):
    requests = mock_image_api(
        httpx.Response(200, json={"data": [{"b64_json": "cG5nLWJ5dGVz"}]})
    )

    result = await engine.generate_image_generation(prompt="生成一只戴帽子的猫")

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://oneapi.example.com/v1/images/generations"
    assert request.headers["content-type"] == "application/json"
    assert request.headers["authorization"] == "Bearer test-key-not-a-real-credential"
    assert json.loads(request.content) == {
        "model": "gpt-image-2",
        "prompt": "生成一只戴帽子的猫",
        "n": 1,
        "response_format": "b64_json",
    }
    assert request.extensions["timeout"]["read"] == 75.0
    assert result["b64_json"] == "cG5nLWJ5dGVz"


@pytest.mark.asyncio
@pytest.mark.parametrize("url_config, expected_url", [
    ({"base_url": "https://oneapi.example.com/v1"}, "https://oneapi.example.com/v1/images/edits"),
    ({"base_url": "https://oneapi.example.com/v1/chat/completions"}, "https://oneapi.example.com/v1/images/edits"),
    ({"image_chat_url": "https://oneapi.example.com/proxy/v1/chat/completions"}, "https://oneapi.example.com/proxy/v1/images/edits"),
    ({"image_generation_url": "https://oneapi.example.com/custom/v1/images/generations/?route=test"}, "https://oneapi.example.com/custom/v1/images/edits?route=test"),
    ({"image_generations_url": "https://oneapi.example.com/v1/images/generations"}, "https://oneapi.example.com/v1/images/edits"),
])
async def test_generation_with_reference_sends_image_file_to_edits(mock_image_api, monkeypatch, url_config, expected_url):
    monkeypatch.setitem(engine.settings.LLM_PROVIDERS, "image-test", {
        "api_key": "test-key-not-a-real-credential", **url_config,
    })
    requests = mock_image_api(
        httpx.Response(200, json={"data": [{"b64_json": "cG5nLWJ5dGVz"}]})
    )
    reference_bytes = b"\x89PNG\r\n\x1a\nreference-image"

    await engine.generate_image_generation(
        prompt="保留参考图人物，给天空添加彩虹",
        model="configured-image-model",
        image=("reference.png", reference_bytes, "image/png"),
        timeout=42.0,
    )

    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == expected_url
    content_type = request.headers["content-type"]
    assert content_type.startswith("multipart/form-data; boundary=")
    message = BytesParser(policy=policy.default).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("ascii")
        + request.content
    )
    parts = list(message.iter_parts())
    assert len(parts) == 5
    fields = {part.get_param("name", header="content-disposition"): part for part in parts}
    assert set(fields) == {"model", "prompt", "n", "response_format", "image"}
    assert {
        name: fields[name].get_payload(decode=True).decode("utf-8")
        for name in ("model", "prompt", "n", "response_format")
    } == {
        "model": "configured-image-model",
        "prompt": "保留参考图人物，给天空添加彩虹",
        "n": "1",
        "response_format": "b64_json",
    }
    assert fields["image"].get_filename() == "reference.png"
    assert fields["image"].get_content_type() == "image/png"
    assert fields["image"].get_payload(decode=True) == reference_bytes
    assert request.extensions["timeout"]["read"] == 42.0


@pytest.mark.asyncio
async def test_reference_request_rejects_nonstandard_endpoint_before_sending(mock_image_api, monkeypatch):
    monkeypatch.setitem(engine.settings.LLM_PROVIDERS["image-test"], "image_generation_url", "https://oneapi.example.com/custom-api")
    requests = mock_image_api(httpx.Response(200, json={"data": [{"b64_json": "cG5n"}]}))
    with pytest.raises(ValueError, match="/images/edits"):
        await engine.generate_image_generation(prompt="生成图片", image=("ref.png", b"png-bytes", "image/png"))
    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kwargs",
    [{"n": 0}, {"n": 2}, {"n": None}, {"response_format": "url"}],
)
async def test_generation_rejects_unsupported_options_before_request(mock_image_api, kwargs):
    requests = mock_image_api(
        httpx.Response(200, json={"data": [{"b64_json": "cG5nLWJ5dGVz"}]})
    )

    with pytest.raises(ValueError):
        await engine.generate_image_generation(prompt="生成图片", **kwargs)

    assert requests == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 429, 500, 503])
async def test_generation_http_failure_is_not_retried_or_exposed(
    mock_image_api, caplog, status_code
):
    response_body = "upstream-private-response-marker"
    requests = mock_image_api(httpx.Response(status_code, text=response_body))

    with pytest.raises(RuntimeError) as error:
        await engine.generate_image_generation(prompt="生成图片")

    assert len(requests) == 1
    assert str(status_code) in str(error.value)
    assert response_body not in str(error.value)
    assert response_body not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 503])
@pytest.mark.parametrize("with_reference", [False, True])
async def test_http_failure_retains_safe_diagnostics(mock_image_api, monkeypatch, status_code, with_reference):
    requests = mock_image_api(httpx.Response(status_code, json={"error": {
        "code": "channel_not_found", "type": "upstream_error", "param": "model",
        "message": "No available channel for this model.",
    }}, headers={"x-request-id": "req-upstream-123"}))
    clock = iter([10.0, 10.125])
    monkeypatch.setattr(engine, "monotonic", lambda: next(clock))
    reference = ("private-reference.png", b"reference-bytes", "image/png") if with_reference else None

    with pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat", image=reference)

    assert len(requests) == 1
    assert str(error.value) == f"Image generation API 返回错误 HTTP {status_code}: No available channel for this model."
    diagnostics = error.value.diagnostics
    assert diagnostics["http_status"] == status_code
    assert diagnostics["provider"] == "image-test"
    assert diagnostics["model"] == "gpt-image-2"
    assert diagnostics["request_format"] == ("multipart" if with_reference else "json")
    assert diagnostics["has_reference_image"] is with_reference
    assert diagnostics["reference_bytes"] == (15 if with_reference else 0)
    assert diagnostics["reference_mime"] == ("image/png" if with_reference else None)
    assert diagnostics["error_code"] == "channel_not_found"
    assert diagnostics["error_type"] == "upstream_error"
    assert diagnostics["error_param"] == "model"
    assert diagnostics["message_category"] == "channel_unavailable"
    assert diagnostics["upstream_request_id"] == "req-upstream-123"
    assert diagnostics["elapsed_ms"] == 125
    assert diagnostics["response_content_type"] == "application/json"
    assert diagnostics["error_message"] == "No available channel for this model."
    assert not hasattr(error.value, "response")


@pytest.mark.asyncio
async def test_http_diagnostics_redact_echoes_in_every_field_including_info_logs(mock_image_api, monkeypatch, caplog):
    api_key = "synthetic-private-key-for-redaction"
    original_prompt = "private-scene-marker"
    prompt = original_prompt + ", 图片比例为:1:1"
    filename = "private-file-marker.png"
    monkeypatch.setattr(engine.settings, "LLM_DEFAULT_PROVIDER", api_key)
    monkeypatch.setattr(engine.settings, "LLM_PROVIDERS", {api_key: {
        "base_url": "https://oneapi.example.com/v1", "api_key": api_key,
    }})
    raw_message = f"invalid request: {api_key} {original_prompt} {filename} data:image/png;base64,cHJpdmF0ZS1pbWFnZQ=="
    mock_image_api(httpx.Response(400, json={"error": {
        "code": "prefix-" + api_key + "-suffix",
        "type": "prefix-" + original_prompt + "-suffix",
        "param": filename, "message": raw_message,
    }}, headers={"x-request-id": api_key, "x-ignored-header": "private-extra-header"}))

    with caplog.at_level(logging.INFO), pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(
            prompt=prompt, model=api_key, image=(filename, b"private-image", "image/png"),
            diagnostic_sensitive_values=(original_prompt,),
        )

    diagnostics = error.value.diagnostics
    for field in ("provider", "model", "error_code", "error_type", "error_param", "upstream_request_id"):
        assert diagnostics[field] == "[redacted]"
    assert diagnostics["message_category"] == "invalid_request"
    captured = caplog.text + str(error.value) + repr(error.value) + json.dumps(diagnostics)
    for private in (api_key, original_prompt, filename, "private-extra-header", "cHJpdmF0ZS1pbWFnZQ=="):
        assert private not in captured


@pytest.mark.asyncio
@pytest.mark.parametrize("body, category", [
    ({"message": "余额不足，请充值后重试", "code": 503}, "quota_exceeded"),
    ({"error": "Unsupported image format"}, "invalid_image"),
    ({"error": {"message": "Unsupported parameter response_format"}}, "unsupported_parameter"),
    ({"error": {"message": "invalid character '-' in numeric literal"}}, "unclassified"),
])
async def test_http_error_message_is_displayed_and_classified(mock_image_api, caplog, body, category):
    mock_image_api(httpx.Response(400, json=body))
    with caplog.at_level(logging.INFO), pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat")
    assert error.value.diagnostics["message_category"] == category
    fields = body.get("error", body)
    expected_message = fields if isinstance(fields, str) else fields["message"]
    assert error.value.diagnostics["error_message"] == expected_message
    assert str(error.value).endswith(": " + expected_message)
    if "code" in body:
        assert error.value.diagnostics["error_code"] == "503"


@pytest.mark.asyncio
async def test_error_message_masks_credentials_and_limits_log_output(mock_image_api):
    mock_image_api(httpx.Response(400, json={"error": {"message": (
        "Invalid body\r\nAuthorization: Bearer synthetic-bearer-value; "
        "api_key='synthetic-quoted-key'; password=synthetic-password; token=synthetic-short-token; sk-synthetic-key "
        "https://example.test/?token=synthetic-url-token "
        + "A" * 90 + " " + "详细错误。" * 300
    )}}))
    with pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat")
    message = error.value.diagnostics["error_message"]
    assert message.startswith("Invalid body")
    assert len(message) <= 1001
    assert message.endswith("…")
    assert "\r" not in message and "\n" not in message
    for secret in ("synthetic-bearer-value", "synthetic-quoted-key", "synthetic-password", "synthetic-short-token", "sk-synthetic-key", "synthetic-url-token", "A" * 90):
        assert secret not in message


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [True, ["private-marker"], {"value": "private-marker"}, 10**30])
async def test_http_error_ignores_non_token_fields(mock_image_api, value):
    mock_image_api(httpx.Response(400, json={"error": {"code": value, "type": value, "param": value, "message": value}}))
    with pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat")
    assert not {"error_code", "error_type", "error_param", "message_category"} & error.value.diagnostics.keys()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [
    "injected\nforged-log-line", "https://private.example.test/path?token=secret",
    "a" * 129, "Bearer-private-credential", "sk-private-credential",
])
async def test_http_diagnostic_tokens_reject_injection_urls_and_credential_shapes(mock_image_api, caplog, value):
    mock_image_api(httpx.Response(400, json={"error": {"code": value, "type": value, "param": value}}, headers={"x-request-id": value}))
    with caplog.at_level(logging.INFO), pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat")
    for field in ("error_code", "error_type", "error_param", "upstream_request_id"):
        assert error.value.diagnostics[field] == "[omitted]"
    assert value not in caplog.text + json.dumps(error.value.diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize("body, content_type, expected_formats", [
    (b"<html>private-marker</html>", "text/html", {"non_json"}),
    (b"{invalid-json-private-marker", "application/json", {"non_json"}),
    (b'["private-marker"]', "application/json", {"non_object_json"}),
    (b"[" * 2000 + b"0" + b"]" * 2000, "application/json", {"non_json", "non_object_json"}),
    (b"private-marker" * 2000, "text/plain", {"omitted_oversize"}),
], ids=["html", "invalid-json", "non-object", "deep-json", "oversize"])
async def test_http_diagnostics_handle_non_json_and_large_errors(mock_image_api, caplog, body, content_type, expected_formats):
    requests = mock_image_api(httpx.Response(503, content=body, headers={
        "content-type": content_type, "x-request-id": "req-gateway-503",
    }))
    with caplog.at_level(logging.INFO), pytest.raises(engine.ImageGenerationError) as error:
        await engine.generate_image_generation(prompt="draw a cat")
    assert len(requests) == 1
    assert error.value.diagnostics["error_body"] in expected_formats
    assert error.value.diagnostics["upstream_request_id"] == "req-gateway-503"
    assert error.value.diagnostics["response_content_type"] == content_type
    assert "private-marker" not in caplog.text + json.dumps(error.value.diagnostics)


@pytest.mark.asyncio
async def test_generation_invalid_json_is_not_retried_or_exposed(mock_image_api, caplog):
    response_body = "upstream-private-invalid-json-marker"
    requests = mock_image_api(httpx.Response(200, text=response_body))

    with pytest.raises(ValueError) as error:
        await engine.generate_image_generation(prompt="生成图片")

    assert len(requests) == 1
    assert response_body not in str(error.value)
    assert response_body not in caplog.text


@pytest.mark.asyncio
async def test_generation_timeout_is_not_retried(mock_image_api):
    requests = mock_image_api(httpx.ReadTimeout("模拟上游超时"))

    with pytest.raises(httpx.ReadTimeout):
        await engine.generate_image_generation(prompt="生成图片")

    assert len(requests) == 1


@pytest.mark.parametrize(
    "result",
    [
        None,
        [],
        "private-response-marker",
        {"data": [], "detail": "private-response-marker"},
        {"data": {}, "detail": "private-response-marker"},
        {"data": [None], "detail": "private-response-marker"},
        {"data": [{"b64_json": 123}], "detail": "private-response-marker"},
    ],
)
def test_generation_parser_rejects_invalid_data_without_response_body(result):
    with pytest.raises(ValueError) as error:
        engine.parse_image_generation_result(result)

    assert "private-response-marker" not in str(error.value)


def test_generation_parser_preserves_base64_metadata_and_usage():
    usage = {"prompt_tokens": 10, "total_tokens": 10}

    result = engine.parse_image_generation_result(
        {
            "created": 1716841200,
            "data": [{"b64_json": "cG5nLWJ5dGVz"}],
            "model": "gpt-image-2",
            "object": "list",
            "usage": usage,
        }
    )

    assert result["b64_json"] == "cG5nLWJ5dGVz"
    assert result["image_url"] is None
    assert result["mime_type"] == "image/png"
    assert result["created"] == 1716841200
    assert result["model"] == "gpt-image-2"
    assert result["object"] == "list"
    assert result["usage"] == usage


def test_generation_parser_preserves_existing_url_response_support():
    result = engine.parse_image_generation_result(
        {
            "data": [{"url": "https://cdn.example.com/result.png"}],
            "usage": {"total_tokens": 10},
        }
    )

    assert result["b64_json"] is None
    assert result["image_url"] == "https://cdn.example.com/result.png"
    assert result["download_url"] == "https://cdn.example.com/result.png"
    assert result["usage"] == {"total_tokens": 10}


def test_generation_parser_reads_mime_type_from_base64_data_url():
    result = engine.parse_image_generation_result(
        {"data": [{"b64_json": "data:image/jpeg;base64,aW1hZ2UtYnl0ZXM="}]}
    )

    assert result["b64_json"] == "aW1hZ2UtYnl0ZXM="
    assert result["mime_type"] == "image/jpeg"
