import pytest
from fastapi import HTTPException

from core.wechat import services as wechat_services
from core.wechat.services import WeChatService


class FakeResponse:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class FakeAsyncClient:
    response_data = {}
    last_url = None
    last_params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, params=None):
        FakeAsyncClient.last_url = url
        FakeAsyncClient.last_params = params
        return FakeResponse(FakeAsyncClient.response_data)


class FakeSettings:
    @staticmethod
    def get_wechat_config(appid):
        return {"secret": "test-secret"} if appid == "wx-test" else None


@pytest.fixture(autouse=True)
def fake_wechat_client(monkeypatch):
    monkeypatch.setattr(wechat_services, "settings", FakeSettings())
    monkeypatch.setattr(wechat_services.httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.last_url = None
    FakeAsyncClient.last_params = None


@pytest.mark.asyncio
async def test_exchange_miniapp_code_for_openid():
    FakeAsyncClient.response_data = {"openid": "mini-openid", "unionid": "union-id"}

    result = await WeChatService.exchange_miniapp_code_for_openid("wx-test", "mini-code")

    assert result == {"openid": "mini-openid", "unionid": "union-id"}
    assert FakeAsyncClient.last_url == "https://api.weixin.qq.com/sns/jscode2session"
    assert FakeAsyncClient.last_params == {
        "appid": "wx-test",
        "secret": "test-secret",
        "js_code": "mini-code",
        "grant_type": "authorization_code",
    }


@pytest.mark.asyncio
async def test_exchange_h5_code_for_openid():
    FakeAsyncClient.response_data = {"openid": "h5-openid"}

    result = await WeChatService.exchange_h5_code_for_openid("wx-test", "h5-code")

    assert result == {"openid": "h5-openid"}
    assert FakeAsyncClient.last_url == "https://api.weixin.qq.com/sns/oauth2/access_token"
    assert FakeAsyncClient.last_params == {
        "appid": "wx-test",
        "secret": "test-secret",
        "code": "h5-code",
        "grant_type": "authorization_code",
    }


@pytest.mark.asyncio
async def test_exchange_code_for_openid_handles_wechat_error():
    FakeAsyncClient.response_data = {"errcode": 40029, "errmsg": "invalid code"}

    with pytest.raises(HTTPException) as exc_info:
        await WeChatService.exchange_miniapp_code_for_openid("wx-test", "bad-code")

    assert exc_info.value.status_code == 400
    assert "invalid code" in exc_info.value.detail


@pytest.mark.asyncio
async def test_exchange_code_for_openid_requires_configured_appid():
    with pytest.raises(HTTPException) as exc_info:
        await WeChatService.exchange_h5_code_for_openid("missing-appid", "h5-code")

    assert exc_info.value.status_code == 400
    assert "未配置该公众号" in exc_info.value.detail
