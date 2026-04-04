"""
全局配置模块
使用 pydantic-settings 管理环境变量
"""
from functools import lru_cache
from typing import List, Optional

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""

    # 基础配置
    APP_NAME: str = "hope-service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # API 配置
    API_V1_PREFIX: str = "/api/v1"

    # 服务器配置
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    APP_PORT: int = 8000

    # 数据库配置
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "hope_service"

    @property
    def DATABASE_URL(self) -> str:
        return (
            f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return (
            f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}"
            f"@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
        )

    # Redis 配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None

    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/0"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/0"

    # JWT 配置
    SECRET_KEY: str = "your-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24小时
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS 配置
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # 微信公众号配置（单公众号）
    WECHAT_APP_ID: str = ""
    WECHAT_APP_SECRET: str = ""
    WECHAT_TOKEN: str = ""

    # 微信公众号配置（多公众号映射）
    WECHAT_APPS: str = ""  # 格式: appid1:secret1:token1,appid2:secret2:token2

    def get_wechat_config(self, appid: str) -> Optional[dict]:
        """根据 appid 获取对应的 secret 和 token"""
        if not self.WECHAT_APPS:
            return None
        for pair in self.WECHAT_APPS.split(","):
            parts = pair.split(":")
            if len(parts) >= 2 and parts[0].strip() == appid:
                secret = parts[1].strip()
                token = parts[2].strip() if len(parts) >= 3 else None
                return {"secret": secret, "token": token}
        return None
    
    # 飞书 Webhook 配置
    FEISHU_WEBHOOK_URL: Optional[str] = None
    
    # 腾讯云短信配置
    TENCENT_SMS_SECRET_ID: str = ""
    TENCENT_SMS_SECRET_KEY: str = ""
    TENCENT_SMS_APP_ID: str = ""
    TENCENT_SMS_SIGN_NAME: str = ""
    TENCENT_SMS_TEMPLATE_ID_REGISTER: str = ""
    TENCENT_SMS_TEMPLATE_ID_BIND: str = ""
    TENCENT_SMS_REGION: str = "ap-guangzhou"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


settings = get_settings()

