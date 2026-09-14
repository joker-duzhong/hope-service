"""OneAPI 图片生成请求与响应契约测试。"""

import json
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
async def test_generation_with_reference_sends_image_file_to_same_endpoint(mock_image_api):
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
    assert str(request.url) == "https://oneapi.example.com/v1/images/generations"
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
@pytest.mark.parametrize("status_code", [400, 429, 500])
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
