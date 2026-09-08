from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel
from core.users import scan_service
from core.users.dependencies import get_current_user
from core.users.models import User
from core.users.scan_schemas import (
    ScanAppResponse, ScanCreateRequest, ScanCreateResponse, ScanExchangeRequest,
    ScanPollResponse, ScanStateResponse,
)
from core.users.schemas import LoginResponse


async def scan_request_guard(request: Request, response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    client_ip = request.client.host if request.client else "unknown"
    await scan_service.rate_limit("request", client_ip, scan_service.REQUEST_LIMIT_PER_MINUTE)


router = APIRouter(
    prefix="/auth/scan", tags=["扫码登录"], dependencies=[Depends(scan_request_guard)],
)
PollToken = Annotated[str, Header(alias="X-Scan-Token", min_length=32, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")]


@router.get("/apps", response_model=ResponseModel[list[ScanAppResponse]], summary="查询可用扫码应用")
async def list_scan_apps():
    """公开应用标识和名称，不返回微信配置、密钥或内部模块路径。"""
    return ResponseModel(data=scan_service.available_apps())


@router.post("/sessions", response_model=ResponseModel[ScanCreateResponse], summary="创建扫码登录会话")
async def create_scan_session(body: ScanCreateRequest, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    await scan_service.rate_limit("create", client_ip, scan_service.CREATE_LIMIT_PER_MINUTE)
    return ResponseModel(data=await scan_service.create_session(body.app_key))


@router.get("/sessions/{transaction_id}", response_model=ResponseModel[ScanPollResponse], summary="发起端轮询扫码状态")
async def poll_scan_session(transaction_id: UUID, poll_token: PollToken):
    return ResponseModel(data=await scan_service.poll_session(transaction_id, poll_token))


@router.get("/sessions/{transaction_id}/info", response_model=ResponseModel[ScanStateResponse], summary="手机端获取扫码展示信息")
async def scan_session_info(transaction_id: UUID):
    return ResponseModel(data=await scan_service.session_info(transaction_id))


@router.post("/sessions/{transaction_id}/scanned", response_model=ResponseModel[ScanStateResponse], summary="手机端通知已扫码")
async def mark_scanned(transaction_id: UUID):
    """无需登录；只推进展示状态，不能授权或获得登录凭据。"""
    return ResponseModel(data=await scan_service.transition(transaction_id, "scanned"))


@router.post("/sessions/{transaction_id}/confirm", response_model=ResponseModel[ScanStateResponse], summary="手机端确认扫码登录")
async def confirm_scan(transaction_id: UUID, user: User = Depends(get_current_user)):
    """以当前登录用户授权，必须先绑定手机号；不能传 user_id 指定他人。"""
    return ResponseModel(data=await scan_service.transition(transaction_id, "confirm", user))


@router.post("/sessions/{transaction_id}/cancel", response_model=ResponseModel[ScanStateResponse], summary="手机端拒绝扫码登录")
async def cancel_scan(transaction_id: UUID, user: User = Depends(get_current_user)):
    return ResponseModel(data=await scan_service.transition(transaction_id, "cancel", user))


@router.post("/exchange", response_model=ResponseModel[LoginResponse], summary="发起端一次性兑换登录凭据")
async def exchange_scan(
    body: ScanExchangeRequest, poll_token: PollToken, db: AsyncSession = Depends(get_db),
):
    return ResponseModel(data=await scan_service.exchange_session(
        body.transaction_id, body.exchange_code, poll_token, db,
    ))
