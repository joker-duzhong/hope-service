"""
认证接口
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.models.user import User
from app.schemas import ResponseModel
from app.schemas.user import (
    Token,
    UserResponse,
    UsernameLogin,
    UserCreate,
    WechatLogin,
    WechatAuthUrl,
    PhoneLogin,
    SendSmsCode,
)
from app.services.auth import AuthService
from app.services.yuzhu import wechat_service
from app.utils.dependencies import get_current_user

router = APIRouter(prefix="/auth", tags=["认证"])


# ==================== 微信授权登录 ====================

@router.get("/wechat/url", response_model=ResponseModel[WechatAuthUrl])
async def get_wechat_auth_url(
    redirect_uri: str,
    state: str = "",
    scope: str = "snsapi_userinfo",
):
    """
    获取微信授权页面URL

    前端重定向到此URL让用户授权
    """
    auth_url = wechat_service.get_oauth_url(
        redirect_uri=redirect_uri,
        state=state,
        scope=scope,
    )
    return ResponseModel(data=WechatAuthUrl(auth_url=auth_url))


@router.post("/wechat/login", response_model=ResponseModel[Token])
async def wechat_login(
    login_data: WechatLogin,
    db: AsyncSession = Depends(get_db),
):
    """
    微信授权登录

    流程：
    1. 前端通过 /auth/wechat/url 获取授权URL
    2. 用户在微信页面授权后，微信回调到前端，带上code
    3. 前端调用此接口，传入code
    4. 后端用code换取access_token和openid
    5. 根据openid查找或创建用户，返回JWT token
    """
    try:
        # 用code换取access_token和openid
        token_info = await wechat_service.get_access_token(login_data.code)

        if "errcode" in token_info:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"微信授权失败: {token_info.get('errmsg', '未知错误')}"
            )

        openid = token_info.get("openid")
        access_token = token_info.get("access_token")
        unionid = token_info.get("unionid")

        # 获取用户信息
        user_info = {}
        try:
            user_info = await wechat_service.get_user_info(access_token, openid)
        except Exception:
            # 如果是snsapi_base授权，可能无法获取用户信息
            pass

        nickname = user_info.get("nickname")
        avatar = user_info.get("headimgurl")

        # 登录或注册
        user = await AuthService.wechat_login(
            db,
            openid=openid,
            unionid=unionid,
            nickname=nickname,
            avatar=avatar,
        )

        # 生成JWT token
        jwt_access_token = create_access_token(subject=user.id)
        jwt_refresh_token = create_refresh_token(subject=user.id)

        return ResponseModel(
            data=Token(
                access_token=jwt_access_token,
                refresh_token=jwt_refresh_token,
            )
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"登录失败: {str(e)}"
        )


# ==================== 用户名密码登录（可选） ====================

@router.post("/register", response_model=ResponseModel[UserResponse])
async def register(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db),
):
    """用户名密码注册"""
    # 检查用户名是否已存在
    if user_data.username:
        existing_user = await AuthService.get_by_username(db, user_data.username)
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="用户名已存在"
            )

    # 检查手机号是否已存在
    if user_data.phone:
        existing_phone = await AuthService.get_by_phone(db, user_data.phone)
        if existing_phone:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号已被注册"
            )

    # 创建用户
    user = await AuthService.create_by_username(
        db,
        username=user_data.username,
        password=user_data.password,
        phone=user_data.phone,
        nickname=user_data.nickname,
        source=user_data.source,
    )
    return ResponseModel(data=UserResponse.model_validate(user))


@router.post("/login", response_model=ResponseModel[Token])
async def login(
    login_data: UsernameLogin,
    db: AsyncSession = Depends(get_db),
):
    """用户名密码登录"""
    user = await AuthService.authenticate(
        db, login_data.username, login_data.password
    )
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # 生成令牌
    access_token = create_access_token(subject=user.id)
    refresh_token = create_refresh_token(subject=user.id)

    return ResponseModel(
        data=Token(
            access_token=access_token,
            refresh_token=refresh_token,
        )
    )


# ==================== 手机号验证码登录（预留） ====================

@router.post("/sms/send", response_model=ResponseModel)
async def send_sms_code(
    data: SendSmsCode,
):
    """
    发送短信验证码（预留）

    TODO: 接入短信服务
    - 阿里云短信
    - 腾讯云短信
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="短信服务暂未开放"
    )


@router.post("/phone/login", response_model=ResponseModel[Token])
async def phone_login(
    login_data: PhoneLogin,
    db: AsyncSession = Depends(get_db),
):
    """
    手机号验证码登录（预留）
    """
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail="短信登录暂未开放"
    )


# ==================== Token管理 ====================

@router.post("/refresh", response_model=ResponseModel[Token])
async def refresh_token(
    refresh_token: str,
    db: AsyncSession = Depends(get_db),
):
    """刷新令牌"""
    payload = decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的刷新令牌"
        )

    user_id = payload.get("sub")
    user = await AuthService.get_by_id(db, user_id)
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用"
        )

    # 生成新令牌
    new_access_token = create_access_token(subject=user.id)
    new_refresh_token = create_refresh_token(subject=user.id)

    return ResponseModel(
        data=Token(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
        )
    )


# ==================== 用户信息 ====================

@router.get("/me", response_model=ResponseModel[UserResponse])
async def get_me(
    current_user: User = Depends(get_current_user),
):
    """获取当前用户信息"""
    return ResponseModel(data=UserResponse.model_validate(current_user))


@router.put("/me", response_model=ResponseModel[UserResponse])
async def update_me(
    nickname: str = None,
    avatar: str = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户信息"""
    user = await AuthService.update_user_info(
        db,
        current_user,
        nickname=nickname,
        avatar=avatar,
    )
    return ResponseModel(data=UserResponse.model_validate(user))
