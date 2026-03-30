# API v1 版本
from fastapi import APIRouter

from app.api.v1 import auth, tools

api_router = APIRouter()

# 注册路由
api_router.include_router(auth.router)
api_router.include_router(tools.router)
