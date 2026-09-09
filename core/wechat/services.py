import json
import httpx
import uuid
import hashlib
import urllib.parse
import secrets
import time
from typing import Any, Dict, Optional
from fastapi import HTTPException
from core.redis_client import redis_client
from core.config import settings
from core.sms import normalize_phone

WECHAT_API_BASE_URL = "https://api.weixin.qq.com/cgi-bin"
WECHAT_SNS_BASE_URL = "https://api.weixin.qq.com/sns"


class WeChatService:
    @staticmethod
    def get_default_appid() -> str:
        if not settings.WECHAT_APPS:
            raise HTTPException(status_code=400, detail="未配置微信公众号")

        first_config = settings.WECHAT_APPS.split(",", 1)[0].strip()
        appid = first_config.split(":", 1)[0].strip()
        if not appid:
            raise HTTPException(status_code=400, detail="未配置微信公众号 AppID")
        return appid

    @staticmethod
    def _get_secret(appid: str, app_type: str) -> str:
        config = settings.get_wechat_config(appid)
        if not config or not config.get("secret"):
            raise HTTPException(status_code=400, detail=f"未配置该{app_type}: {appid}")
        return config["secret"]

    @staticmethod
    def _build_openid_response(data: Dict[str, Any], error_prefix: str) -> dict:
        if "errcode" in data and data["errcode"] != 0:
            raise HTTPException(
                status_code=400,
                detail=f"{error_prefix}: {data.get('errmsg', '未知错误')}",
            )

        openid = data.get("openid")
        if not openid:
            raise HTTPException(status_code=400, detail="获取 openid 失败")

        result = {"openid": openid}
        if data.get("unionid"):
            result["unionid"] = data["unionid"]
        return result

    @staticmethod
    async def exchange_miniapp_code_for_openid(appid: str, code: str) -> dict:
        secret = WeChatService._get_secret(appid, "小程序")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{WECHAT_SNS_BASE_URL}/jscode2session",
                params={
                    "appid": appid,
                    "secret": secret,
                    "js_code": code,
                    "grant_type": "authorization_code",
                },
            )
            data = resp.json()

        return WeChatService._build_openid_response(data, "微信小程序登录失败")

    @staticmethod
    async def exchange_h5_code_for_openid(appid: str, code: str) -> dict:
        secret = WeChatService._get_secret(appid, "公众号")
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{WECHAT_SNS_BASE_URL}/oauth2/access_token",
                params={
                    "appid": appid,
                    "secret": secret,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            data = resp.json()

        return WeChatService._build_openid_response(data, "微信网页授权失败")

    @staticmethod
    async def exchange_phone_code(appid: str, code: str) -> str:
        secret = WeChatService._get_secret(appid, "小程序")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(f"{WECHAT_API_BASE_URL}/token", params={
                    "grant_type": "client_credential", "appid": appid, "secret": secret,
                })
                response.raise_for_status()
                token_data = response.json()
                access_token = token_data.get("access_token")
                if not isinstance(access_token, str) or not access_token:
                    raise HTTPException(502, "微信手机号服务暂不可用，请重新登录")
                response = await client.post(
                    "https://api.weixin.qq.com/wxa/business/getuserphonenumber",
                    params={"access_token": access_token}, json={"code": code},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("errcode") != 0:
                    raise HTTPException(400, "手机号授权已失效，请重新登录并授权手机号")
                info = data.get("phone_info", {})
                if str(info.get("countryCode")) != "86":
                    raise HTTPException(400, "当前仅支持中国大陆手机号")
                return normalize_phone(info.get("purePhoneNumber") or info.get("phoneNumber"))
        except (httpx.HTTPError, ValueError, TypeError, AttributeError):
            raise HTTPException(502, "微信手机号验证失败，请重新登录后重试") from None

    @staticmethod
    async def get_jsapi_ticket(appid: str) -> str:
        access_token = await WeChatService.get_access_token(appid)
        redis_key = f"wechat_jsapi_ticket:{appid}"
        try:
            ticket = await redis_client.get(redis_key)
            if ticket:
                return ticket.decode("utf-8") if isinstance(ticket, bytes) else ticket
        except Exception as e:
            print(f"Redis error when getting jsapi_ticket: {e}")

        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{WECHAT_API_BASE_URL}/ticket/getticket",
                params={"access_token": access_token, "type": "jsapi"},
            )
            data = resp.json()

        if data.get("errcode") not in (None, 0):
            raise HTTPException(
                status_code=400,
                detail=f"获取微信 jsapi_ticket 失败: {data.get('errmsg', '未知错误')}",
            )

        ticket = data.get("ticket")
        if not ticket:
            raise HTTPException(status_code=400, detail="获取微信 jsapi_ticket 失败")

        expires_in = int(data.get("expires_in") or 7200)
        try:
            await redis_client.setex(redis_key, max(expires_in - 200, 300), ticket)
        except Exception as e:
            print(f"Redis error when caching jsapi_ticket: {e}")
        return ticket

    @staticmethod
    async def create_jssdk_config(url: str, appid: Optional[str] = None) -> dict:
        resolved_appid = appid or WeChatService.get_default_appid()
        if not settings.get_wechat_config(resolved_appid):
            raise HTTPException(status_code=400, detail=f"未配置该公众号: {resolved_appid}")

        ticket = await WeChatService.get_jsapi_ticket(resolved_appid)
        timestamp = int(time.time())
        nonce_str = secrets.token_urlsafe(16)
        raw = (
            f"jsapi_ticket={ticket}"
            f"&noncestr={nonce_str}"
            f"&timestamp={timestamp}"
            f"&url={url}"
        )
        signature = hashlib.sha1(raw.encode("utf-8")).hexdigest()
        return {
            "appId": resolved_appid,
            "timestamp": timestamp,
            "nonceStr": nonce_str,
            "signature": signature,
        }

    @staticmethod
    async def get_access_token(appid: str) -> str:
        config = settings.get_wechat_config(appid)
        if not config:
            raise HTTPException(status_code=400, detail=f"WeChat config not found for appid: {appid}")

        secret = config.get("secret")
        if not secret:
            raise HTTPException(status_code=400, detail=f"WeChat secret not configured for appid: {appid}")

        redis_key = f"wechat_access_token:{appid}"
        try:
            token = await redis_client.get(redis_key)
            if token:
                return token
        except Exception as e:
            print(f"Redis error when getting access token: {e}")

        url = f"{WECHAT_API_BASE_URL}/token?grant_type=client_credential&appid={appid}&secret={secret}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()
            if "access_token" in data:
                token = data["access_token"]
                try:
                    await redis_client.setex(redis_key, 7000, token)
                except Exception as e:
                    print(f"Redis error when caching access token: {e}")
                return token

            errcode = data.get("errcode", "unknown")
            errmsg = data.get("errmsg", "unknown")
            print(f"WeChat token API error: errcode={errcode}, errmsg={errmsg}, appid={appid}")
            raise HTTPException(status_code=400, detail=f"获取微信access_token失败: {errmsg} (code: {errcode})")

    @staticmethod
    async def create_qrcode(appid: str, browser_token: str) -> dict:
        # 检查配置是否存在
        config = settings.get_wechat_config(appid)
        if not config:
            raise HTTPException(status_code=400, detail=f"WeChat config not found for appid: {appid}")

        if not config.get("secret"):
            raise HTTPException(status_code=400, detail=f"WeChat secret not configured for appid: {appid}")

        scene_id = str(uuid.uuid4())
        access_token = await WeChatService.get_access_token(appid)

        url = f"{WECHAT_API_BASE_URL}/qrcode/create?access_token={access_token}"
        payload = {
            "expire_seconds": 300,
            "action_name": "QR_STR_SCENE",
            "action_info": {"scene": {"scene_str": scene_id}}
        }

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if "ticket" in data:
                ticket = data["ticket"]
                qr_url = f"https://mp.weixin.qq.com/cgi-bin/showqrcode?ticket={urllib.parse.quote(ticket)}"

                await redis_client.setex(
                    f"wechat_scan:{scene_id}",
                    300,
                    json.dumps({"status": "WAITING", "browser_token": browser_token}),
                )

                return {"scene_id": scene_id, "qr_url": qr_url}

            # 微信返回错误
            errcode = data.get("errcode", "unknown")
            errmsg = data.get("errmsg", "unknown")
            print(f"WeChat API error: errcode={errcode}, errmsg={errmsg}, appid={appid}")
            raise HTTPException(status_code=400, detail=f"微信接口错误: {errmsg} (code: {errcode})")

    @staticmethod
    def verify_signature(appid: str, signature: str, timestamp: str, nonce: str) -> bool:
        config = settings.get_wechat_config(appid)
        if not config or not config.get("token"):
            return False

        token = config["token"]
        components = [token, timestamp, nonce]
        components.sort()
        combined = "".join(components)
        hashed = hashlib.sha1(combined.encode('utf-8')).hexdigest()
        return hashed == signature

    @staticmethod
    async def process_scan_event(appid: str, scene_id: str, openid: str, event_type: str = "SCAN"):
        """旧公众号事件只作回调应答，不创建账号或改变登录状态。"""
        return None

    @staticmethod
    async def consume_scan_login(scene_id: str, browser_token: str) -> uuid.UUID:
        redis_key = f"wechat_scan:{scene_id}"
        result = await redis_client.eval(
            """
            local value = redis.call('GET', KEYS[1])
            if not value then return {0, ''} end
            local state = cjson.decode(value)
            if state.browser_token ~= ARGV[1] then return {1, ''} end
            if state.status ~= 'SUCCESS' or not state.user_id then return {2, ''} end
            redis.call('DEL', KEYS[1])
            return {3, value}
            """,
            1,
            redis_key,
            browser_token,
        )
        status, data = result
        if status == 0:
            raise HTTPException(status_code=400, detail="二维码已过期")
        if status == 1:
            raise HTTPException(status_code=403, detail="扫码登录会话不匹配")
        if status == 2:
            raise HTTPException(status_code=409, detail="扫码登录尚未完成")

        return uuid.UUID(json.loads(data)["user_id"])

    @staticmethod
    async def send_customer_message(appid: str, openid: str, content: str):
        try:
            access_token = await WeChatService.get_access_token(appid)
            url = f"{WECHAT_API_BASE_URL}/message/custom/send?access_token={access_token}"
            payload = {
                "touser": openid,
                "msgtype": "text",
                "text": {"content": content}
            }
            async with httpx.AsyncClient() as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                return data.get("errcode", 0) == 0
        except Exception as e:
            print(f"Failed to send customer message: {e}")
            return False
