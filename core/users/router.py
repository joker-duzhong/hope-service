"""
用户授权路由 —— 仅解析请求，调用 service
"""
import urllib.parse
from typing import Literal
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.response import ResponseModel
from core.security import decode_token, rotate_refresh_token
from core.users.dependencies import get_current_user
from core.users.models import User
from core.users.schemas import (
    RefreshRequest,
    Token,
    UserResponse,
    UserUpdate,
    UsernameLogin,
    WechatAuthUrl,
    WechatLogin,
    SendSmsCodeRequest,
    SmsCodeResponse,
    PhoneLoginRequest,
    LoginResponse,
    BindPhoneRequest,
)
from core.users.services import UserService
from core.sms import generate_test_sms_code, send_sms_code
from core.auth_scope import PASSPORT_SCOPE, validate_scope
from core.users.identity_service import resolve_identity_scope, validate_oauth_target

router = APIRouter(prefix="/auth", tags=["用户授权"])


# ==================== 短信 ====================

@router.post("/sms/send", response_model=ResponseModel[SmsCodeResponse])
async def send_sms(req: SendSmsCodeRequest):
    """发送短信验证码"""
    if req.test == "hope":
        code = await generate_test_sms_code(req.phone)
        if code is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="测试验证码生成失败，请稍后重试或检查验证码频率限制",
            )
        return ResponseModel(message="测试验证码已生成", data=SmsCodeResponse(code=code))
    success = await send_sms_code(req.phone)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="短信服务调用失败，请检查服务端日志中的阿里云错误信息",
        )
    return ResponseModel(message="发送成功")

@router.post("/phone/login", response_model=ResponseModel[LoginResponse])
async def phone_login(
    req: PhoneLoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """手机号验证码登录，未注册自动创建账号。"""
    scope = req.app_key or PASSPORT_SCOPE
    validate_scope(scope)
    user = await UserService.login_with_phone(db, req.phone, req.code)
    return ResponseModel(data=await UserService.build_login_response(db, user, scope))


@router.post("/phone/bind", response_model=ResponseModel[UserResponse], deprecated=True)
async def phone_bind(
    req: BindPhoneRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """绑定手机号"""
    user = await UserService.bind_phone(
        db,
        user=current_user,
        phone=req.phone,
        code=req.code
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="验证码错误或手机号已被绑定"
        )
    return ResponseModel(data=await UserService.build_scoped_user_response(db, user, request.state.auth_scope))


# ==================== 登录 ====================

@router.post("/login", response_model=ResponseModel[LoginResponse])
async def login(
    login_data: UsernameLogin,
    db: AsyncSession = Depends(get_db),
):
    """仅供现有超级管理员使用的密码登录；普通用户请使用短信登录。"""
    user = await UserService.authenticate(db, login_data.username, login_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return ResponseModel(data=await UserService.build_login_response(db, user, "admin_web"))


# ==================== 微信登录 ====================

@router.get("/wechat/url", response_model=ResponseModel[WechatAuthUrl])
async def get_wechat_auth_url(
    redirect_uri: str,
    appid: str,
    state: str = "",
    scope: Literal["snsapi_base", "snsapi_userinfo"] = "snsapi_base",
):
    """获取微信授权页面URL"""
    validate_oauth_target(appid, redirect_uri)
    if not settings.get_wechat_config(appid):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未配置该公众号: {appid}",
        )
    params = urllib.parse.urlencode({
        "appid": appid,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
    })
    auth_url = f"https://open.weixin.qq.com/connect/oauth2/authorize?{params}#wechat_redirect"
    return ResponseModel(data=WechatAuthUrl(auth_url=auth_url))


@router.post("/wechat/login", response_model=ResponseModel[LoginResponse], deprecated=True)
async def wechat_login(
    login_data: WechatLogin,
    db: AsyncSession = Depends(get_db),
):
    """微信授权登录"""
    resolve_identity_scope("h5", login_data.appid)
    wx_config = settings.get_wechat_config(login_data.appid)
    if not wx_config or not wx_config.get("secret"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"未配置该公众号: {login_data.appid}",
        )
        
    real_secret = wx_config["secret"] 
    async with httpx.AsyncClient() as client:
        token_resp = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": login_data.appid,
                "secret": real_secret,
                "code": login_data.code,
                "grant_type": "authorization_code",
            },
        )
        token_info = token_resp.json()

    if "errcode" in token_info:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"微信授权失败: {token_info.get('errmsg', '未知错误')}",
        )

    openid = token_info.get("openid")
    unionid = token_info.get("unionid")
    wx_access_token = token_info.get("access_token")

    # 尝试获取用户信息
    nickname = None
    avatar = None
    try:
        async with httpx.AsyncClient() as client:
            user_resp = await client.get(
                "https://api.weixin.qq.com/sns/userinfo",
                params={"access_token": wx_access_token, "openid": openid},
            )
            user_info = user_resp.json()
            nickname = user_info.get("nickname")
            avatar = user_info.get("headimgurl")
    except Exception:
        pass

    user = await UserService.wechat_login(
        db, openid=openid, appid=login_data.appid, unionid=unionid, nickname=nickname, avatar=avatar,
    )

    return ResponseModel(data=await UserService.build_login_response(db, user))


# ==================== Token 管理 ====================

@router.post("/refresh", response_model=ResponseModel[Token])
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """刷新令牌"""
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh" or not payload.get("jti"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌"
        )

    user_id = payload.get("sub")
    try:
        user_id = UUID(user_id)
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌"
        )
    user = await UserService.get_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用"
        )
    scope = payload.get("app_scope")
    if not isinstance(scope, str) or not scope:
        raise HTTPException(401, "登录版本已更新，请重新登录")
    validate_scope(scope, user)
    if not getattr(user, "phone", None) and not (scope == "admin_web" and user.is_superuser):
        raise HTTPException(401, "请重新完成手机号验证")
    if payload.get("token_version") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="刷新令牌已失效"
        )

    token_pair = await rotate_refresh_token(body.refresh_token, user.id, user.token_version)
    if token_pair is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌"
        )
    access_token, refresh_token = token_pair
    return ResponseModel(data=Token(access_token=access_token, refresh_token=refresh_token))


# ==================== 用户信息 ====================

@router.get("/me", response_model=ResponseModel[UserResponse])
async def get_me(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户信息"""
    return ResponseModel(data=await UserService.build_scoped_user_response(db, current_user, request.state.auth_scope))


@router.put("/me", response_model=ResponseModel[UserResponse])
async def update_me(
    body: UserUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户信息"""
    user = await UserService.update_user_info(
        db,
        current_user,
        username=body.username,
        email=body.email,
        nickname=body.nickname,
        avatar=body.avatar,
    )
    return ResponseModel(data=await UserService.build_scoped_user_response(db, user, request.state.auth_scope))
