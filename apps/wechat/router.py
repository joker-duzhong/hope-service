from fastapi import APIRouter, Request, Response, HTTPException
from core.response import ResponseModel
from core.config import settings
from apps.wechat.services import WeChatService
from apps.wechat.schemas import WechatQRPollResponse
from apps.wechat.crypto import WeChatCrypto
from core.redis_client import redis_client
import xml.etree.ElementTree as ET
import json

router = APIRouter()


def get_crypto(appid: str) -> WeChatCrypto:
    """获取加解密实例"""
    config = settings.get_wechat_config(appid)
    if not config or not config.get("token") or not config.get("encoding_aes_key"):
        raise HTTPException(status_code=500, detail="WeChat crypto config missing")
    return WeChatCrypto(
        token=config["token"],
        encoding_aes_key=config["encoding_aes_key"],
        appid=appid,
    )


@router.get("/auth/wechat/qrcode", summary="获取微信登录二维码")
async def get_qrcode(appid: str):
    result = await WeChatService.create_qrcode(appid)
    return ResponseModel(data=result)


@router.get("/wechat/callback/{appid}", summary="微信 Webhook 回调验证")
async def verify_wechat_webhook(
    appid: str, signature: str, timestamp: str, nonce: str, echostr: str
):
    # 安全模式下 echostr 是加密的，需要解密后返回
    config = settings.get_wechat_config(appid)
    if not config or not config.get("encoding_aes_key"):
        # 明文模式
        if WeChatService.verify_signature(appid, signature, timestamp, nonce):
            return Response(content=echostr, media_type="text/plain")
    else:
        # 安全模式
        crypto = get_crypto(appid)
        if crypto.verify_signature(signature, timestamp, nonce):
            # echostr 是 base64 编码的加密数据，解密后返回
            try:
                decrypted = crypto.decrypt(echostr)
                return Response(content=decrypted, media_type="text/plain")
            except Exception as e:
                print(f"Failed to decrypt echostr: {e}")
    return Response(content="error", media_type="text/plain")


@router.post("/wechat/callback/{appid}", summary="处理微信扫码回调事件")
async def handle_wechat_event(appid: str, request: Request, msg_signature: str = None, timestamp: str = None, nonce: str = None):
    body = await request.body()
    body_str = body.decode("utf-8")

    try:
        root = ET.fromstring(body_str)

        # 检查是否是加密消息
        encrypt = root.findtext("Encrypt", default="")

        if encrypt:
            # 安全模式：解密消息
            config = settings.get_wechat_config(appid)
            if config and config.get("encoding_aes_key") and msg_signature and timestamp and nonce:
                crypto = get_crypto(appid)
                decrypted_xml = crypto.decrypt_message(body_str, msg_signature, timestamp, nonce)
                root = ET.fromstring(decrypted_xml)
            else:
                print("Missing crypto config or parameters for encrypted message")

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
