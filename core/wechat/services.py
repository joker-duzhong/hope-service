import json
import httpx
import uuid
import hashlib
import urllib.parse
from fastapi import HTTPException
from core.redis_client import redis_client
from core.config import settings
from core.database import async_session_maker
from core.users.services import UserService
from core.security import create_access_token

WECHAT_API_BASE_URL = "https://api.weixin.qq.com/cgi-bin"


class WeChatService:
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
        async with async_session_maker() as db:
            user = await UserService.get_by_openid(db, openid)
            is_new_user = False

            if not user:
                is_new_user = True
                user = await UserService.create_by_wechat(
                    db,
                    openid=openid,
                    source="wechat_scan"
                )

        token = create_access_token(subject=user.id)

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
        await redis_client.set(f"wechat_scan:{scene_id}", json.dumps(cache_data), ex=300)

        # Send greeting message via WeChat Customer Service API
        message = "✅ 注册并登录成功，欢迎来到 Hope Service！" if is_new_user else "✅ 登录成功，欢迎回来！"
        await WeChatService.send_customer_message(appid, openid, message)

    @staticmethod
    async def login_with_code(appid: str, code: str) -> dict:
        """使用微信授权码登录或注册"""
        config = settings.get_wechat_config(appid)
        if not config:
            raise HTTPException(status_code=400, detail=f"WeChat config not found for appid: {appid}")

        secret = config.get("secret")
        if not secret:
            raise HTTPException(status_code=400, detail=f"WeChat secret not configured for appid: {appid}")

        # 调用微信API获取openid
        url = f"{WECHAT_API_BASE_URL}/oauth2/access_token?appid={appid}&secret={secret}&code={code}&grant_type=authorization_code"

        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()

            if "errcode" in data:
                errcode = data.get("errcode")
                errmsg = data.get("errmsg", "unknown error")
                print(f"WeChat OAuth error: errcode={errcode}, errmsg={errmsg}, appid={appid}, code={code}")
                raise HTTPException(status_code=400, detail=f"微信授权失败: {errmsg} (code: {errcode})")

            openid = data.get("openid")
            if not openid:
                raise HTTPException(status_code=400, detail="Failed to get openid from WeChat")

        # 在数据库中查找或创建用户
        async with async_session_maker() as db:
            user = await UserService.get_by_openid(db, openid)
            is_new_user = False

            if not user:
                is_new_user = True
                user = await UserService.create_by_wechat(
                    db,
                    openid=openid,
                    source="wechat_login"
                )

        # 生成登录token
        token = create_access_token(subject=user.id)

        user_info = {
            "id": user.id,
            "openid": user.openid,
            "nickname": user.nickname,
            "avatar": user.avatar
        }

        return {
            "token": token,
            "userInfo": user_info,
            "isNewUser": is_new_user
        }

    @staticmethod
        try:
            access_token = await WeChatService.get_access_token(appid)
            url = f"{WECHAT_API_BASE_URL}/message/custom/send?access_token={access_token}"
            payload = {
                "touser": openid,
                "msgtype": "text",
                "text": {"content": content}
            }
            async with httpx.AsyncClient() as client:
                await client.post(url, json=payload)
        except Exception as e:
            print(f"Failed to send customer message: {e}")
