import json
import httpx
import uuid
import hashlib
import time
import urllib.parse
from fastapi import HTTPException
from core.redis_client import redis_client
from core.config import settings
from core.database import async_session
from core.users.services import UserService
from core.security import create_access_token

WECHAT_API_BASE_URL = "https://api.weixin.qq.com/cgi-bin"

class WeChatService:
    @staticmethod
    async def get_access_token(appid: str) -> str:
        config = settings.get_wechat_config(appid)
        if not config:
            raise HTTPException(status_code=500, detail="WeChat config missing for this appid")
            
        secret = config["secret"]
        
        redis_key = f"wechat_access_token:{appid}"
        token = await redis_client.get(redis_key)
        if token:
            return token.decode('utf-8')
            
        url = f"{WECHAT_API_BASE_URL}/token?grant_type=client_credential&appid={appid}&secret={secret}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()
            if "access_token" in data:
                token = data["access_token"]
                await redis_client.setex(redis_key, 7000, token)
                return token
            raise HTTPException(status_code=500, detail="Failed to get access token")

    @staticmethod
    async def create_qrcode(appid: str) -> dict:
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
            raise HTTPException(status_code=500, detail="Failed to create qrcode")
            
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
        async with async_session() as db:
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
                await client.post(url, json=payload)
        except Exception as e:
            print(f"Failed to send customer message: {e}")
