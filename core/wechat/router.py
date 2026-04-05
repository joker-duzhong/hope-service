from fastapi import APIRouter, Request, Response, HTTPException
from core.response import ResponseModel
from core.config import settings
from core.wechat.services import WeChatService
from core.wechat.crypto import WeChatCrypto
from core.redis_client import redis_client
import xml.etree.ElementTree as ET
import json

router = APIRouter()


def get_crypto(appid: str) -> WeChatCrypto:
    """获取加解密实例"""
    config = settings.get_wechat_config(appid)
    if not config or not config.get("token") or not config.get("encoding_aes_key"):
        raise HTTPException(status_code=400, detail="WeChat crypto config missing")
    return WeChatCrypto(
        token=config["token"],
        encoding_aes_key=config["encoding_aes_key"],
        appid=appid,
    )


@router.get("/auth/wechat/qrcode", summary="获取微信登录二维码")
async def get_qrcode(appid: str):
    try:
        result = await WeChatService.create_qrcode(appid)
        return ResponseModel(data=result)
    except HTTPException:
        raise
    except Exception as e:
        return ResponseModel(code=400, message=str(e))


@router.get("/wechat/callback/{appid}", summary="微信 Webhook 回调验证")
async def verify_wechat_webhook(
    appid: str, signature: str, timestamp: str, nonce: str, echostr: str
):
    try:
        # 验证服务器配置时，echostr 始终是明文，直接验证签名后返回
        config = settings.get_wechat_config(appid)
        if not config or not config.get("token"):
            return Response(content="error: token not configured", media_type="text/plain")

        # 使用 token 验证签名
        crypto = WeChatCrypto(
            token=config["token"],
            encoding_aes_key=config.get("encoding_aes_key", ""),
            appid=appid,
        )
        if crypto.verify_signature(signature, timestamp, nonce):
            return Response(content=echostr, media_type="text/plain")

        return Response(content="error: signature mismatch", media_type="text/plain")
    except Exception as e:
        return Response(content=f"error: {e}", media_type="text/plain")


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


@router.post("/auth/wechat/login", summary="微信登记页面登录")
async def wechat_login(request: Request):
    """
    使用微信授权码登录
    请求体: {"appid": "xxx", "code": "xxx"}
    """
    try:
        body = await request.json()
        appid = body.get("appid")
        code = body.get("code")

        if not appid or not code:
            return ResponseModel(code=400, message="appid and code are required")

        # 使用微信服务中的登录逻辑
        result = await WeChatService.login_with_code(appid, code)
        return ResponseModel(data=result)
    except HTTPException as e:
        return ResponseModel(code=e.status_code, message=e.detail)
    except Exception as e:
        print(f"WeChat login error: {e}")
        return ResponseModel(code=500, message=f"Internal server error: {str(e)}")


@router.get("/auth/wechat/status", summary="查询微信扫码状态")
async def get_scan_status(scene_id: str):
    try:
        data = await redis_client.get(f"wechat_scan:{scene_id}")
        if not data:
            return ResponseModel(data={"status": "EXPIRED"})

        parsed = json.loads(data)
        return ResponseModel(data=parsed)
    except Exception as e:
        return ResponseModel(code=400, message=str(e))
