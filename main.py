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
from core.admin import router as admin_router


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

    # Core: 管理后台
    app.include_router(admin_router, prefix=settings.API_V1_PREFIX, tags=["管理后台"])

    # Core: 资源存储
    from core.storage.router import router as storage_router
    app.include_router(storage_router, prefix=settings.API_V1_PREFIX, tags=["资源存储"])

    # Core: 微信认证服务
    from core.wechat.router import router as wechat_router
    app.include_router(wechat_router, prefix=f"{settings.API_V1_PREFIX}", tags=["微信认证"])

    # Apps: 在此挂载各业务模块路由
    from apps.trade_copilot.router import router as trade_copilot_router
    app.include_router(trade_copilot_router, prefix=f"{settings.API_V1_PREFIX}/trade-copilot", tags=["交易助手"])

    from apps.just_right.router import router as just_right_router
    app.include_router(just_right_router, prefix=f"{settings.API_V1_PREFIX}/just-right", tags=["恰好"])

    from apps.nest_talk.router import router as nest_talk_router
    app.include_router(nest_talk_router, prefix=f"{settings.API_V1_PREFIX}/nest-talk", tags=["语筑"])

    # Apps: 时空图书馆 (Time Library)
    from apps.time_library.router import router as time_library_router
    from apps.time_library.admin_router import router as time_library_admin_router
    app.include_router(time_library_router, prefix=f"{settings.API_V1_PREFIX}/time-library", tags=["时空图书馆"])
    app.include_router(time_library_admin_router, prefix=f"{settings.API_V1_PREFIX}/time-library/admin", tags=["时空图书馆-管理端"])

    # Apps: AI Gateway
    from apps.ai_gateway.router import router as ai_gateway_router
    app.include_router(ai_gateway_router, prefix=f"{settings.API_V1_PREFIX}/ai", tags=["AI对话网关"])

    # Apps: 在线高考 (Zaiwen Gaokao)
    from apps.zaiwen_gaokao.router import router as gaokao_router
    app.include_router(gaokao_router, prefix=f"{settings.API_V1_PREFIX}/zaiwen-gaokao", tags=["在线高考"])

    # Apps: Project Sisyphus (西西弗斯认知引擎)
    from apps.project_sisyphus.router import router as sisyphus_router
    app.include_router(sisyphus_router, prefix=f"{settings.API_V1_PREFIX}/sisyphus", tags=["西西弗斯认知引擎"])

    # Apps: 影子董事会 (Shadow Board AI)
    from apps.shadow_board.router import router as shadow_board_router
    app.include_router(shadow_board_router, prefix=f"{settings.API_V1_PREFIX}/shadow-board", tags=["影子董事会"])

    # Apps: TypoCraft (言图)
    from apps.typo_craft.router import router as typo_craft_router
    app.include_router(typo_craft_router, prefix=f"{settings.API_V1_PREFIX}/typo-craft", tags=["言图引擎"])

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
