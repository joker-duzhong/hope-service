"""
全局自定义异常与拦截器
"""
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class AppException(Exception):
    """业务异常基类"""

    def __init__(self, code: int = 400, message: str = "请求失败", detail: str = ""):
        self.code = code
        self.message = message
        self.detail = detail


class NotFoundException(AppException):
    def __init__(self, message: str = "资源不存在"):
        super().__init__(code=404, message=message)


class UnauthorizedException(AppException):
    def __init__(self, message: str = "未授权"):
        super().__init__(code=401, message=message)


class ForbiddenException(AppException):
    def __init__(self, message: str = "权限不足"):
        super().__init__(code=403, message=message)


class BadRequestException(AppException):
    def __init__(self, message: str = "请求参数错误"):
        super().__init__(code=400, message=message)


# Alias for backward compatibility
CustomException = AppException


def register_exception_handlers(app: FastAPI) -> None:
    """在 FastAPI 实例上注册全局异常处理器"""

    @app.exception_handler(AppException)
    async def app_exception_handler(_request: Request, exc: AppException):
        return JSONResponse(
            status_code=exc.code,
            content={
                "code": exc.code,
                "message": exc.message,
                "data": exc.detail or None,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail, "data": None},
            headers=exc.headers,
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_exception_handler(_request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail, "data": None},
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError):
        errors = [
            {"type": error["type"], "loc": error["loc"], "msg": error["msg"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"code": 422, "message": "请求参数校验失败", "data": errors},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Request, _exc: Exception):
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"code": 500, "message": "服务器内部错误", "data": None},
        )
