"""
微信公众号服务
"""
from typing import Optional
from urllib.parse import urlencode

import httpx

from app.core.config import settings


class WeChatService:
    """微信公众号服务"""

    def __init__(self):
        self.app_id = settings.WECHAT_APP_ID
        self.app_secret = settings.WECHAT_APP_SECRET
        self.base_url = "https://api.weixin.qq.com"

    def get_oauth_url(self, redirect_uri: str, state: str = "", scope: str = "snsapi_userinfo") -> str:
        """
        生成微信授权页面URL

        Args:
            redirect_uri: 授权后重定向的回调地址
            state: 自定义状态参数
            scope: 授权作用域
                - snsapi_base: 静默授权，只能获取openid
                - snsapi_userinfo: 需用户确认，可获取用户信息

        Returns:
            微信授权页面URL
        """
        params = {
            "appid": self.app_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": scope,
            "state": state,
        }
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?{urlencode(params)}#wechat_redirect"

    async def get_access_token(self, code: str) -> dict:
        """
        通过授权码获取access_token和openid

        Args:
            code: 微信授权码

        Returns:
            {
                "access_token": "...",
                "expires_in": 7200,
                "refresh_token": "...",
                "openid": "...",
                "scope": "snsapi_userinfo"
            }
        """
        url = f"{self.base_url}/sns/oauth2/access_token"
        params = {
            "appid": self.app_id,
            "secret": self.app_secret,
            "code": code,
            "grant_type": "authorization_code",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def refresh_access_token(self, refresh_token: str) -> dict:
        """
        刷新access_token

        Args:
            refresh_token: 刷新令牌

        Returns:
            新的token信息
        """
        url = f"{self.base_url}/sns/oauth2/refresh_token"
        params = {
            "appid": self.app_id,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_user_info(self, access_token: str, openid: str) -> dict:
        """
        获取用户信息

        Args:
            access_token: 访问令牌
            openid: 用户唯一标识

        Returns:
            {
                "openid": "...",
                "nickname": "...",
                "sex": 1,
                "province": "...",
                "city": "...",
                "country": "...",
                "headimgurl": "...",
                "privilege": [],
                "unionid": "..."
            }
        """
        url = f"{self.base_url}/sns/userinfo"
        params = {
            "access_token": access_token,
            "openid": openid,
            "lang": "zh_CN",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_jsapi_ticket(self, access_token: str) -> dict:
        """
        获取jsapi_ticket（用于JS-SDK）

        Args:
            access_token: 全局access_token

        Returns:
            {"ticket": "...", "expires_in": 7200}
        """
        url = f"{self.base_url}/cgi-bin/ticket/getticket"
        params = {
            "access_token": access_token,
            "type": "jsapi",
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def get_global_access_token(self) -> dict:
        """
        获取全局access_token（用于调用公众号接口）

        Returns:
            {"access_token": "...", "expires_in": 7200}
        """
        url = f"{self.base_url}/cgi-bin/token"
        params = {
            "grant_type": "client_credential",
            "appid": self.app_id,
            "secret": self.app_secret,
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()

    async def send_template_message(
        self,
        touser: str,
        template_id: str,
        data: dict,
        url: Optional[str] = None,
        miniprogram: Optional[dict] = None,
    ) -> dict:
        """
        发送模板消息

        Args:
            touser: 接收者openid
            template_id: 模板ID
            data: 模板数据
            url: 点击后跳转的URL
            miniprogram: 跳转小程序配置

        Returns:
            {"errcode": 0, "msgid": 123, "errmsg": "ok"}
        """
        access_token_info = await self.get_global_access_token()
        access_token = access_token_info.get("access_token")

        api_url = f"{self.base_url}/cgi-bin/message/template/send"
        params = {"access_token": access_token}
        body = {
            "touser": touser,
            "template_id": template_id,
            "data": data,
        }
        if url:
            body["url"] = url
        if miniprogram:
            body["miniprogram"] = miniprogram

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(api_url, params=params, json=body)
            response.raise_for_status()
            return response.json()


# 单例
wechat_service = WeChatService()
