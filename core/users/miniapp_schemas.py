"""
微信小程序登录相关的 Pydantic 模型
"""
from pydantic import BaseModel, Field


class MiniappLoginRequest(BaseModel):
    """小程序登录请求"""
    appid: str = Field(..., description="小程序 AppID")
    code: str = Field(..., description="wx.login() 获取的 code")


class MiniappPhoneRequest(BaseModel):
    """小程序获取手机号请求"""
    appid: str = Field(..., description="小程序 AppID")
    code: str = Field(..., description="getPhoneNumber 返回的 code")
