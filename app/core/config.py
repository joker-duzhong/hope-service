"""
应用配置模块
使用 pydantic-settings 管理环境变量配置
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
    APP_PORT: int = 8000  # Docker部署用

    # 数据库配置
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "hope_service"

    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+asyncpg://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    @property
    def SYNC_DATABASE_URL(self) -> str:
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

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

    # 微信公众号配置
    WECHAT_APP_ID: Optional[str] = "wx29ee8c1ad373bafa"
    WECHAT_APP_SECRET: Optional[str] = "817917aead96825e6595c708ab7d58c9"

    # 外部服务配置
    SIMPLETEX_API_URL: str = "https://server.simpletex.cn"
    SIMPLETEX_API_TOKEN: Optional[str] = "BRTHXYByT1t5wUXRm9ejZsxiIYE4bIqehgqSs3FgwfMM8kqDjUSc5I2SNTxdkALJ"

    # 短信服务配置（预留）
    SMS_ACCESS_KEY: Optional[str] = None
    SMS_SECRET_KEY: Optional[str] = None
    SMS_SIGN_NAME: Optional[str] = None

    # 大模型配置（预留）
    OPENAI_API_KEY: Optional[str] = None
    ANTHROPIC_API_KEY: Optional[str] = None

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()


settings = get_settings()
