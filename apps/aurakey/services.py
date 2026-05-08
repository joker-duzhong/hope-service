import uuid
import random
import string
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional, Set

from sqlalchemy import select, desc, func, cast, Date
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from apps.aurakey.models import (
    AurakeyGallery, AurakeyGalleryLike, AurakeyTask,
    AurakeyUserAsset, AurakeyAssetLog, AurakeyProduct, AurakeyOrder,
    AurakeyModelOption
)
from apps.aurakey.schemas import (
    TaskGenerateRequest, TaskGenerateResponse, TaskStatusResponse
)
from core.database import async_session_maker
from core.llm.engine import generate_image, fetch_image_result


class AurakeyService:

    @staticmethod
    async def get_or_create_user_asset(db: AsyncSession, user_id: uuid.UUID) -> AurakeyUserAsset:
        stmt = select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == user_id)
        result = await db.execute(stmt)
        asset = result.scalars().first()
        if not asset:
            invite_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            asset = AurakeyUserAsset(user_id=user_id, balance=10, invite_code=invite_code) # default 10 points
            db.add(asset)
            await db.commit()
            await db.refresh(asset)
            
            # 初始化送 10 算力写流水
            log = AurakeyAssetLog(user_id=user_id, type=1, amount=10, balance_after=10, description="新用户注册赠送")
            db.add(log)
            await db.commit()
        return asset

    @staticmethod
    async def get_gallery_list(
        db: AsyncSession,
        page: int,
        page_size: int,
        current_user_id: Optional[uuid.UUID] = None,
        category_id: Optional[uuid.UUID] = None,
    ) -> Tuple[int, List[dict]]:
        conditions = [AurakeyGallery.is_deleted == False]
        if category_id:
            conditions.append(AurakeyGallery.category_id == category_id)

        total = await db.scalar(select(func.count()).select_from(AurakeyGallery).where(*conditions))

        stmt = (
            select(AurakeyGallery)
            .where(*conditions)
            .order_by(desc(AurakeyGallery.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        items = (await db.execute(stmt)).scalars().all()
        
        # 批量查询点赞状态，避免 N+1
        liked_ids: Set[uuid.UUID] = set()
        if current_user_id and items:
            gallery_ids = [item.id for item in items]
            liked_rows = await db.execute(
                select(AurakeyGalleryLike.gallery_id).where(
                    AurakeyGalleryLike.user_id == current_user_id,
                    AurakeyGalleryLike.gallery_id.in_(gallery_ids)
                )
            )
            liked_ids = {row[0] for row in liked_rows}

        res = []
        for item in items:
            res.append({
                "id": item.id,
                "thumb_url": item.thumb_url or item.image_url,
                "aspect_ratio": item.aspect_ratio or "1:1",
                "author": {
                    "user_id": item.user_id,
                    "nickname": item.author_nickname or "匿名用户",
                    "avatar": item.author_avatar or ""
                },
                "like_count": item.like_count,
                "is_liked": item.id in liked_ids,
                "view_count": item.view_count
            })
        return total, res

    @staticmethod
    async def get_gallery_detail(db: AsyncSession, gallery_id: uuid.UUID, current_user_id: Optional[uuid.UUID] = None) -> dict:
        item = await db.get(AurakeyGallery, gallery_id)
        if not item or item.is_deleted:
            raise HTTPException(status_code=404, detail="作品不存在")
            
        # 增加浏览量
        item.view_count += 1
        await db.commit()
        
        is_liked = False
        if current_user_id:
            like = await db.scalar(select(AurakeyGalleryLike).where(AurakeyGalleryLike.gallery_id == item.id, AurakeyGalleryLike.user_id == current_user_id))
            is_liked = bool(like)
            
        return {
            "id": item.id,
            "thumb_url": item.thumb_url or item.image_url, # TODO
            "image_url": item.image_url,
            "aspect_ratio": item.aspect_ratio or "1:1",
            "prompt": item.prompt,
            "model_name": item.model_name or "Pro v1.0",
            "author": {
                "user_id": item.user_id,
                "nickname": item.author_nickname or "匿名用户",
                "avatar": item.author_avatar or ""
            },
            "like_count": item.like_count,
            "is_liked": is_liked,
            "view_count": item.view_count
        }

    @staticmethod
    async def toggle_like(db: AsyncSession, gallery_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        item = await db.get(AurakeyGallery, gallery_id)
        if not item or item.is_deleted:
            raise HTTPException(status_code=404, detail="作品不存在")
            
        like = await db.scalar(select(AurakeyGalleryLike).where(AurakeyGalleryLike.gallery_id == item.id, AurakeyGalleryLike.user_id == user_id))
        
        if like:
            # 取消点赞
            await db.delete(like)
            item.like_count = max(0, item.like_count - 1)
            is_liked = False
        else:
            # 点赞
            new_like = AurakeyGalleryLike(user_id=user_id, gallery_id=gallery_id)
            db.add(new_like)
            item.like_count += 1
            is_liked = True
            
        await db.commit()
        return {"is_liked": is_liked, "like_count": item.like_count}

    @staticmethod
    async def submit_generate_task(db: AsyncSession, request: TaskGenerateRequest, user_id: uuid.UUID) -> TaskGenerateResponse:
        asset = await AurakeyService.get_or_create_user_asset(db, user_id)
        
        # 查模型配置
        model_opt = await db.scalar(select(AurakeyModelOption).where(AurakeyModelOption.model_id == request.model_name))
        
        cost = model_opt.cost if model_opt else 10
        is_vip_only = model_opt.is_vip_only if model_opt else False
        
        if is_vip_only and not asset.is_vip:
            raise HTTPException(status_code=403, detail="该模型仅限VIP可用")
            
        if asset.balance < cost:
            raise HTTPException(status_code=400, detail="算力不足")
            
        asset.balance -= cost
        log = AurakeyAssetLog(user_id=user_id, type=2, amount=-cost, balance_after=asset.balance, description=f"生成插画({request.model_name})")
        
        task = AurakeyTask(
            user_id=user_id,
            prompt=request.prompt,
            model_name=request.model_name,
            aspect_ratio=request.aspect_ratio,
            frozen_points=cost,
            cost=cost,
            status="pending"
        )
        
        db.add(log)
        db.add(task)
        await db.commit()
        await db.refresh(task)
        await db.refresh(asset)
        
        # 将原先发进 celery_task 的异步操作，改写为在主进程直接非阻塞提交上游网络请求，然后迅速返回
        # 这要求直接在这里走 engine 发起远程任务：
        try:
            task.status = "processing"
            task.progress = 10
            remote_task_id = await generate_image(
                prompt=task.prompt,
                model=task.model_name,
                ratio=task.aspect_ratio
            )
            task.remote_task_id = remote_task_id
            task.progress = 20
            await db.commit()
        except Exception as e:
            # 提交直接失败了，此时拦截并退费
            task.status = "failed"
            task.failed_reason = str(e)
            
            asset.balance += cost
            log_refund = AurakeyAssetLog(
                user_id=user_id, type=3, amount=cost, 
                balance_after=asset.balance, description="提交生图失败自动退回"
            )
            db.add(log_refund)
            await db.commit()
        
        return TaskGenerateResponse(task_id=task.id, frozen_points=cost, balance_after=asset.balance)

    @staticmethod
    async def get_task_status(db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID) -> TaskStatusResponse:
        task = await db.get(AurakeyTask, task_id)
        if not task or task.user_id != user_id:
            raise HTTPException(status_code=404, detail="任务不存在")
            
        # 若处于处理中且有上游ID，前端轮询时在服务端同步查询一次真实状态
        if task.status == "processing" and task.remote_task_id:
            try:
                task_res = await fetch_image_result(task_id=task.remote_task_id)
                res_status = task_res.get("status")
                
                if res_status == "SUCCESS":
                    task.status = "success"
                    task.progress = 100
                    urls = task_res.get("image_urls", [])
                    if urls:
                        task.image_url = urls[0]
                    else:
                        task.status = "failed"
                        task.failed_reason = "生图成功但上游未返回图片链接"
                elif res_status == "FAILURE":
                    task.status = "failed"
                    task.failed_reason = task_res.get("msg", "上游 API 生图失败")
                else:
                    # pending / RUNNING
                    if task.progress < 95:
                        task.progress += 2
                        
                await db.commit()
            except Exception as e:
                # 轮询如果发生网络异常等，不要直接标记为 failed，可能只是暂时的抖动
                # 这里只打印日志，返回旧状态即可
                print(f"Fetch image result failed for remote_id {task.remote_task_id}: {e}")
                
            # 若转为了 failed，且尚未退款（frozen_points > 0），则自动给用户退费
            if task.status == "failed" and task.frozen_points > 0:
                asset = await db.scalar(select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == task.user_id))
                if asset:
                    refund_amount = task.frozen_points
                    asset.balance += refund_amount
                    task.frozen_points = 0  # 标记为已退款，防止重复退款
                    log = AurakeyAssetLog(
                        user_id=task.user_id, type=3, amount=refund_amount,
                        balance_after=asset.balance, description="生图失败自动退回"
                    )
                    db.add(log)
                await db.commit()
            
        return TaskStatusResponse(
            task_id=task.id,
            status=task.status,
            progress=task.progress,
            imageUrl=task.image_url,
            failedReason=task.failed_reason
        )

    @staticmethod
    async def publish_history_task(db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID, username: str, user_avatar: str):
        task = await db.get(AurakeyTask, task_id)
        if not task or task.user_id != user_id or task.status != "success":
            raise HTTPException(status_code=404, detail="任务不存在或无法发布")
            
        task.is_published = True
        
        gallery = AurakeyGallery(
            user_id=user_id,
            author_nickname=username,
            author_avatar=user_avatar,
            image_url=task.image_url,
            prompt=task.prompt,
            model_name=task.model_name,
            aspect_ratio=task.aspect_ratio,
            task_id=task.id
        )
        db.add(gallery)
        await db.commit()
        return {"status": "published"}

    @staticmethod
    async def handle_wechat_notify(db: AsyncSession, order_no: str, is_success: bool):
        order = await db.scalar(select(AurakeyOrder).where(AurakeyOrder.order_no == order_no))
        if not order or order.status != "waiting":
            return
            
        if is_success:
            order.status = "success"
            # 发放资产
            asset = await AurakeyService.get_or_create_user_asset(db, order.user_id)
            product = await db.get(AurakeyProduct, order.product_id)
            
            if product.type == "point_pack":
                total_add = product.point_amount + product.bonus_amount
                asset.balance += total_add
                log = AurakeyAssetLog(
                    user_id=order.user_id, type=1, amount=total_add, 
                    balance_after=asset.balance, description=f"充值购买 {product.name}"
                )
                db.add(log)
            elif product.type == "vip":
                asset.is_vip = True
                asset.vip_type = product.tag
                now_utc = datetime.now(timezone.utc)
                base = asset.vip_expire_time if asset.vip_expire_time and asset.vip_expire_time > now_utc else now_utc
                asset.vip_expire_time = base + timedelta(days=30)
                
            await db.commit()
        else:
            order.status = "failed"
            await db.commit()

    @staticmethod
    async def daily_sign_in(db: AsyncSession, user_id: uuid.UUID) -> dict:
        """每日签到，返回本次奖励和连续签到天数"""
        today_utc = datetime.now(timezone.utc).date()

        # 检查今日是否已签到（type=4）
        # 注意: 统一按 UTC 当天的起止时间范围查询，避免 cast 带来的时区问题
        today_start = datetime.combine(today_utc, datetime.min.time()).replace(tzinfo=timezone.utc)
        today_end = today_start + timedelta(days=1)
        
        already = await db.scalar(
            select(AurakeyAssetLog).where(
                AurakeyAssetLog.user_id == user_id,
                AurakeyAssetLog.type == 4,
                AurakeyAssetLog.created_at >= today_start,
                AurakeyAssetLog.created_at < today_end,
            )
        )
        if already:
            raise HTTPException(status_code=400, detail="今日已签到，明天再来吧")

        asset = await AurakeyService.get_or_create_user_asset(db, user_id)
        reward = 10
        asset.balance += reward

        log = AurakeyAssetLog(
            user_id=user_id, type=4, amount=reward,
            balance_after=asset.balance, description="每日签到奖励"
        )
        db.add(log)
        await db.commit()

        # 统计连续签到天数（包括刚签到的今天）
        sign_logs = (await db.execute(
            select(AurakeyAssetLog.created_at)
            .where(AurakeyAssetLog.user_id == user_id, AurakeyAssetLog.type == 4)
            .order_by(desc(AurakeyAssetLog.created_at))
            .limit(365)
        )).scalars().all()

        continuous_days = 0
        check_date = today_utc
        # 确保日志日期映射到 UTC date 以保持一致性
        sign_dates = {log_dt.astimezone(timezone.utc).date() if log_dt.tzinfo else log_dt.date() for log_dt in sign_logs}
        
        while check_date in sign_dates:
            continuous_days += 1
            check_date -= timedelta(days=1)

        return {"rewardPoints": reward, "continuousDays": continuous_days}
