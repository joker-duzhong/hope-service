from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

class BasePayRequest(BaseModel):
    order_id: str = Field(..., description="业务订单号")
    amount: int = Field(..., description="支付金额（统一单位：分）。内部逻辑将依据支付渠道进行转换（支付宝需转为元，微信直接用分）")
    subject: str = Field(..., description="订单标题/商品说明")
    client_ip: Optional[str] = Field(None, description="客户端真实的 IP 地址")
    notify_url: Optional[str] = Field(None, description="异步回调地址，为空则使用系统全局配置配置")

# =================
# 微信支付请求/响应模型
# =================

class WechatPayMiniRequest(BasePayRequest):
    openid: str = Field(..., description="用户在对应小程序的 openid")

class WechatPayNativeRequest(BasePayRequest):
    product_id: Optional[str] = Field(None, description="Native支付的商品ID")

# =================
# 支付宝请求/响应模型
# =================

class AliPayWapRequest(BasePayRequest):
    quit_url: Optional[str] = Field(None, description="用户付款中途退出返回商户网站的地址")
    return_url: Optional[str] = Field(None, description="付款成功后的同步跳转页面路径")

class AliPayScanRequest(BasePayRequest):
    store_id: Optional[str] = Field(None, description="商户门店编号")

class AliPayPcRequest(BasePayRequest):
    quit_url: Optional[str] = Field(None, description="用户付款中途退出返回商户网站的地址")
    return_url: Optional[str] = Field(None, description="付款成功后的同步跳转页面路径")

# =================
# 统一支付结果输出模型
# =================

class PayResultResponse(BaseModel):
    success: bool = Field(True, description="是否发起成功")
    pay_url: Optional[str] = Field(None, description="跳转链接(支付宝H5)、或二维码内容(微信Native/支付宝扫码)")
    pay_data: Optional[Dict[str, Any]] = Field(None, description="客户端拉起支付所需的上下文参数(如小程序需要的 timestamp/nonce_str/paySign 等)")
    message: str = Field("ok", description="附加信息")
