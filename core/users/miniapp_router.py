"""
微信小程序登录路由
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.response import ResponseModel
from core.users.dependencies import get_current_user
from core.users.models import User
from core.users.schemas import LoginResponse, UserResponse
from core.users.miniapp_schemas import MiniappLoginRequest, MiniappPhoneRequest
from core.users.services import UserService
from core.users.identity_service import resolve_identity_scope

router = APIRouter(prefix="/auth/miniapp", tags=["小程序登录"])


@router.post("/login", response_model=ResponseModel[LoginResponse], deprecated=True)
async def miniapp_login(
    req: MiniappLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """小程序登录（使用 wx.login 获取的 code）"""
    app_scope = resolve_identity_scope("miniapp", req.appid)
    wx_config = settings.get_wechat_config(req.appid)
    if not wx_config or not wx_config.get("secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未配置该小程序: {req.appid}",
        )

    # 调用微信 code2Session 接口
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            "https://api.weixin.qq.com/sns/jscode2session",
            params={
                "appid": req.appid,
                "secret": wx_config["secret"],
                "js_code": req.code,
                "grant_type": "authorization_code",
            },
        )
        data = resp.json()

    if "errcode" in data and data["errcode"] != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"微信登录失败: {data.get('errmsg', '未知错误')}",
        )

    openid = data.get("openid")
    unionid = data.get("unionid")

    if not openid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="获取 openid 失败",
        )

    user = await UserService.wechat_login(
        db,
        openid=openid,
        appid=req.appid,
        unionid=unionid,
        nickname=None,
        avatar=None,
    )

    return ResponseModel(data=await UserService.build_login_response(db, user, app_scope))


@router.post("/phone", response_model=ResponseModel[UserResponse], deprecated=True)
async def miniapp_get_phone(
    req: MiniappPhoneRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取并绑定手机号（使用 getPhoneNumber 返回的 code）"""
    app_scope = resolve_identity_scope("miniapp", req.appid)
    if request.state.auth_scope != app_scope:
        raise HTTPException(403, "当前凭据不适用于该小程序")
    wx_config = settings.get_wechat_config(req.appid)
    if not wx_config or not wx_config.get("secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未配置该小程序: {req.appid}",
        )

    # 获取小程序 access_token
    async with httpx.AsyncClient() as client:
        token_resp = await client.get(
            "https://api.weixin.qq.com/cgi-bin/token",
            params={
                "grant_type": "client_credential",
                "appid": req.appid,
                "secret": wx_config["secret"],
            },
        )
        token_data = token_resp.json()

    if "errcode" in token_data and token_data["errcode"] != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"获取 access_token 失败: {token_data.get('errmsg')}",
        )

    access_token = token_data.get("access_token")

    # 使用 code 换取手机号
    async with httpx.AsyncClient() as client:
        phone_resp = await client.post(
            f"https://api.weixin.qq.com/wxa/business/getuserphonenumber?access_token={access_token}",
            json={"code": req.code},
        )
        phone_data = phone_resp.json()

    if phone_data.get("errcode") != 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"获取手机号失败: {phone_data.get('errmsg')}",
        )

    phone_info = phone_data.get("phone_info", {})
    phone = phone_info.get("phoneNumber")

    if not phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="未获取到手机号",
        )

    current_user = await UserService.bind_verified_phone(db, current_user, phone)

    return ResponseModel(data=await UserService.build_scoped_user_response(db, current_user, app_scope))
