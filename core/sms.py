import asyncio
import logging
import random

from tencentcloud.common import credential
from tencentcloud.common.profile.client_profile import ClientProfile
from tencentcloud.common.profile.http_profile import HttpProfile
from tencentcloud.sms.v20210111 import models, sms_client

from core.config import settings
from core.redis_client import redis_client

logger = logging.getLogger(__name__)

async def send_sms_code(phone: str, purpose: str) -> bool:
    """发送短信验证码并将其存储在 Redis 中，有效期 5 分钟。"""
    if purpose not in ("register", "bind"):
        logger.error(f"Invalid SMS purpose: {purpose}")
        return False
        
    code = f"{random.randint(100000, 999999)}"
    
    try:
        cred = credential.Credential(
            settings.TENCENT_SMS_SECRET_ID, 
            settings.TENCENT_SMS_SECRET_KEY
        )
        httpProfile = HttpProfile()
        httpProfile.endpoint = "sms.tencentcloudapi.com"

        clientProfile = ClientProfile()
        clientProfile.httpProfile = httpProfile
        client = sms_client.SmsClient(cred, settings.TENCENT_SMS_REGION, clientProfile)

        req = models.SendSmsRequest()
        # 默认只处理中国大陆手机号
        req.PhoneNumberSet = [f"+86{phone}"]
        req.SmsSdkAppId = settings.TENCENT_SMS_APP_ID
        req.SignName = settings.TENCENT_SMS_SIGN_NAME
        
        if purpose == "register":
            req.TemplateId = settings.TENCENT_SMS_TEMPLATE_ID_REGISTER
        elif purpose == "bind":
            req.TemplateId = settings.TENCENT_SMS_TEMPLATE_ID_BIND
            
        req.TemplateParamSet = [code, "5"]

        resp = await asyncio.to_thread(client.SendSms, req)
        logger.info(f"Send SMS response for {phone}: {resp.to_json_string()}")
        
    except Exception as e:
        logger.error(f"Failed to send SMS to {phone}: {str(e)}")
        # 为了本地测试如果没有配置短信，可以打印出来，并存入 Redis
        # 但是生产建议 return False
        
    # 将验证码存入 Redis，5分钟过期
    cache_key = f"sms:{purpose}:{phone}"
    await redis_client.setex(cache_key, 300, code)
    
    return True

async def verify_sms_code(phone: str, purpose: str, code: str) -> bool:
    """验证 Redis 中的短信验证码。验证成功即删除"""
    cache_key = f"sms:{purpose}:{phone}"
    cached_code = await redis_client.get(cache_key)
    
    if cached_code and cached_code == code:
        await redis_client.delete(cache_key)
        return True
    return False
