"""
微信小程序登录路由
"""
import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.response import ResponseModel
from core.security import create_access_token, create_refresh_token
from core.users.dependencies import get_current_user
from core.users.models import User
from core.users.schemas import Token, UserResponse
from core.users.miniapp_schemas import MiniappLoginRequest, MiniappPhoneRequest
from core.users.services import UserService

router = APIRouter(prefix="/auth/miniapp", tags=["小程序登录"])


@router.post("/login", response_model=ResponseModel[Token])
async def miniapp_login(
    req: MiniappLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """小程序登录（使用 wx.login 获取的 code）"""
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

    # 复用公众号登录逻辑，自动注册或登录
    user = await UserService.wechat_login(
        db,
        openid=openid,
        unionid=unionid,
        nickname=None,
        avatar=None,
    )

    return ResponseModel(
        data=Token(
            access_token=create_access_token(subject=user.id),
            refresh_token=create_refresh_token(subject=user.id),
        )
    )


@router.post("/phone", response_model=ResponseModel[UserResponse])
async def miniapp_get_phone(
    req: MiniappPhoneRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取并绑定手机号（使用 getPhoneNumber 返回的 code）"""
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

    # 检查手机号是否已被其他用户绑定
    existing = await UserService.get_by_phone(db, phone)
    if existing and existing.id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该手机号已被其他用户绑定",
        )

    # 直接更新用户手机号（小程序获取的手机号已通过微信验证，无需短信验证码）
    current_user.phone = phone
    await db.commit()
    await db.refresh(current_user)

    return ResponseModel(data=await UserService.build_user_response(db, current_user))
