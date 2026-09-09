from typing import Optional

from fastapi import APIRouter, Depends, Request, Response, HTTPException, Query

from core.response import ResponseModel
from core.config import settings
from core.wechat.services import WeChatService
from core.wechat.crypto import WeChatCrypto
from core.wechat.schemas import (
    WechatQRExchangeRequest,
    WechatCodeToOpenidRequest,
    WechatJssdkConfigResponse,
    WechatOpenidResponse,
)
from core.users.schemas import Token
import xml.etree.ElementTree as ET

router = APIRouter()


async def legacy_scan_unavailable() -> None:
    raise HTTPException(410, "旧公众号事件扫码登录已停用，请使用授权中心 /auth/scan 流程")


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


@router.get("/auth/wechat/qrcode", summary="获取微信登录二维码", deprecated=True, dependencies=[Depends(legacy_scan_unavailable)])
async def get_qrcode(appid: str, response: Response):
    await legacy_scan_unavailable()


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
async def handle_wechat_event(
    appid: str,
    request: Request,
    signature: str = None,
    msg_signature: str = None,
    timestamp: str = None,
    nonce: str = None,
):
    body = await request.body()
    body_str = body.decode("utf-8")

    # 详细日志记录
    print(f"[WeChat Callback] Received event for appid: {appid}")
    print(f"[WeChat Callback] Body length: {len(body_str)}")
    print(f"[WeChat Callback] msg_signature: {msg_signature}, timestamp: {timestamp}, nonce: {nonce}")

    try:
        config = settings.get_wechat_config(appid)
        if not config or not config.get("token"):
            return Response(content="forbidden", status_code=403, media_type="text/plain")

        root = ET.fromstring(body_str)

        # 检查是否是加密消息
        encrypt = root.findtext("Encrypt", default="")

        if encrypt:
            print(f"[WeChat Callback] Encrypted message detected")
            # 安全模式：解密消息
            if not config.get("encoding_aes_key") or not msg_signature or not timestamp or not nonce:
                return Response(content="forbidden", status_code=403, media_type="text/plain")
            crypto = get_crypto(appid)
            decrypted_xml = crypto.decrypt_message(body_str, msg_signature, timestamp, nonce)
            root = ET.fromstring(decrypted_xml)
            print(f"[WeChat Callback] Message decrypted successfully")
        else:
            print(f"[WeChat Callback] Plain text message")
            if not signature or not timestamp or not nonce:
                return Response(content="forbidden", status_code=403, media_type="text/plain")
            crypto = WeChatCrypto(token=config["token"], appid=appid)
            if not crypto.verify_signature(signature, timestamp, nonce):
                return Response(content="forbidden", status_code=403, media_type="text/plain")

        msg_type = root.findtext("MsgType", default="")
        openid = root.findtext("FromUserName", default="")

        print(f"[WeChat Callback] MsgType: {msg_type}, OpenID: {openid}")

        if msg_type == "event":
            event = root.findtext("Event", default="")
            event_key = root.findtext("EventKey", default="")
            print(f"[WeChat Callback] Event: {event}, EventKey: {event_key}")

            scene_id = None
            if event == "subscribe":
                scene_id = event_key.replace("qrscene_", "")
                print(f"[WeChat Callback] Subscribe event, scene_id: {scene_id}")
            elif event == "SCAN":
                scene_id = event_key
                print(f"[WeChat Callback] SCAN event, scene_id: {scene_id}")

            if scene_id:
                print(f"[WeChat Callback] Processing scan event for scene_id: {scene_id}")
                await WeChatService.process_scan_event(appid, scene_id, openid, event)
                print(f"[WeChat Callback] Scan event processed successfully")
            else:
                print(f"[WeChat Callback] No scene_id found, skipping")

    except Exception as e:
        print(f"[WeChat Callback] Error parsing wechat XML: {e}")
        import traceback
        traceback.print_exc()
        return Response(content="bad request", status_code=400, media_type="text/plain")

    return Response(content="success", media_type="text/plain")


@router.post(
    "/auth/wechat/miniapp/openid",
    response_model=ResponseModel[WechatOpenidResponse],
    summary="小程序 code 换 openid",
)
async def miniapp_code_to_openid(req: WechatCodeToOpenidRequest):
    result = await WeChatService.exchange_miniapp_code_for_openid(req.appid, req.code)
    return ResponseModel(data=WechatOpenidResponse(**result))


@router.post(
    "/auth/wechat/h5/openid",
    response_model=ResponseModel[WechatOpenidResponse],
    summary="H5 网页授权 code 换 openid",
)
async def h5_code_to_openid(req: WechatCodeToOpenidRequest):
    result = await WeChatService.exchange_h5_code_for_openid(req.appid, req.code)
    return ResponseModel(data=WechatOpenidResponse(**result))


@router.get(
    "/wechat/jssdk-config",
    response_model=ResponseModel[WechatJssdkConfigResponse],
    summary="获取微信网页 JSSDK 配置",
)
async def get_jssdk_config(
    url: str = Query(..., description="当前网页完整 URL，不包含 URL hash"),
    appid: Optional[str] = Query(None, description="公众号 AppID，不传则使用默认公众号"),
):
    result = await WeChatService.create_jssdk_config(url=url, appid=appid)
    return ResponseModel(data=WechatJssdkConfigResponse(**result))


@router.get("/auth/wechat/status", summary="查询微信扫码状态", deprecated=True, dependencies=[Depends(legacy_scan_unavailable)])
async def get_scan_status(scene_id: str, request: Request):
    await legacy_scan_unavailable()


@router.post("/auth/wechat/exchange", response_model=ResponseModel[Token], summary="兑换微信扫码登录令牌", deprecated=True, dependencies=[Depends(legacy_scan_unavailable)])
async def exchange_scan_login(
    body: WechatQRExchangeRequest,
    request: Request,
):
    await legacy_scan_unavailable()
