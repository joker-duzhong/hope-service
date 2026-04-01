"""
用户授权路由 —— 仅解析请求，调用 service
"""
import urllib.parse

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from core.response import ResponseModel
from core.security import create_access_token, create_refresh_token, decode_token
from core.users.dependencies import get_current_user
from core.users.models import User
from core.users.schemas import (
    RefreshRequest,
    Token,
    UserCreate,
    UserResponse,
    UserUpdate,
    UsernameLogin,
    WechatAuthUrl,
    WechatLogin,
)
from core.users.services import UserService

router = APIRouter(prefix="/auth", tags=["用户授权"])


# ==================== 注册 ====================

@router.post("/register", response_model=ResponseModel[UserResponse])
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """用户名密码注册"""
    if user_data.username:
        existing = await UserService.get_by_username(db, user_data.username)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="用户名已存在"
            )

    if user_data.phone:
        existing = await UserService.get_by_phone(db, user_data.phone)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="手机号已被注册"
            )

    user = await UserService.create_by_username(
        db,
        username=user_data.username,
        password=user_data.password,
        phone=user_data.phone,
        nickname=user_data.nickname,
        source=user_data.source,
    )
    return ResponseModel(data=UserResponse.model_validate(user))


# ==================== 登录 ====================

@router.post("/login", response_model=ResponseModel[Token])
async def login(
    login_data: UsernameLogin,
    db: AsyncSession = Depends(get_db),
):
    """用户名密码登录"""
    user = await UserService.authenticate(db, login_data.username, login_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return ResponseModel(
        data=Token(
            access_token=create_access_token(subject=user.id),
            refresh_token=create_refresh_token(subject=user.id),
        )
    )


# ==================== 微信登录 ====================

@router.get("/wechat/url", response_model=ResponseModel[WechatAuthUrl])
async def get_wechat_auth_url(
    redirect_uri: str,
    state: str = "",
    scope: str = "snsapi_userinfo",
):
    """获取微信授权页面URL"""
    params = urllib.parse.urlencode({
        "appid": settings.WECHAT_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": scope,
        "state": state,
    })
    auth_url = f"https://open.weixin.qq.com/connect/oauth2/authorize?{params}#wechat_redirect"
    return ResponseModel(data=WechatAuthUrl(auth_url=auth_url))


@router.post("/wechat/login", response_model=ResponseModel[Token])
async def wechat_login(
    login_data: WechatLogin,
    db: AsyncSession = Depends(get_db),
):
    """微信授权登录"""
    async with httpx.AsyncClient() as client:
        token_resp = await client.get(
            "https://api.weixin.qq.com/sns/oauth2/access_token",
            params={
                "appid": settings.WECHAT_APP_ID,
                "secret": settings.WECHAT_APP_SECRET,
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
        db, openid=openid, unionid=unionid, nickname=nickname, avatar=avatar,
    )

    return ResponseModel(
        data=Token(
            access_token=create_access_token(subject=user.id),
            refresh_token=create_refresh_token(subject=user.id),
        )
    )


# ==================== Token 管理 ====================

@router.post("/refresh", response_model=ResponseModel[Token])
async def refresh_token(
    body: RefreshRequest,
    db: AsyncSession = Depends(get_db),
):
    """刷新令牌"""
    payload = decode_token(body.refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的刷新令牌"
        )

    user_id = payload.get("sub")
    user = await UserService.get_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在或已禁用"
        )

    return ResponseModel(
        data=Token(
            access_token=create_access_token(subject=user.id),
            refresh_token=create_refresh_token(subject=user.id),
        )
    )


# ==================== 用户信息 ====================

@router.get("/me", response_model=ResponseModel[UserResponse])
async def get_me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息"""
    return ResponseModel(data=UserResponse.model_validate(current_user))


@router.put("/me", response_model=ResponseModel[UserResponse])
async def update_me(
    body: UserUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户信息"""
    user = await UserService.update_user_info(
        db, current_user, nickname=body.nickname, avatar=body.avatar,
    )
    return ResponseModel(data=UserResponse.model_validate(user))
