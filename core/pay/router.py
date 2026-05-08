from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.pay.wechat_pay import WechatPayNotificationHandler

router = APIRouter()


@router.post("/payments/wechat/notify", summary="微信支付统一回调")
async def wechat_pay_notify(request: Request, db: AsyncSession = Depends(get_db)):
    body = await request.body()
    handler = WechatPayNotificationHandler()
    try:
        await handler.handle_notification(db, dict(request.headers), body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(status_code=204)
