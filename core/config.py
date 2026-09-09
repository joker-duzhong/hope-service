"""
全局配置模块
使用 pydantic-settings 管理环境变量
"""
from functools import lru_cache
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""

    # 基础配置
    APP_NAME: str = "hope-service"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    ENVIRONMENT: str = "development"

    @field_validator("DEBUG", mode="before")
    @classmethod
    def normalize_debug(cls, value):
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"release", "prod", "production", "false", "0", "off", "no"}:
                return False
            if lowered in {"debug", "dev", "development", "true", "1", "on", "yes"}:
                return True
        return value

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
    JWT_ISSUER: str = "hope-service"
    JWT_AUDIENCE: str = "hope-platform"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 24小时
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # CORS 配置
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # 微信公众号配置（多公众号映射）
    WECHAT_APPS: str = ""  # 格式: appid1:secret1:token1:aeskey1,appid2:secret2:token2:aeskey2

    PASSPORT_WECHAT_APP_IDS: List[str] = []
    PASSPORT_CALLBACK_ORIGINS: List[str] = []
    MINIAPP_APP_SCOPES: dict[str, str] = {}

    def get_wechat_config(self, appid: str) -> Optional[dict]:
        """根据 appid 获取对应的 secret、token 和 encoding_aes_key"""
        if not self.WECHAT_APPS:
            return None
        for pair in self.WECHAT_APPS.split(","):
            parts = pair.split(":")
            if len(parts) >= 2 and parts[0].strip() == appid:
                secret = parts[1].strip()
                token = parts[2].strip() if len(parts) >= 3 else None
                encoding_aes_key = parts[3].strip() if len(parts) >= 4 else None
                return {"secret": secret, "token": token, "encoding_aes_key": encoding_aes_key}
        return None
    
    # 飞书 Webhook 配置
    FEISHU_WEBHOOK_URL: Optional[str] = None
    
    # 阿里云号码认证短信配置
    ALIBABA_CLOUD_ACCESS_KEY_ID: str = ""
    ALIBABA_CLOUD_ACCESS_KEY_SECRET: str = ""
    ALIYUN_SMS_SIGN_NAME: str = ""
    ALIYUN_SMS_TEMPLATE_CODE: str = ""

    # LLM 配置
    # 格式: {"openai": {"api_key": "sk-...", "base_url": "...", "default_model": "gpt-4o", "timeout": 60, "max_retries": 3}}
    LLM_PROVIDERS: dict = {}
    LLM_DEFAULT_PROVIDER: str = ""

    # 七牛云 OSS 配置
    QINIU_ACCESS_KEY: str = ""
    QINIU_SECRET_KEY: str = ""
    QINIU_BUCKET_NAME: str = ""
    QINIU_DOMAIN: str = ""  # 访问域名，如 http://oss.yourdomain.com

    # ==================== 支付配置 ====================
    # 微信支付配置
    WECHAT_PAY_APP_ID: str = ""
    WECHAT_PAY_MCH_ID: str = ""
    WECHAT_PAY_API_V3_KEY: str = ""
    WECHAT_PAY_PRIVATE_KEY: str = ""  # 微信商户API私钥或 PEM 文件路径
    WECHAT_PAY_PLATFORM_CERT_PATH: str = ""  # 微信支付平台证书/公钥 PEM 文件路径，用于回调验签
    WECHAT_PAY_CERT_SN: str = ""      # 商户证书序列号
    WECHAT_PAY_NOTIFY_URL: str = ""

    # 支付宝配置
    ALIPAY_APP_ID: str = ""
    ALIPAY_PRIVATE_KEY: str = ""      # 支付宝应用私钥字符串
    ALIPAY_PUBLIC_KEY: str = ""       # 支付宝公钥字符串 (用于回调验签)
    ALIPAY_GATEWAY: str = "https://openapi.alipay.com/gateway.do"
    ALIPAY_NOTIFY_URL: str = ""

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in {"production", "prod"}

    def validate_runtime_security(self) -> None:
        """Reject insecure JWT settings before a production process starts."""
        if not self.is_production:
            return
        if self.SECRET_KEY == "your-secret-key-change-in-production" or len(self.SECRET_KEY.encode("utf-8")) < 32:
            raise RuntimeError("生产环境必须配置至少 32 字节的随机 SECRET_KEY")
        if self.ALGORITHM != "HS256":
            raise RuntimeError("生产环境仅支持 HS256 JWT 算法")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


settings = get_settings()
