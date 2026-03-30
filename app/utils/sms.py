"""
短信服务（预留）

支持的短信服务商：
- 阿里云短信
- 腾讯云短信
"""
from typing import Optional

from app.core.config import settings


class SMSService:
    """短信服务基类"""

    def __init__(self):
        self.access_key = settings.SMS_ACCESS_KEY
        self.secret_key = settings.SMS_SECRET_KEY
        self.sign_name = settings.SMS_SIGN_NAME

    async def send_code(self, phone: str, code: str, scene: str = "login") -> bool:
        """
        发送验证码

        Args:
            phone: 手机号
            code: 验证码
            scene: 场景（login/register/reset）

        Returns:
            是否发送成功
        """
        # TODO: 实现短信发送逻辑
        # 1. 根据配置选择短信服务商
        # 2. 调用短信API发送验证码
        # 3. 将验证码存入Redis，设置过期时间（如5分钟）
        raise NotImplementedError("短信服务暂未实现")

    async def verify_code(self, phone: str, code: str, scene: str = "login") -> bool:
        """
        验证验证码

        Args:
            phone: 手机号
            code: 验证码
            scene: 场景

        Returns:
            验证是否通过
        """
        # TODO: 实现验证码校验逻辑
        # 1. 从Redis获取验证码
        # 2. 验证是否正确
        # 3. 验证通过后删除验证码
        raise NotImplementedError("短信服务暂未实现")


class AliyunSMSService(SMSService):
    """阿里云短信服务"""

    async def send_code(self, phone: str, code: str, scene: str = "login") -> bool:
        # TODO: 实现阿里云短信API调用
        raise NotImplementedError("阿里云短信服务暂未实现")


class TencentSMSService(SMSService):
    """腾讯云短信服务"""

    async def send_code(self, phone: str, code: str, scene: str = "login") -> bool:
        # TODO: 实现腾讯云短信API调用
        raise NotImplementedError("腾讯云短信服务暂未实现")


def get_sms_service() -> SMSService:
    """获取短信服务实例"""
    # TODO: 根据配置返回对应的短信服务
    return SMSService()


sms_service = get_sms_service()
