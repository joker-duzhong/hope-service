from pydantic import BaseModel, Field
from typing import Optional, Dict, Any


class WechatCodeToOpenidRequest(BaseModel):
    """微信 code 换 openid 请求"""
    appid: str = Field(..., description="微信 AppID")
    code: str = Field(..., description="前端获取的一次性 code")


class WechatOpenidResponse(BaseModel):
    """微信 openid 响应"""
    openid: str = Field(..., description="用户在当前微信应用下的 openid")
    unionid: Optional[str] = Field(None, description="同一开放平台下的 unionid")


class WechatJssdkConfigResponse(BaseModel):
    """微信网页 JSSDK 配置响应"""
    appId: str = Field(..., description="公众号 AppID")
    timestamp: int = Field(..., description="签名时间戳")
    nonceStr: str = Field(..., description="签名随机串")
    signature: str = Field(..., description="JSSDK 签名")


class WechatQRPollResponse(BaseModel):
    status: str
    token: Optional[str] = None
    userInfo: Optional[Dict[str, Any]] = None
