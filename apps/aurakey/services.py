import uuid
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Optional, Set, Any

from sqlalchemy import select, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from apps.aurakey.models import (
    AurakeyGalleryLike, AurakeyTask,
    AurakeyUserAsset, AurakeyAssetLog, AurakeyProduct, AurakeyOrder,
    AurakeyModelOption, AurakeyPointGrant, AurakeySystemConfig
)
from apps.aurakey.schemas import (
    TaskGenerateRequest, TaskGenerateResponse, TaskStatusResponse, TaskStreamGenerateRequest
)
from apps.aurakey.config import (
    AURAKEY_SYSTEM_CONFIG_KEY,
    merge_aurakey_config,
    get_default_aurakey_config,
)
from core.database import async_session_maker
from core.llm.engine import generate_image, fetch_image_result
from core.storage.services import StorageService
from core.users.models import User


logger = logging.getLogger(__name__)
LOCAL_TZ = timezone(timedelta(hours=8))


class AurakeyService:

    GALLERY_APPROVED_STATUS = "approved"
    DEFAULT_TASK_DURATION_SECONDS = 120
    MAX_TASK_DURATION_SECONDS = 600

    @staticmethod
    def _now_utc() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _to_ts(value: Optional[datetime]) -> Optional[int]:
        return int(value.timestamp()) if value else None

    @staticmethod
    def _resolve_product_vip_type(product: AurakeyProduct) -> str:
        return (product.vip_type or product.tag or product.name or "VIP").strip()

    @staticmethod
    def _resolve_product_valid_days(product: AurakeyProduct, *, config: Optional[dict[str, Any]] = None) -> Optional[int]:
        if product.valid_days is not None:
            return product.valid_days
        if product.type == "vip":
            if config is None:
                config = get_default_aurakey_config()
            return config.get("default_vip_valid_days", 30)
        if product.type == "point_pack":
            if config is None:
                config = get_default_aurakey_config()
            return config.get("default_point_pack_valid_days")
        return None

    @staticmethod
    def _next_reset_at(now_utc: Optional[datetime] = None, reset_hour: int = 12) -> datetime:
        current_utc = now_utc or AurakeyService._now_utc()
        local_now = current_utc.astimezone(LOCAL_TZ)
        candidate = local_now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if local_now >= candidate:
            candidate += timedelta(days=1)
        return candidate.astimezone(timezone.utc)

    @staticmethod
    async def get_system_config(db: AsyncSession) -> dict[str, Any]:
        stmt = select(AurakeySystemConfig).where(AurakeySystemConfig.key == AURAKEY_SYSTEM_CONFIG_KEY)
        config = (await db.execute(stmt)).scalar_one_or_none()
        return merge_aurakey_config(config.value if config else None)

    @staticmethod
    async def save_system_config(db: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
        stmt = select(AurakeySystemConfig).where(AurakeySystemConfig.key == AURAKEY_SYSTEM_CONFIG_KEY)
        config = (await db.execute(stmt)).scalar_one_or_none()
        merged = merge_aurakey_config(payload)
        if not config:
            config = AurakeySystemConfig(key=AURAKEY_SYSTEM_CONFIG_KEY, value=merged)
            db.add(config)
        else:
            config.value = merged
        await db.commit()
        return merged

    @staticmethod
    def _task_has_resource(task: AurakeyTask) -> bool:
        return task.image_resource_id is not None

    @staticmethod
    async def _get_task_resource(db: AsyncSession, task: AurakeyTask):
        if not task.image_resource_id:
            return None
        return await StorageService.get_resource_response_or_none(db, task.image_resource_id)

    @staticmethod
    async def _get_task_resource_map(db: AsyncSession, tasks: List[AurakeyTask]):
        resource_ids = [task.image_resource_id for task in tasks if task.image_resource_id]
        return await StorageService.get_resources_by_ids(db, resource_ids)

    @staticmethod
    def _task_reference_image_ids(task: AurakeyTask) -> List[uuid.UUID]:
        ids: List[uuid.UUID] = []
        for value in task.reference_image_ids or []:
            try:
                ids.append(value if isinstance(value, uuid.UUID) else uuid.UUID(str(value)))
            except (TypeError, ValueError):
                continue
        return ids

    @staticmethod
    async def _get_task_reference_images(db: AsyncSession, task: AurakeyTask):
        resource_map = await StorageService.get_resources_by_ids(db, AurakeyService._task_reference_image_ids(task))
        return [resource_map[resource_id] for resource_id in AurakeyService._task_reference_image_ids(task) if resource_id in resource_map]

    @staticmethod
    async def _get_task_reference_image_map(db: AsyncSession, tasks: List[AurakeyTask]):
        resource_ids: List[uuid.UUID] = []
        for task in tasks:
            resource_ids.extend(AurakeyService._task_reference_image_ids(task))
        return await StorageService.get_resources_by_ids(db, list(dict.fromkeys(resource_ids)))

    @staticmethod
    def _coerce_progress(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, str):
            value = value.strip().rstrip("%")
        try:
            progress = int(float(value))
        except (TypeError, ValueError):
            return None
        return max(0, min(100, progress))

    @staticmethod
    def _normalize_task_time(value: Optional[datetime]) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=LOCAL_TZ)
        return value.astimezone(timezone.utc)

    @staticmethod
    async def _get_recent_average_task_duration_seconds(
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        limit: int = 10,
    ) -> int:
        stmt = (
            select(AurakeyTask.created_at, AurakeyTask.updated_at)
            .where(
                AurakeyTask.user_id == user_id,
                AurakeyTask.status == "success",
                AurakeyTask.is_deleted == False,
            )
            .order_by(desc(AurakeyTask.updated_at))
            .limit(limit)
        )
        rows = (await db.execute(stmt)).all()
        durations = []
        for created_at, updated_at in rows:
            created_at = AurakeyService._normalize_task_time(created_at)
            updated_at = AurakeyService._normalize_task_time(updated_at)
            if not created_at or not updated_at:
                continue
            seconds = (updated_at - created_at).total_seconds()
            if seconds > 0:
                durations.append(min(seconds, AurakeyService.MAX_TASK_DURATION_SECONDS))
        if not durations:
            return AurakeyService.DEFAULT_TASK_DURATION_SECONDS
        return max(1, int(sum(durations) / len(durations)))

    @staticmethod
    def _simulate_task_progress(task: AurakeyTask, average_duration_seconds: int) -> int:
        if task.status == "success":
            return 100
        if task.status == "failed":
            return AurakeyService._coerce_progress(task.progress) or 100
        current_progress = AurakeyService._coerce_progress(task.progress) or 0
        if task.status != "processing":
            return current_progress

        created_at = AurakeyService._normalize_task_time(task.created_at)
        if not created_at:
            return min(current_progress, 99)
        now = AurakeyService._now_utc()
        elapsed_seconds = max(0, (now - created_at).total_seconds())
        simulated = int(elapsed_seconds / max(1, average_duration_seconds) * 100)
        return min(99, max(current_progress, simulated))

    @staticmethod
    async def resolve_task_progress(
        db: AsyncSession,
        task: AurakeyTask,
        *,
        upstream_progress: Any = None,
        average_duration_seconds: Optional[int] = None,
    ) -> int:
        if task.status == "success":
            task.progress = 100
            return task.progress

        progress = AurakeyService._coerce_progress(upstream_progress)
        if progress is not None:
            if task.status == "processing":
                progress = min(progress, 99)
            task.progress = progress
            return task.progress

        if average_duration_seconds is None:
            average_duration_seconds = await AurakeyService._get_recent_average_task_duration_seconds(db, task.user_id)
        task.progress = AurakeyService._simulate_task_progress(task, average_duration_seconds)
        return task.progress

    @staticmethod
    async def _validate_reference_images(db: AsyncSession, resource_ids: List[uuid.UUID]):
        if not resource_ids:
            return {}
        unique_ids = list(dict.fromkeys(resource_ids))
        resource_map = await StorageService.get_resources_by_ids(db, unique_ids)
        missing_ids = [resource_id for resource_id in unique_ids if resource_id not in resource_map]
        if missing_ids:
            raise HTTPException(status_code=400, detail="参考图资源不存在")
        if any(not (resource.type or "").startswith("image/") for resource in resource_map.values()):
            raise HTTPException(status_code=400, detail="参考图资源必须是图片类型")
        return resource_map

    @staticmethod
    async def _sync_point_balance(db: AsyncSession, asset: AurakeyUserAsset) -> List[AurakeyPointGrant]:
        now_utc = AurakeyService._now_utc()
        stmt = select(AurakeyPointGrant).where(
            AurakeyPointGrant.user_id == asset.user_id,
            AurakeyPointGrant.remaining_amount > 0,
            AurakeyPointGrant.is_deleted == False,
        )
        grants = (await db.execute(stmt)).scalars().all()
        active_grants: List[AurakeyPointGrant] = []
        active_total = 0
        changed = False
        for grant in grants:
            if grant.expires_at and grant.expires_at <= now_utc:
                grant.remaining_amount = 0
                changed = True
            else:
                active_grants.append(grant)
                active_total += grant.remaining_amount
        if asset.balance != active_total:
            asset.balance = active_total
            changed = True
        if changed:
            await db.flush()
        return active_grants

    @staticmethod
    async def _credit_points(
        db: AsyncSession,
        asset: AurakeyUserAsset,
        amount: int,
        *,
        description: str,
        source_type: str,
        source_id: Optional[uuid.UUID] = None,
        expires_at: Optional[datetime] = None,
    ) -> Optional[AurakeyPointGrant]:
        if amount <= 0:
            return None
        asset.balance += amount
        grant = AurakeyPointGrant(
            user_id=asset.user_id,
            source_type=source_type,
            source_id=source_id,
            amount=amount,
            remaining_amount=amount,
            expires_at=expires_at,
            description=description,
        )
        db.add(grant)
        return grant

    @staticmethod
    async def _spend_points(
        db: AsyncSession,
        asset: AurakeyUserAsset,
        amount: int,
        *,
        description: str,
        allow_partial: bool = False,
    ) -> tuple[int, list[dict[str, Any]]]:
        if amount <= 0:
            return 0, []
        active_grants = await AurakeyService._sync_point_balance(db, asset)
        if asset.balance < amount and not allow_partial:
            raise HTTPException(status_code=400, detail="算力不足")

        remaining = amount
        deducted = 0
        allocation: list[dict[str, Any]] = []
        sorted_grants = sorted(
            active_grants,
            key=lambda grant: (
                grant.expires_at is None,
                grant.expires_at or datetime.max.replace(tzinfo=timezone.utc),
                grant.created_at,
            ),
        )
        for grant in sorted_grants:
            if remaining <= 0:
                break
            take = min(grant.remaining_amount, remaining)
            if take <= 0:
                continue
            grant.remaining_amount -= take
            remaining -= take
            deducted += take
            allocation.append(
                {
                    "grant_id": str(grant.id),
                    "amount": take,
                    "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                }
            )

        if deducted <= 0:
            return 0, []
        asset.balance = max(0, asset.balance - deducted)
        await db.flush()
        if remaining > 0 and allow_partial:
            logger.warning("Partial point spend for user %s, requested=%s deducted=%s", asset.user_id, amount, deducted)
        return deducted, allocation

    @staticmethod
    async def _restore_points(
        db: AsyncSession,
        asset: AurakeyUserAsset,
        allocation: list[dict[str, Any]],
        fallback_amount: int,
        *,
        description: str,
    ) -> int:
        restored = 0
        if allocation:
            for item in allocation:
                amount = int(item.get("amount") or 0)
                if amount <= 0:
                    continue
                expires_at_raw = item.get("expires_at")
                expires_at = datetime.fromisoformat(expires_at_raw) if expires_at_raw else None
                grant_id = item.get("grant_id")
                grant = None
                if grant_id:
                    grant = await db.get(AurakeyPointGrant, uuid.UUID(str(grant_id)))
                if grant:
                    grant.remaining_amount += amount
                    if grant.expires_at is None and expires_at is not None:
                        grant.expires_at = expires_at
                else:
                    db.add(
                        AurakeyPointGrant(
                            user_id=asset.user_id,
                            source_type="refund",
                            source_id=None,
                            amount=amount,
                            remaining_amount=amount,
                            expires_at=expires_at,
                            description=description,
                        )
                    )
                restored += amount
        elif fallback_amount > 0:
            db.add(
                AurakeyPointGrant(
                    user_id=asset.user_id,
                    source_type="refund",
                    source_id=None,
                    amount=fallback_amount,
                    remaining_amount=fallback_amount,
                    expires_at=None,
                    description=description,
                )
            )
            restored = fallback_amount

        if restored > 0:
            asset.balance += restored
            await db.flush()
        return restored

    @staticmethod
    async def _get_current_vip_state(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
        now_utc = AurakeyService._now_utc()
        stmt = (
            select(AurakeyOrder)
            .where(
                AurakeyOrder.user_id == user_id,
                AurakeyOrder.status == "success",
                AurakeyOrder.product_type == "vip",
                AurakeyOrder.is_deleted == False,
            )
            .order_by(desc(AurakeyOrder.entitlement_expire_at), desc(AurakeyOrder.paid_at), desc(AurakeyOrder.created_at))
        )
        rows = (await db.execute(stmt)).scalars().all()

        active_orders = []
        for order in rows:
            expire_at = order.entitlement_expire_at
            if expire_at is None:
                expire_at = order.paid_at
                if expire_at and order.valid_days is not None:
                    expire_at = expire_at + timedelta(days=order.valid_days)
            if expire_at and expire_at > now_utc:
                active_orders.append((order, expire_at))

        if not active_orders:
            return {
                "is_vip": False,
                "vip_type": "普通会员",
                "vip_expire_time": None,
                "vip_level": 0,
            }

        active_orders.sort(
            key=lambda item: (
                item[0].vip_level,
                item[1],
                item[0].paid_at or item[0].created_at,
            ),
            reverse=True,
        )
        order, expire_at = active_orders[0]
        max_expire_at = max(item[1] for item in active_orders)
        return {
            "is_vip": True,
            "vip_type": order.vip_type or order.product_name or "VIP",
            "vip_expire_time": max_expire_at,
            "vip_level": order.vip_level or 0,
        }

    @staticmethod
    async def refresh_asset_state(db: AsyncSession, asset: AurakeyUserAsset) -> dict[str, Any]:
        active_grants = await AurakeyService._sync_point_balance(db, asset)
        vip_state = await AurakeyService._get_current_vip_state(db, asset.user_id)
        asset.is_vip = vip_state["is_vip"]
        asset.vip_type = vip_state["vip_type"] if vip_state["is_vip"] else None
        asset.vip_expire_time = vip_state["vip_expire_time"]
        await db.flush()
        return {
            "balance": asset.balance,
            "active_grants": active_grants,
            **vip_state,
        }

    @staticmethod
    async def get_or_create_user_asset(db: AsyncSession, user_id: uuid.UUID) -> AurakeyUserAsset:
        stmt = select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == user_id)
        result = await db.execute(stmt)
        asset = result.scalars().first()
        if not asset:
            invite_code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            config = await AurakeyService.get_system_config(db)
            initial_reward = int(config.get("register_reward_points", 10) or 0)
            asset = AurakeyUserAsset(user_id=user_id, balance=0, invite_code=invite_code)
            db.add(asset)
            await db.commit()
            await db.refresh(asset)
            if initial_reward > 0:
                await AurakeyService._credit_points(
                    db,
                    asset,
                    initial_reward,
                    description="新用户注册赠送",
                    source_type="signup",
                )
                log = AurakeyAssetLog(
                    user_id=user_id,
                    type=1,
                    amount=initial_reward,
                    balance_after=asset.balance,
                    description="新用户注册赠送",
                )
                db.add(log)
            await db.commit()
        await AurakeyService.refresh_asset_state(db, asset)
        return asset

    @staticmethod
    async def get_gallery_list(
        db: AsyncSession,
        page: int,
        page_size: int,
        current_user_id: Optional[uuid.UUID] = None,
        category_id: Optional[uuid.UUID] = None,
    ) -> Tuple[int, List[dict]]:
        conditions = [
            AurakeyTask.is_deleted == False,
            AurakeyTask.status == "success",
            AurakeyTask.is_published == True,
            AurakeyTask.publish_status == AurakeyService.GALLERY_APPROVED_STATUS,
            AurakeyTask.image_resource_id.isnot(None),
        ]
        if category_id:
            conditions.append(AurakeyTask.category_id == category_id)

        total = await db.scalar(select(func.count()).select_from(AurakeyTask).where(*conditions))

        stmt = (
            select(AurakeyTask, User)
            .outerjoin(User, User.id == AurakeyTask.user_id)
            .where(*conditions)
            .order_by(desc(AurakeyTask.published_at), desc(AurakeyTask.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).all()
        tasks = [task for task, _user in rows]
        resource_map = await AurakeyService._get_task_resource_map(db, tasks)
        
        # 批量查询点赞状态，避免 N+1
        liked_ids: Set[uuid.UUID] = set()
        if current_user_id and rows:
            task_ids = [task.id for task, _user in rows]
            liked_rows = await db.execute(
                select(AurakeyGalleryLike.gallery_id).where(
                    AurakeyGalleryLike.user_id == current_user_id,
                    AurakeyGalleryLike.gallery_id.in_(task_ids)
                )
            )
            liked_ids = {row[0] for row in liked_rows}

        res = []
        for item, user in rows:
            resource = resource_map.get(item.image_resource_id)
            if not resource:
                continue
            nickname = ((user.nickname or user.username) if user else None) or "匿名用户"
            avatar = (user.avatar if user else None) or ""
            res.append({
                "id": item.id,
                "resource": resource,
                "aspect_ratio": item.aspect_ratio or "1:1",
                "author": {
                    "user_id": item.user_id,
                    "nickname": nickname,
                    "avatar": avatar
                },
                "like_count": item.like_count,
                "is_liked": item.id in liked_ids,
                "view_count": item.view_count,
                "prompt": item.prompt
            })
        return total or 0, res

    @staticmethod
    async def get_admin_gallery_list(
        db: AsyncSession,
        page: int,
        page_size: int,
        publish_status: Optional[str] = None,
        is_published: Optional[bool] = None,
        category_id: Optional[uuid.UUID] = None,
        user_id: Optional[uuid.UUID] = None,
        keyword: Optional[str] = None,
    ) -> Tuple[int, List[dict[str, Any]]]:
        if publish_status and publish_status not in {"approved", "blocked"}:
            raise HTTPException(status_code=400, detail="发布审核状态必须是 approved 或 blocked")

        conditions = [
            AurakeyTask.is_deleted == False,
            AurakeyTask.status == "success",
            AurakeyTask.image_resource_id.isnot(None),
        ]
        if publish_status:
            conditions.append(AurakeyTask.publish_status == publish_status)
        if is_published is not None:
            conditions.append(AurakeyTask.is_published == is_published)
        if category_id:
            conditions.append(AurakeyTask.category_id == category_id)
        if user_id:
            conditions.append(AurakeyTask.user_id == user_id)
        keyword = keyword.strip() if keyword else None
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(
                    AurakeyTask.prompt.ilike(pattern),
                    AurakeyTask.model_name.ilike(pattern),
                    User.username.ilike(pattern),
                    User.nickname.ilike(pattern),
                    User.phone.ilike(pattern),
                    User.openid.ilike(pattern),
                )
            )

        total_stmt = (
            select(func.count())
            .select_from(AurakeyTask)
            .outerjoin(User, User.id == AurakeyTask.user_id)
            .where(*conditions)
        )
        total = await db.scalar(total_stmt)

        stmt = (
            select(AurakeyTask, User)
            .outerjoin(User, User.id == AurakeyTask.user_id)
            .where(*conditions)
            .order_by(desc(AurakeyTask.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        rows = (await db.execute(stmt)).all()
        tasks = [task for task, _user in rows]
        resource_map = await AurakeyService._get_task_resource_map(db, tasks)

        return total or 0, [
            AurakeyService._task_to_admin_gallery_item(task, user, resource_map.get(task.image_resource_id))
            for task, user in rows
            if resource_map.get(task.image_resource_id)
        ]

    @staticmethod
    async def get_gallery_detail(db: AsyncSession, gallery_id: uuid.UUID, current_user_id: Optional[uuid.UUID] = None) -> dict:
        stmt = (
            select(AurakeyTask, User)
            .outerjoin(User, User.id == AurakeyTask.user_id)
            .where(
                AurakeyTask.id == gallery_id,
                AurakeyTask.is_deleted == False,
                AurakeyTask.status == "success",
                AurakeyTask.is_published == True,
                AurakeyTask.publish_status == AurakeyService.GALLERY_APPROVED_STATUS,
                AurakeyTask.image_resource_id.isnot(None),
            )
        )
        row = (await db.execute(stmt)).first()
        if not row:
            raise HTTPException(status_code=404, detail="作品不存在")
        item, user = row
            
        # 增加浏览量
        item.view_count += 1
        await db.commit()
        
        is_liked = False
        if current_user_id:
            like = await db.scalar(select(AurakeyGalleryLike).where(AurakeyGalleryLike.gallery_id == item.id, AurakeyGalleryLike.user_id == current_user_id))
            is_liked = bool(like)
            
        nickname = ((user.nickname or user.username) if user else None) or "匿名用户"
        avatar = (user.avatar if user else None) or ""
        resource = await AurakeyService._get_task_resource(db, item)
        if not resource:
            raise HTTPException(status_code=404, detail="作品资源不存在")
        reference_image_ids = AurakeyService._task_reference_image_ids(item)
        return {
            "id": item.id,
            "resource": resource,
            "aspect_ratio": item.aspect_ratio or "1:1",
            "prompt": item.prompt,
            "model_name": item.model_name or "Pro v1.0",
            "reference_images_ids": reference_image_ids,
            "reference_images": await AurakeyService._get_task_reference_images(db, item),
            "author": {
                "user_id": item.user_id,
                "nickname": nickname,
                "avatar": avatar
            },
            "like_count": item.like_count,
            "is_liked": is_liked,
            "view_count": item.view_count
        }

    @staticmethod
    async def toggle_like(db: AsyncSession, gallery_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        item = await db.get(AurakeyTask, gallery_id)
        if (
            not item
            or item.is_deleted
            or item.status != "success"
            or not item.is_published
            or not item.image_resource_id
            or item.publish_status != AurakeyService.GALLERY_APPROVED_STATUS
        ):
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

        deducted, allocation = await AurakeyService._spend_points(
            db,
            asset,
            cost,
            description=f"生成插画({request.model_name})",
        )
        log = AurakeyAssetLog(user_id=user_id, type=2, amount=-deducted, balance_after=asset.balance, description=f"生成插画({request.model_name})")

        task = AurakeyTask(
            user_id=user_id,
            prompt=request.prompt,
            model_name=request.model_name,
            aspect_ratio=request.aspect_ratio,
            frozen_points=deducted,
            cost=deducted,
            point_deductions=allocation,
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
            
            refund_amount = await AurakeyService._restore_points(
                db,
                asset,
                task.point_deductions or [],
                task.frozen_points,
                description="提交生图失败自动退回",
            )
            task.point_deductions = []
            task.frozen_points = 0
            log_refund = AurakeyAssetLog(
                user_id=user_id, type=3, amount=refund_amount,
                balance_after=asset.balance, description="提交生图失败自动退回"
            )
            db.add(log_refund)
            await db.commit()

        return TaskGenerateResponse(task_id=task.id, frozen_points=deducted, balance_after=asset.balance)

    @staticmethod
    async def submit_stream_generate_task(db: AsyncSession, request: TaskStreamGenerateRequest, user_id: uuid.UUID) -> TaskGenerateResponse:
        asset = await AurakeyService.get_or_create_user_asset(db, user_id)
        reference_image_ids = list(dict.fromkeys(request.reference_images_ids))
        await AurakeyService._validate_reference_images(db, reference_image_ids)

        model_opt = await db.scalar(select(AurakeyModelOption).where(AurakeyModelOption.model_id == request.model_name))
        cost = model_opt.cost if model_opt else 10
        is_vip_only = model_opt.is_vip_only if model_opt else False

        if is_vip_only and not asset.is_vip:
            raise HTTPException(status_code=403, detail="该模型仅限VIP可用")

        deducted, allocation = await AurakeyService._spend_points(
            db,
            asset,
            cost,
            description=f"流式生成插画({request.model_name})",
        )
        task = AurakeyTask(
            user_id=user_id,
            prompt=request.prompt,
            model_name=request.model_name,
            aspect_ratio=request.aspect_ratio,
            category_id=request.category_id,
            frozen_points=deducted,
            cost=deducted,
            point_deductions=allocation,
            reference_image_ids=[str(resource_id) for resource_id in reference_image_ids],
            status="processing",
            progress=5,
        )
        log = AurakeyAssetLog(
            user_id=user_id,
            type=2,
            amount=-deducted,
            balance_after=asset.balance,
            description=f"流式生成插画({request.model_name})",
        )
        db.add(task)
        db.add(log)
        await db.commit()
        await db.refresh(task)
        await db.refresh(asset)

        from apps.aurakey.tasks import run_stream_image_task
        run_stream_image_task.delay(str(task.id), request.is_public)

        return TaskGenerateResponse(task_id=task.id, frozen_points=deducted, balance_after=asset.balance)

    @staticmethod
    async def get_task_status(db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID) -> TaskStatusResponse:
        task = await db.get(AurakeyTask, task_id)
        if not task or task.user_id != user_id:
            raise HTTPException(status_code=404, detail="任务不存在")
            
        # 若处于处理中且有上游ID，前端轮询时在服务端同步查询一次真实状态
        progress_resolved_during_poll = False
        if task.status == "processing" and task.remote_task_id:
            try:
                task_res = await fetch_image_result(task_id=task.remote_task_id)
                res_status = task_res.get("status")
                
                if res_status == "SUCCESS":
                    urls = task_res.get("image_urls", [])
                    if urls:
                        resource = await StorageService.upload_remote_file(
                            db=db,
                            remote_url=urls[0],
                            owner_id=task.user_id,
                            scope="hope_aurakey",
                        )
                        task.image_resource_id = resource.id
                        task.image_url = None
                        task.status = "success"
                        task.progress = 100
                    else:
                        task.status = "failed"
                        task.failed_reason = "生图成功但上游未返回图片链接"
                elif res_status == "FAILURE":
                    task.status = "failed"
                    task.failed_reason = task_res.get("msg", "上游 API 生图失败")
                else:
                    await AurakeyService.resolve_task_progress(
                        db,
                        task,
                        upstream_progress=task_res.get("progress"),
                    )
                    progress_resolved_during_poll = True
                        
                await db.commit()
            except Exception as e:
                # 轮询如果发生网络异常等，不要直接标记为 failed，可能只是暂时的抖动
                # 这里只打印日志，返回旧状态即可
                print(f"Fetch image result failed for remote_id {task.remote_task_id}: {e}")
                
            # 若转为了 failed，且尚未退款（frozen_points > 0），则自动给用户退费
            if task.status == "failed" and task.frozen_points > 0:
                asset = await db.scalar(select(AurakeyUserAsset).where(AurakeyUserAsset.user_id == task.user_id))
                if asset:
                    refund_amount = await AurakeyService._restore_points(
                        db,
                        asset,
                        task.point_deductions or [],
                        task.frozen_points,
                        description="生图失败自动退回",
                    )
                    task.frozen_points = 0  # 标记为已退款，防止重复退款
                    task.point_deductions = []
                    log = AurakeyAssetLog(
                        user_id=task.user_id, type=3, amount=refund_amount,
                        balance_after=asset.balance, description="生图失败自动退回"
                    )
                    db.add(log)
                await db.commit()
        if task.status == "processing" and not progress_resolved_during_poll:
            old_progress = task.progress
            await AurakeyService.resolve_task_progress(db, task)
            if task.progress != old_progress:
                await db.commit()
            
        return TaskStatusResponse(
            task_id=task.id,
            status=task.status,
            progress=task.progress,
            resource=await AurakeyService._get_task_resource(db, task),
            reference_images_ids=AurakeyService._task_reference_image_ids(task),
            reference_images=await AurakeyService._get_task_reference_images(db, task),
            failed_reason=task.failed_reason
        )

    @staticmethod
    async def publish_task_to_gallery(
        db: AsyncSession,
        task: AurakeyTask,
        author_nickname: Optional[str] = None,
        author_avatar: Optional[str] = None,
    ):
        if not task.image_resource_id:
            raise HTTPException(status_code=400, detail="任务尚未生成图片资源，无法公开")
        task.is_published = True
        task.publish_status = task.publish_status or AurakeyService.GALLERY_APPROVED_STATUS
        if not task.published_at:
            task.published_at = AurakeyService._now_utc()

    @staticmethod
    def _task_publish_state(task: AurakeyTask) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "is_published": task.is_published,
            "publish_status": task.publish_status,
            "category_id": task.category_id,
            "published_at": AurakeyService._to_ts(task.published_at),
        }

    @staticmethod
    def _task_to_admin_gallery_item(task: AurakeyTask, user: Optional[User], resource=None) -> dict[str, Any]:
        return {
            "task_id": task.id,
            "user": {
                "user_id": task.user_id,
                "username": user.username if user else None,
                "nickname": user.nickname if user else None,
                "avatar": user.avatar if user else None,
            },
            "resource": resource,
            "prompt": task.prompt,
            "model_name": task.model_name,
            "aspect_ratio": task.aspect_ratio,
            "status": task.status,
            "cost": task.cost,
            "is_published": task.is_published,
            "publish_status": task.publish_status,
            "category_id": task.category_id,
            "like_count": task.like_count,
            "view_count": task.view_count,
            "published_at": AurakeyService._to_ts(task.published_at),
            "created_at": AurakeyService._to_ts(task.created_at),
        }

    @staticmethod
    async def update_task_publish_state(
        db: AsyncSession,
        task_id: uuid.UUID,
        user_id: uuid.UUID,
        is_published: bool,
        category_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        task = await db.get(AurakeyTask, task_id)
        if not task or task.user_id != user_id or task.is_deleted or task.status != "success":
            raise HTTPException(status_code=404, detail="任务不存在或无法发布")
        if is_published and not task.image_resource_id:
            raise HTTPException(status_code=400, detail="任务尚未生成图片，无法公开")

        task.is_published = is_published
        if category_id is not None:
            task.category_id = category_id
        if is_published and not task.published_at:
            task.published_at = AurakeyService._now_utc()
        await db.commit()
        return AurakeyService._task_publish_state(task)

    @staticmethod
    async def update_task_publish_state_by_admin(
        db: AsyncSession,
        task_id: uuid.UUID,
        is_published: bool,
        category_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        task = await db.get(AurakeyTask, task_id)
        if not task or task.is_deleted:
            raise HTTPException(status_code=404, detail="任务不存在")
        if is_published and (task.status != "success" or not task.image_resource_id):
            raise HTTPException(status_code=400, detail="仅成功且已生成图片的任务可公开")

        task.is_published = is_published
        if category_id is not None:
            task.category_id = category_id
        if is_published and not task.published_at:
            task.published_at = AurakeyService._now_utc()
        await db.commit()
        return AurakeyService._task_publish_state(task)

    @staticmethod
    async def batch_update_task_publish_state_by_admin(
        db: AsyncSession,
        task_ids: List[uuid.UUID],
        is_published: bool,
        category_id: Optional[uuid.UUID] = None,
    ) -> dict[str, Any]:
        unique_task_ids = list(dict.fromkeys(task_ids))
        if not unique_task_ids:
            raise HTTPException(status_code=400, detail="任务 ID 列表不能为空")

        stmt = select(AurakeyTask).where(AurakeyTask.id.in_(unique_task_ids))
        tasks = (await db.execute(stmt)).scalars().all()
        task_map = {task.id: task for task in tasks}

        items: list[dict[str, Any]] = []
        failed_items: list[dict[str, Any]] = []
        now = AurakeyService._now_utc()

        for task_id in unique_task_ids:
            task = task_map.get(task_id)
            if not task or task.is_deleted:
                failed_items.append({"task_id": task_id, "reason": "任务不存在"})
                continue
            if is_published and (task.status != "success" or not task.image_resource_id):
                failed_items.append({"task_id": task_id, "reason": "仅成功且已生成图片的任务可公开"})
                continue

            task.is_published = is_published
            if category_id is not None:
                task.category_id = category_id
            if is_published and not task.published_at:
                task.published_at = now
            items.append(AurakeyService._task_publish_state(task))

        await db.commit()
        return {
            "updated_count": len(items),
            "failed_count": len(failed_items),
            "items": items,
            "failed_items": failed_items,
        }

    @staticmethod
    async def update_task_publish_review_status(
        db: AsyncSession,
        task_id: uuid.UUID,
        publish_status: str,
    ) -> dict[str, Any]:
        if publish_status not in {"approved", "blocked"}:
            raise HTTPException(status_code=400, detail="发布审核状态必须是 approved 或 blocked")
        task = await db.get(AurakeyTask, task_id)
        if not task or task.is_deleted:
            raise HTTPException(status_code=404, detail="任务不存在")

        task.publish_status = publish_status
        await db.commit()
        return AurakeyService._task_publish_state(task)

    @staticmethod
    async def publish_history_task(db: AsyncSession, task_id: uuid.UUID, user_id: uuid.UUID, username: str, user_avatar: str):
        task = await db.get(AurakeyTask, task_id)
        if not task or task.user_id != user_id or task.status != "success":
            raise HTTPException(status_code=404, detail="任务不存在或无法发布")

        res = await AurakeyService.update_task_publish_state(db, task_id, user_id, True, task.category_id)
        return {
            "status": "published",
            "publish_status": res["publish_status"],
            "is_published": res["is_published"],
        }

    @staticmethod
    async def handle_wechat_notify(
        db: AsyncSession,
        order_no: str,
        is_success: bool,
        paid_amount: Optional[int] = None,
        third_trade_no: Optional[str] = None,
    ):
        order = await db.scalar(select(AurakeyOrder).where(AurakeyOrder.order_no == order_no))
        if not order or order.status != "waiting":
            return

        if is_success:
            if paid_amount is None:
                raise ValueError("微信支付回调缺少支付金额")
            if paid_amount != order.amount:
                logger.error(
                    "Wechat paid amount mismatch for order %s: paid=%s expected=%s",
                    order_no,
                    paid_amount,
                    order.amount,
                )
                raise ValueError("微信支付回调金额与订单金额不一致")

            product = await db.get(AurakeyProduct, order.product_id)
            if not product:
                logger.error("Order %s product %s not found", order_no, order.product_id)
                raise ValueError("订单商品不存在")
            if product.type not in {"point_pack", "vip"}:
                logger.error("Unknown AuraKey product type for order %s: %s", order_no, product.type)
                raise ValueError("未知的商品类型")

            asset = await AurakeyService.get_or_create_user_asset(db, order.user_id)
            now_utc = AurakeyService._now_utc()
            config = await AurakeyService.get_system_config(db)
            valid_days = AurakeyService._resolve_product_valid_days(product, config=config)
            entitlement_start_at = now_utc
            entitlement_expire_at = now_utc + timedelta(days=valid_days) if valid_days is not None else None
            total_add = product.point_amount + product.bonus_amount
            vip_type = AurakeyService._resolve_product_vip_type(product) if product.type == "vip" else None

            order.status = "success"
            order.paid_at = now_utc
            order.third_trade_no = third_trade_no
            order.entitlement_start_at = entitlement_start_at
            order.entitlement_expire_at = entitlement_expire_at
            order.product_name = product.name
            order.product_type = product.type
            order.vip_type = vip_type
            order.vip_level = product.vip_level or 0
            order.point_amount = product.point_amount or 0
            order.bonus_amount = product.bonus_amount or 0
            order.valid_days = valid_days
            order.granted_points = total_add

            if product.type == "point_pack":
                await AurakeyService._credit_points(
                    db,
                    asset,
                    total_add,
                    description=f"充值购买 {product.name}",
                    source_type="order",
                    source_id=order.id,
                    expires_at=entitlement_expire_at,
                )
                log = AurakeyAssetLog(
                    user_id=order.user_id, type=1, amount=total_add,
                    balance_after=asset.balance, description=f"充值购买 {product.name}"
                )
                db.add(log)
            elif product.type == "vip":
                asset.is_vip = True
                asset.vip_type = vip_type
                base = asset.vip_expire_time if asset.vip_expire_time and asset.vip_expire_time > now_utc else now_utc
                vip_days = valid_days if valid_days is not None else config.get("default_vip_valid_days", 30)
                asset.vip_expire_time = base + timedelta(days=vip_days)
                order.entitlement_start_at = now_utc
                order.entitlement_expire_at = asset.vip_expire_time
                point_expires_at = now_utc + timedelta(days=vip_days)
                if total_add > 0:
                    await AurakeyService._credit_points(
                        db,
                        asset,
                        total_add,
                        description=f"购买会员 {product.name} 赠送算力",
                        source_type="order",
                        source_id=order.id,
                        expires_at=point_expires_at,
                    )
                    db.add(
                        AurakeyAssetLog(
                            user_id=order.user_id,
                            type=1,
                            amount=total_add,
                            balance_after=asset.balance,
                            description=f"购买会员 {product.name} 赠送算力",
                        )
                    )
                else:
                    db.add(
                        AurakeyAssetLog(
                            user_id=order.user_id,
                            type=1,
                            amount=0,
                            balance_after=asset.balance,
                            description=f"购买会员 {product.name}",
                        )
                    )
            await db.commit()
        else:
            order.status = "failed"
            await db.commit()

    @staticmethod
    async def get_user_entitlement(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
        asset = await AurakeyService.get_or_create_user_asset(db, user_id)
        state = await AurakeyService.refresh_asset_state(db, asset)
        await db.commit()
        return {
            "vip_expire_time": AurakeyService._to_ts(state["vip_expire_time"]),
            "remaining_points": asset.balance,
            "is_vip": state["is_vip"],
            "vip_type": state["vip_type"],
            "vip_level": state["vip_level"],
        }

    @staticmethod
    def _order_to_purchase_item(order: AurakeyOrder, remaining_points: int = 0) -> dict[str, Any]:
        now_utc = AurakeyService._now_utc()
        expire_at = order.entitlement_expire_at
        is_effective = order.status == "success" and (expire_at is None or expire_at > now_utc)
        return {
            "order_no": order.order_no,
            "status": order.status,
            "amount": order.amount,
            "pay_method": order.pay_method,
            "product_id": order.product_id,
            "product_name": order.product_name or "",
            "product_type": order.product_type or "",
            "point_amount": order.point_amount or 0,
            "bonus_amount": order.bonus_amount or 0,
            "granted_points": order.granted_points or 0,
            "remaining_points": remaining_points,
            "vip_type": order.vip_type,
            "vip_level": order.vip_level or 0,
            "valid_days": order.valid_days,
            "entitlement_start_at": AurakeyService._to_ts(order.entitlement_start_at),
            "entitlement_expire_at": AurakeyService._to_ts(order.entitlement_expire_at),
            "created_at": AurakeyService._to_ts(order.created_at) or 0,
            "paid_at": AurakeyService._to_ts(order.paid_at),
            "is_effective": is_effective,
        }

    @staticmethod
    async def get_purchase_orders(
        db: AsyncSession,
        user_id: uuid.UUID,
        page: int,
        page_size: int,
    ) -> tuple[int, list[dict[str, Any]]]:
        base_conditions = [AurakeyOrder.user_id == user_id, AurakeyOrder.is_deleted == False]
        total = await db.scalar(select(func.count()).select_from(AurakeyOrder).where(*base_conditions))
        stmt = (
            select(AurakeyOrder)
            .where(*base_conditions)
            .order_by(desc(AurakeyOrder.created_at))
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        orders = (await db.execute(stmt)).scalars().all()
        remaining_by_order_id: dict[uuid.UUID, int] = {}
        if orders:
            order_ids = [order.id for order in orders]
            rows = await db.execute(
                select(AurakeyPointGrant.source_id, func.sum(AurakeyPointGrant.remaining_amount))
                .where(
                    AurakeyPointGrant.source_type == "order",
                    AurakeyPointGrant.source_id.in_(order_ids),
                    AurakeyPointGrant.is_deleted == False,
                )
                .group_by(AurakeyPointGrant.source_id)
            )
            remaining_by_order_id = {row[0]: int(row[1] or 0) for row in rows}
        return total or 0, [
            AurakeyService._order_to_purchase_item(order, remaining_by_order_id.get(order.id, 0))
            for order in orders
        ]

    @staticmethod
    async def daily_sign_in(db: AsyncSession, user_id: uuid.UUID) -> dict:
        """每日签到，返回本次奖励和连续签到天数"""
        now_utc = AurakeyService._now_utc()
        config = await AurakeyService.get_system_config(db)
        reset_hour = int(config.get("daily_free_points_reset_hour", 12) or 12)
        local_now = now_utc.astimezone(LOCAL_TZ)
        reset_start_local = local_now.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
        if local_now < reset_start_local:
            reset_start_local -= timedelta(days=1)
        today_start = reset_start_local.astimezone(timezone.utc)
        today_end = today_start + timedelta(days=1)
        today_utc = local_now.date()

        # 检查今日是否已签到（type=4）
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
        reward = int(config.get("daily_sign_in_reward_points", 10) or 0)
        expires_at = AurakeyService._next_reset_at(now_utc, reset_hour)
        await AurakeyService._credit_points(
            db,
            asset,
            reward,
            description="每日签到奖励",
            source_type="sign_in",
            expires_at=expires_at,
        )

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
        sign_dates = {
            (log_dt.astimezone(LOCAL_TZ) if log_dt.tzinfo else log_dt.replace(tzinfo=timezone.utc).astimezone(LOCAL_TZ)).date()
            for log_dt in sign_logs
        }
        
        while check_date in sign_dates:
            continuous_days += 1
            check_date -= timedelta(days=1)

        return {"reward_points": reward, "continuous_days": continuous_days}
