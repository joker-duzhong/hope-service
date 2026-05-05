import json
import base64
from datetime import datetime
from urllib.parse import quote_plus
from typing import Dict, Any

import httpx
from Crypto.PublicKey import RSA
from Crypto.Signature import PKCS1_v1_5
from Crypto.Hash import SHA256

from core.config import settings
from core.pay.schemas import (
    AliPayWapRequest,
    AliPayScanRequest,
    AliPayPcRequest,
    PayResultResponse,
    BasePayRequest
)

class AliPayClient:
    """
    支付宝支付核心客户端类
    """
    def __init__(self):
        self.app_id = settings.ALIPAY_APP_ID
        self.gateway_url = settings.ALIPAY_GATEWAY
        self.sign_type = "RSA2"
        self.notify_url = settings.ALIPAY_NOTIFY_URL

        # 处理并加载私钥 (可传入单行字符串或已包含头尾的多行字符串)
        raw_private_key = settings.ALIPAY_PRIVATE_KEY
        if raw_private_key:
            if "-----BEGIN" not in raw_private_key:
                raw_private_key = f"-----BEGIN RSA PRIVATE KEY-----\n{raw_private_key}\n-----END RSA PRIVATE KEY-----"
            self.private_key = RSA.importKey(raw_private_key)
        else:
            self.private_key = None

    def _sign_payload(self, params: dict) -> str:
        """使用应用私钥进行 RSA2 签名"""
        if not self.private_key:
            raise ValueError("未配置 ALIPAY_PRIVATE_KEY")

        # 过滤空值并按字母顺序排序
        sorted_keys = sorted([k for k in params.keys() if params[k]])
        sign_strings = [f"{k}={params[k]}" for k in sorted_keys]
        unsigned_string = "&".join(sign_strings)

        h = SHA256.new(unsigned_string.encode('utf-8'))
        signer = PKCS1_v1_5.new(self.private_key)
        signature = signer.sign(h)
        return base64.b64encode(signature).decode('utf-8')

    def _build_request_params(self, method_name: str, biz_content: dict, notify_url: str = None, return_url: str = None) -> dict:
        """构建公共参数与业务参数合并后的字典"""
        params = {
            "app_id": self.app_id,
            "method": method_name,
            "format": "JSON",
            "charset": "utf-8",
            "sign_type": self.sign_type,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "version": "1.0",
            "biz_content": json.dumps(biz_content, ensure_ascii=False, separators=(',', ':'))
        }
        
        callback_notify = notify_url or self.notify_url
        if callback_notify:
            params["notify_url"] = callback_notify
            
        if return_url:
            params["return_url"] = return_url
            
        params["sign"] = self._sign_payload(params)
        return params

    def _convert_amount(self, amount_in_cents: int) -> str:
        """支付宝需要元作为单位，例如 100分 -> 1.00"""
        return f"{amount_in_cents / 100.0:.2f}"

    async def create_wap_order(self, req: AliPayWapRequest) -> PayResultResponse:
        """发起支付宝 手机网站(H5) 支付 (alipay.trade.wap.pay)"""
        biz_content = {
            "out_trade_no": req.order_id,
            "total_amount": self._convert_amount(req.amount),
            "subject": req.subject,
            "product_code": "QUICK_WAP_WAY"
        }
        if req.quit_url:
            biz_content["quit_url"] = req.quit_url

        params = self._build_request_params("alipay.trade.wap.pay", biz_content, req.notify_url, req.return_url)
        
        # 将参数转换为 QueryString 拼接成跳转链接
        qs = "&".join([f"{k}={quote_plus(str(v))}" for k, v in params.items()])
        pay_url = f"{self.gateway_url}?{qs}"
        
        return PayResultResponse(success=True, pay_url=pay_url)

    async def create_pc_order(self, req: AliPayPcRequest) -> PayResultResponse:
        """发起支付宝 PC网页支付 (alipay.trade.page.pay)"""
        biz_content = {
            "out_trade_no": req.order_id,
            "total_amount": self._convert_amount(req.amount),
            "subject": req.subject,
            "product_code": "FAST_INSTANT_TRADE_PAY"
        }

        params = self._build_request_params("alipay.trade.page.pay", biz_content, req.notify_url, req.return_url)
        
        qs = "&".join([f"{k}={quote_plus(str(v))}" for k, v in params.items()])
        pay_url = f"{self.gateway_url}?{qs}"
        
        return PayResultResponse(success=True, pay_url=pay_url)

    async def create_scan_order(self, req: AliPayScanRequest) -> PayResultResponse:
        """发起支付宝 扫码支付 (alipay.trade.precreate)"""
        biz_content = {
            "out_trade_no": req.order_id,
            "total_amount": self._convert_amount(req.amount),
            "subject": req.subject
        }
        if req.store_id:
            biz_content["store_id"] = req.store_id
        
        params = self._build_request_params("alipay.trade.precreate", biz_content, req.notify_url)
        
        # 实际发出网关请求拿二维码
        async with httpx.AsyncClient() as client:
            try:
                resp = await client.get(self.gateway_url, params=params, timeout=10.0)
                data = resp.json()
                
                resp_node = data.get("alipay_trade_precreate_response", {})
                if resp_node.get("code") == "10000":
                    qr_url = resp_node.get("qr_code")
                    return PayResultResponse(success=True, pay_url=qr_url)
                else:
                    return PayResultResponse(success=False, message=resp_node.get("sub_msg", resp_node.get("msg", "支付宝请求错误")))
            except Exception as e:
                return PayResultResponse(success=False, message=f"支付宝网关请求异常: {str(e)}")

    async def create_app_order(self, req: BasePayRequest) -> PayResultResponse:
        """[预留] 支付宝 APP 支付 (拉起手机端支付宝)"""
        raise NotImplementedError("支付宝APP支付暂未接入")
