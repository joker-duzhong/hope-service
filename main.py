"""
🟢 唯一入口 —— FastAPI 实例化，路由挂载，中间件配置
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import init_db
from core.exceptions import register_exception_handlers
from core.users import router as users_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 仅在 DEBUG 模式下自动建表，生产环境应使用 Alembic 迁移
    if settings.DEBUG:
        await init_db()
    yield


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        description="模块化单体后端服务",
        openapi_url=f"{settings.API_V1_PREFIX}/openapi.json",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 全局异常处理
    register_exception_handlers(app)

    # ==================== 路由挂载 ====================
    # Core: 用户授权
    app.include_router(users_router, prefix=settings.API_V1_PREFIX, tags=["用户授权"])

    # Apps: 在此挂载各业务模块路由
    # app.include_router(trade_router, prefix="/api/v1/trade", tags=["交易助手"])

    # 健康检查
    @app.get("/health", tags=["健康检查"])
    async def health_check():
        return {"status": "ok", "version": settings.APP_VERSION}

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )
