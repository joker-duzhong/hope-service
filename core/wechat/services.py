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
from core.database import async_session_maker
from core.users.services import UserService
from core.security import create_access_token

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
    async def create_qrcode(appid: str) -> dict:
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

                # Cache status
                await redis_client.setex(f"wechat_scan:{scene_id}", 300, json.dumps({"status": "WAITING"}))

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
        print(f"[process_scan_event] Starting - appid: {appid}, scene_id: {scene_id}, openid: {openid}, event_type: {event_type}")

        try:
            async with async_session_maker() as db:
                print(f"[process_scan_event] Getting user by openid: {openid}")
                user = await UserService.get_by_openid(db, openid)
                is_new_user = False

                if not user:
                    print(f"[process_scan_event] User not found, creating new user")
                    is_new_user = True
                    user = await UserService.create_by_wechat(
                        db,
                        openid=openid,
                        source="wechat_scan"
                    )
                    print(f"[process_scan_event] New user created with id: {user.id}")
                else:
                    print(f"[process_scan_event] Existing user found with id: {user.id}")

            token = create_access_token(subject=user.id)
            print(f"[process_scan_event] Access token created")

            user_info = {
                "id": user.id,
                "openid": user.openid,
                "nickname": user.nickname,
                "avatar": user.avatar
            }

            cache_data = {
                "status": "SUCCESS",
                "token": token,
                "userInfo": user_info
            }

            redis_key = f"wechat_scan:{scene_id}"
            print(f"[process_scan_event] Setting Redis key: {redis_key}")
            await redis_client.set(redis_key, json.dumps(cache_data), ex=300)
            print(f"[process_scan_event] Redis key set successfully")

            # Verify Redis write
            verify_data = await redis_client.get(redis_key)
            if verify_data:
                print(f"[process_scan_event] Redis verification successful: {verify_data[:100]}...")
            else:
                print(f"[process_scan_event] WARNING: Redis verification failed - key not found!")

            # Send greeting message via WeChat Customer Service API
            message = "✅ 注册并登录成功，欢迎来到 Hope Service！" if is_new_user else "✅ 登录成功，欢迎回来！"
            await WeChatService.send_customer_message(appid, openid, message)
            print(f"[process_scan_event] Process completed successfully")

        except Exception as e:
            print(f"[process_scan_event] ERROR: {e}")
            import traceback
            traceback.print_exc()
            raise

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
