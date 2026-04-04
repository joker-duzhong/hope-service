from fastapi import APIRouter, Request, Response, HTTPException, Depends
from core.response import ResponseModel
from apps.wechat.services import WeChatService
from apps.wechat.schemas import WechatQRPollResponse
from core.redis_client import redis_client
import xml.etree.ElementTree as ET
import json

router = APIRouter()

@router.get("/auth/wechat/qrcode", summary="获取微信登录二维码")
async def get_qrcode(appid: str):
    result = await WeChatService.create_qrcode(appid)
    return ResponseModel(data=result)

@router.get("/wechat/callback/{appid}", summary="微信 Webhook 回调验证")
async def verify_wechat_webhook(appid: str, signature: str, timestamp: str, nonce: str, echostr: str):
    if WeChatService.verify_signature(appid, signature, timestamp, nonce):
        return Response(content=echostr, media_type="text/plain")
    return Response(content="error", media_type="text/plain")

@router.post("/wechat/callback/{appid}", summary="处理微信扫码回调事件")
async def handle_wechat_event(appid: str, request: Request):
    body = await request.body()
    try:
        root = ET.fromstring(body)
        msg_type = root.findtext("MsgType", default="")
        openid = root.findtext("FromUserName", default="")

        if msg_type == "event":
            event = root.findtext("Event", default="")
            event_key = root.findtext("EventKey", default="")
            scene_id = None
            if event == "subscribe":
                scene_id = event_key.replace("qrscene_", "")
            elif event == "SCAN":
                scene_id = event_key
            
            if scene_id:
                await WeChatService.process_scan_event(appid, scene_id, openid, event)
                # TODO: send greeting message to WeChat user via客服 API 
        
    except Exception as e:
        print(f"Error parsing wechat XML: {e}")
        
    return Response(content="success", media_type="text/plain")

@router.get("/auth/wechat/status", summary="查询微信扫码状态")
async def get_scan_status(scene_id: str):
    data = await redis_client.get(f"wechat_scan:{scene_id}")
    if not data:
        return ResponseModel(data={"status": "EXPIRED"})
    
    parsed = json.loads(data)
    return ResponseModel(data=parsed)
