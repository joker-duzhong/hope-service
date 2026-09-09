from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel
from core.users import identity_service, scan_service
from core.users.identity_schemas import (
    AuthenticatedIdentityResponse, CompleteMiniappIdentityRequest,
    CompleteSmsIdentityRequest, H5IdentityRequest, IdentityRequest, IdentityResponse,
)


async def identity_guard(request: Request, response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
    client_ip = request.client.host if request.client else "unknown"
    await scan_service.rate_limit("identity", client_ip, 30)


router = APIRouter(prefix="/auth/identity", tags=["两阶段身份登录"], dependencies=[Depends(identity_guard)])


@router.post("/h5", response_model=ResponseModel[IdentityResponse])
async def identify_h5(body: H5IdentityRequest, db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=await identity_service.identify(db, "h5", body))


@router.post("/miniapp", response_model=ResponseModel[IdentityResponse])
async def identify_miniapp(body: IdentityRequest, db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=await identity_service.identify(db, "miniapp", body))


@router.post("/complete/sms", response_model=ResponseModel[AuthenticatedIdentityResponse])
async def complete_sms(body: CompleteSmsIdentityRequest, db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=await identity_service.complete_sms(db, body.login_ticket, body.phone, body.code))


@router.post("/complete/miniapp-phone", response_model=ResponseModel[AuthenticatedIdentityResponse])
async def complete_miniapp(body: CompleteMiniappIdentityRequest, db: AsyncSession = Depends(get_db)):
    return ResponseModel(data=await identity_service.complete_miniapp(db, body.login_ticket, body.phone_code))
