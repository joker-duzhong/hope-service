"""
🟢 唯一入口 —— FastAPI 实例化，路由挂载，中间件配置
"""
from contextlib import asynccontextmanager
from importlib import import_module

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.apps_config import REGISTERED_APPS, AppConfig
from core.config import settings
from core.database import init_db
from core.dependencies import bind_app_key
from core.exceptions import register_exception_handlers
from core.users import router as users_router
from core.admin import router as admin_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    settings.validate_runtime_security()
    # 仅在 DEBUG 模式下自动建表，生产环境应使用 Alembic 迁移
    if settings.DEBUG:
        await init_db()
    yield


def register_business_apps(app: FastAPI) -> None:
    """Register every business router from the shared application registry."""
    for app_config in REGISTERED_APPS.values():
        if app_config.key == "admin_web":
            continue

        for router_config in app_config.router_modules:
            if not app_config.is_active:
                continue

            module = import_module(router_config.module)
            router = getattr(module, "router")
            app.include_router(
                router,
                prefix=f"{settings.API_V1_PREFIX}{router_config.prefix}",
                tags=router_config.tags,
                dependencies=[Depends(bind_app_key(app_config.key))],
            )


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
        # 优化 OpenAPI 配置，便于前端 SDK 生成
        servers=[
            {"url": f"http://localhost:{settings.PORT}", "description": "本地开发环境"},
            {"url": "https://api.lxyy.fun", "description": "生产环境"},
        ],
        contact={
            "name": "API Support",
            "email": "support@example.com",
        },
        license_info={
            "name": "Private",
        },
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

    # Core: 管理后台
    app.include_router(admin_router, prefix=settings.API_V1_PREFIX, tags=["管理后台"])

    # Core: 资源存储
    from core.storage.router import router as storage_router
    app.include_router(storage_router, prefix=settings.API_V1_PREFIX, tags=["资源存储"])

    # Core: 微信认证服务
    from core.wechat.router import router as wechat_router
    app.include_router(wechat_router, prefix=f"{settings.API_V1_PREFIX}", tags=["微信认证"])

    # Core: 微信小程序登录
    from core.users.miniapp_router import router as miniapp_router
    app.include_router(miniapp_router, prefix=settings.API_V1_PREFIX, tags=["小程序登录"])

    # Core: 支付回调
    from core.pay.router import router as pay_router
    app.include_router(pay_router, prefix=settings.API_V1_PREFIX, tags=["支付"])

    register_business_apps(app)

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
