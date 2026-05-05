import json
import time
import uuid
import base64
from typing import Dict, Any

import httpx
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash import SHA256

from core.config import settings
from core.pay.schemas import (
    WechatPayMiniRequest,
    WechatPayNativeRequest,
    PayResultResponse,
    BasePayRequest
)

class WechatPayClient:
    """
    微信支付 (API v3) 客户端集成核心类
    """
    def __init__(self):
        self.app_id = settings.WECHAT_PAY_APP_ID
        self.mch_id = settings.WECHAT_PAY_MCH_ID
        self.api_v3_key = settings.WECHAT_PAY_API_V3_KEY
        self.cert_sn = settings.WECHAT_PAY_CERT_SN
        self.notify_url = settings.WECHAT_PAY_NOTIFY_URL
        self.base_url = "https://api.mch.weixin.qq.com"
        
        # 处理商户私钥
        raw_private_key = settings.WECHAT_PAY_PRIVATE_KEY
        if raw_private_key:
            if "-----BEGIN" not in raw_private_key:
                 raw_private_key = f"-----BEGIN PRIVATE KEY-----\n{raw_private_key}\n-----END PRIVATE KEY-----"
            self.private_key = RSA.importKey(raw_private_key)
        else:
            self.private_key = None

    def _sign_rsa(self, message: str) -> str:
        """核心签名方法: SHA256 With RSA"""
        if not self.private_key:
             raise ValueError("未配置 WECHAT_PAY_PRIVATE_KEY")
        h = SHA256.new(message.encode('utf-8'))
        signer = PKCS1_v1_5.new(self.private_key)
        signature = signer.sign(h)
        return base64.b64encode(signature).decode('utf-8')

    def _generate_v3_signature(self, method: str, url: str, body: str) -> Dict[str, str]:
        """
        生成 V3 接口要求的 Authorization 头部
        """
        timestamp = str(int(time.time()))
        nonce_str = uuid.uuid4().hex
        
        # 组装待签名串
        message = f"{method}\n{url}\n{timestamp}\n{nonce_str}\n{body}\n"
        sign = self._sign_rsa(message)
        
        auth_value = (
            f'WECHATPAY2-SHA256-RSA2048 '
            f'mchid="{self.mch_id}",'
            f'nonce_str="{nonce_str}",'
            f'signature="{sign}",'
            f'timestamp="{timestamp}",'
            f'serial_no="{self.cert_sn}"'
        )
        return {"Authorization": auth_value}

    async def _async_request(self, method: str, endpoint: str, json_data: dict) -> httpx.Response:
        """
        统一异步 HTTP 请求，遵守框架不可阻塞原则
        """
        body_str = json.dumps(json_data, ensure_ascii=False) if json_data else ""
        auth_header = self._generate_v3_signature(method, endpoint, body_str)
        
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "hope-service-pay/1.0"
        }
        headers.update(auth_header)
        
        async with httpx.AsyncClient(base_url=self.base_url) as client:
            return await client.request(method, endpoint, content=body_str.encode('utf-8'), headers=headers, timeout=10.0)

    async def create_mini_program_order(self, req: WechatPayMiniRequest) -> PayResultResponse:
        """
        发起微信小程序支付 (JSAPI 模式)
        返回小程序端 wx.requestPayment 需要的参数字典拼装。
        """
        endpoint = "/v3/pay/transactions/jsapi"
        payload = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": req.subject,
            "out_trade_no": req.order_id,
            "notify_url": req.notify_url or self.notify_url,
            "amount": {"total": req.amount, "currency": "CNY"},
            "payer": {"openid": req.openid}
        }
        
        try:
            resp = await self._async_request("POST", endpoint, payload)
            data = resp.json()
            
            if resp.status_code == 200 and "prepay_id" in data:
                # 组装返回给小程序的 pay_data
                prepay_id = data["prepay_id"]
                timestamp = str(int(time.time()))
                nonce_str = uuid.uuid4().hex
                package = f"prepay_id={prepay_id}"
                
                # 小程序调起支付需要的签名串: appId\ntimeStamp\nnonceStr\npackage\n
                sign_message = f"{self.app_id}\n{timestamp}\n{nonce_str}\n{package}\n"
                pay_sign = self._sign_rsa(sign_message)
                
                pay_data = {
                    "timeStamp": timestamp,
                    "nonceStr": nonce_str,
                    "package": package,
                    "signType": "RSA",
                    "paySign": pay_sign,
                    "appId": self.app_id
                }
                return PayResultResponse(success=True, pay_data=pay_data)
            else:
                return PayResultResponse(success=False, message=data.get("message", "微信下单失败"))
        except Exception as e:
            return PayResultResponse(success=False, message=f"请求微信异常: {str(e)}")

    async def create_native_order(self, req: WechatPayNativeRequest) -> PayResultResponse:
        """
        发起微信 Native (扫码) 支付
        返回一个可供前端直接生成二维码的 URL (code_url)
        """
        endpoint = "/v3/pay/transactions/native"
        payload = {
            "appid": self.app_id,
            "mchid": self.mch_id,
            "description": req.subject,
            "out_trade_no": req.order_id,
            "notify_url": req.notify_url or self.notify_url,
            "amount": {"total": req.amount, "currency": "CNY"}
        }
        
        try:
            resp = await self._async_request("POST", endpoint, payload)
            data = resp.json()
            if resp.status_code == 200 and "code_url" in data:
                return PayResultResponse(success=True, pay_url=data["code_url"])
            else:
                 return PayResultResponse(success=False, message=data.get("message", "微信native下单失败"))
        except Exception as e:
             return PayResultResponse(success=False, message=f"请求微信异常: {str(e)}")

    async def create_h5_order(self, req: BasePayRequest) -> PayResultResponse:
        """[预留] 微信 H5 支付 (外部浏览器)"""
        raise NotImplementedError("微信H5支付暂未接入")

    async def create_app_order(self, req: BasePayRequest) -> PayResultResponse:
        """[预留] 微信 APP 支付"""
        raise NotImplementedError("微信APP支付暂未接入")

    async def create_jsapi_order(self, req: BasePayRequest) -> PayResultResponse:
        """[预留] 微信服务号/公众号 JSAPI 支付"""
        raise NotImplementedError("微信公众号 JSAPI 支付暂未接入")
