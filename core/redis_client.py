"""
全局异步 Redis 客户端封装
"""
import redis.asyncio as redis
from core.config import settings

# 全局共享的异步 Redis 客户端实例
# decode_responses=True 会自动将读取到的 bytes 解码为 string
redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
