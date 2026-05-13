import uuid
from datetime import datetime, date, timedelta, timezone
from typing import List, Tuple, Dict, Any, Optional

from sqlalchemy import select, func, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from apps.aurakey.models import (
    AurakeyUserAsset, AurakeyTask, AurakeyOrder,
    AurakeyGalleryCategory, AurakeyModelOption, AurakeyAspectRatioOption,
    AurakeyAssetLog, AurakeyProduct
)
from apps.aurakey.services import AurakeyService
from core.users.models import User

class AurakeyAdminService:
    @staticmethod
    async def get_dashboard_stats(db: AsyncSession) -> Dict[str, Any]:
        today_utc = datetime.now(timezone.utc).date()
        today_start = datetime.combine(today_utc, datetime.min.time()).replace(tzinfo=timezone.utc)
        yesterday_start = today_start - timedelta(days=1)

        # today new users
        query_new = select(func.count(User.id)).where(User.created_at >= today_start)
        today_new_users = (await db.execute(query_new)).scalar() or 0

        # today generations
        query_gen = select(func.count(AurakeyTask.id)).where(AurakeyTask.created_at >= today_start)
        today_generations = (await db.execute(query_gen)).scalar() or 0

        # today revenue (success orders)
        query_rev = select(func.sum(AurakeyOrder.amount)).where(
            AurakeyOrder.status == "success",
            AurakeyOrder.pay_method.in_(["wechat_mini", "alipay"]),
            AurakeyOrder.created_at >= today_start
        )
        today_revenue = (await db.execute(query_rev)).scalar() or 0

        # yesterday revenue
        query_yest_rev = select(func.sum(AurakeyOrder.amount)).where(
            AurakeyOrder.status == "success",
            AurakeyOrder.pay_method.in_(["wechat_mini", "alipay"]),
            AurakeyOrder.created_at >= yesterday_start,
            AurakeyOrder.created_at < today_start
        )
        yesterday_revenue = (await db.execute(query_yest_rev)).scalar() or 0

        growth_rate = 0.0
        if yesterday_revenue > 0:
            growth_rate = ((today_revenue - yesterday_revenue) / yesterday_revenue) * 100.0
        elif today_revenue > 0:
            growth_rate = 100.0

        return {
            "today_new_users": today_new_users,
            "today_active_users": 0, # Placeholder depending on login logs
            "today_generations": today_generations,
            "today_revenue": today_revenue,
            "revenue_growth_rate": round(growth_rate, 1)
        }

    @staticmethod
    async def adjust_balance(db: AsyncSession, user_id: uuid.UUID, amount: int, remark: str) -> Dict[str, Any]:
        asset = (await db.execute(select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == user_id))).scalar_one_or_none()
        if not asset:
            # Create if not exist
            asset = AurakeyUserAsset(user_id=user_id, balance=0, invite_code=str(uuid.uuid4())[:8].upper())
            db.add(asset)
            await db.flush()

        actual_amount = amount
        if amount > 0:
            await AurakeyService._credit_points(
                db,
                asset,
                amount,
                description=remark or "管理员系统调节",
                source_type="admin",
            )
        elif amount < 0:
            deducted_amount, _ = await AurakeyService._spend_points(
                db,
                asset,
                abs(amount),
                description=remark or "管理员系统调节",
                allow_partial=True,
            )
            actual_amount = -deducted_amount
        log = AurakeyAssetLog(
            user_id=user_id,
            type=99, # admin adjust
            amount=actual_amount,
            balance_after=asset.balance,
            description=remark or "管理员系统调节"
        )
        db.add(log)
        await db.commit()
        return {"is_success": True, "balance_after": asset.balance}

    @staticmethod
    async def refund_order(db: AsyncSession, order_no: str, remark: str) -> Dict[str, Any]:
        order = (await db.execute(select(AurakeyOrder).where(AurakeyOrder.order_no == order_no))).scalar_one_or_none()
        if not order or order.status != "success":
            return {"is_success": False, "refund_id": None, "deducted_points": 0}
        
        asset = (await db.execute(select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == order.user_id))).scalar_one_or_none()
        
        deducted = 0

        if asset:
            deducted = order.granted_points or 0
            if deducted <= 0:
                product = await db.get(AurakeyProduct, order.product_id)
                if product:
                    deducted = product.point_amount + product.bonus_amount
                else:
                    deducted = 100 # Fallback safety

            deducted, _ = await AurakeyService._spend_points(
                db,
                asset,
                deducted,
                description=f"订单退款自动扣除({remark or ''})",
                allow_partial=True,
            )

            log = AurakeyAssetLog(
                user_id=order.user_id,
                type=98, # refund deduct
                amount=-deducted,
                balance_after=asset.balance,
                description=f"订单退款自动扣除({remark or ''})"
            )
            db.add(log)

        order.status = "refunded"
        if asset:
            await AurakeyService.refresh_asset_state(db, asset)
        await db.commit()
        
        return {"is_success": True, "refund_id": f"REF{order_no}", "deducted_points": deducted}

    @staticmethod
    async def toggle_ban(db: AsyncSession, user_id: uuid.UUID, target_status: str):
        user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
        if user:
            user.is_active = (target_status == "normal")
            await db.commit()
        return target_status

    @staticmethod
    async def get_product(db: AsyncSession, product_id: uuid.UUID) -> Optional[AurakeyProduct]:
        return await db.get(AurakeyProduct, product_id)

    @staticmethod
    async def create_product(db: AsyncSession, req) -> AurakeyProduct:
        product = AurakeyProduct(
            type=req.type,
            name=req.name,
            price=req.price,
            original_price=req.original_price,
            point_amount=req.point_amount,
            bonus_amount=req.bonus_amount,
            tag=req.tag,
            vip_type=req.vip_type,
            vip_level=req.vip_level,
            valid_days=req.valid_days,
        )
        db.add(product)
        await db.commit()
        await db.refresh(product)
        return product

    @staticmethod
    async def update_product(db: AsyncSession, product_id: uuid.UUID, req) -> Optional[AurakeyProduct]:
        product = await db.get(AurakeyProduct, product_id)
        if not product:
            return None
        
        update_data = req.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(product, key, value)
            
        await db.commit()
        await db.refresh(product)
        return product

    @staticmethod
    async def delete_product(db: AsyncSession, product_id: uuid.UUID) -> bool:
        product = await db.get(AurakeyProduct, product_id)
        if not product:
            return False
        product.is_deleted = True
        await db.commit()
        return True
