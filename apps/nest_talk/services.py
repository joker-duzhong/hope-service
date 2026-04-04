"""
Nest Talk Services - 语筑智能房产顾问
"""
import logging
import json
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from decimal import Decimal

from sqlalchemy import select, func, and_, or_, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from core.config import settings
from core.exceptions import AppException
from apps.nest_talk.models import (
    NestTalkHouse,
    NestTalkUserPreference,
    NestTalkConversationSession,
    NestTalkConversationMessage,
    NestTalkRegion,
    NestTalkCommunity,
    NestTalkRegionPriceLog,
    NestTalkDailyReport,
)
from apps.nest_talk.schemas import (
    HouseSearchRequest,
    HouseOut,
    HouseDetailOut,
    BargainHouseOut,
    UserPreferenceCreate,
    UserPreferenceUpdate,
    UserPreferenceOut,
    ChatRequest,
    ChatResponse,
    ExtractedRequirements,
    DailyReportOut,
    HouseStatistics,
    PriceDistribution,
)

logger = logging.getLogger(__name__)


class HouseService:
    """房源管理服务"""

    @classmethod
    async def search_houses(
        cls,
        session: AsyncSession,
        params: HouseSearchRequest
    ) -> tuple[List[NestTalkHouse], int]:
        """
        多条件搜索房源
        返回: (房源列表, 总数)
        """
        # 构建基础查询
        stmt = select(NestTalkHouse).where(
            NestTalkHouse.is_deleted == False,
            NestTalkHouse.status == "active"
        )

        # 预算筛选
        if params.budget_min is not None:
            stmt = stmt.where(NestTalkHouse.total_price >= params.budget_min)
        if params.budget_max is not None:
            stmt = stmt.where(NestTalkHouse.total_price <= params.budget_max)

        # 面积筛选
        if params.area_min is not None:
            stmt = stmt.where(NestTalkHouse.area >= params.area_min)
        if params.area_max is not None:
            stmt = stmt.where(NestTalkHouse.area <= params.area_max)

        # 居室筛选
        if params.rooms is not None:
            stmt = stmt.where(NestTalkHouse.rooms == params.rooms)

        # 区域筛选
        if params.regions and len(params.regions) > 0:
            stmt = stmt.where(NestTalkHouse.region_name.in_(params.regions))

        # 楼层筛选
        if params.floor_min is not None:
            stmt = stmt.where(NestTalkHouse.floor >= params.floor_min)
        if params.floor_max is not None:
            stmt = stmt.where(NestTalkHouse.floor <= params.floor_max)

        # 排除顶楼
        if params.exclude_top_floor:
            stmt = stmt.where(
                or_(
                    NestTalkHouse.floor == None,
                    NestTalkHouse.floor < NestTalkHouse.total_floors
                )
            )

        # 排除底楼
        if params.exclude_ground_floor:
            stmt = stmt.where(
                or_(
                    NestTalkHouse.floor == None,
                    NestTalkHouse.floor > 1
                )
            )

        # 朝向筛选
        if params.orientations and len(params.orientations) > 0:
            orientation_conditions = [
                NestTalkHouse.orientation.ilike(f"%{o}%")
                for o in params.orientations
            ]
            stmt = stmt.where(or_(*orientation_conditions))

        # 统计总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await session.execute(count_stmt)
        total = total_result.scalar()

        # 分页
        offset = (params.page - 1) * params.page_size
        stmt = stmt.order_by(NestTalkHouse.created_at.desc())
        stmt = stmt.offset(offset).limit(params.page_size)

        result = await session.execute(stmt)
        houses = list(result.scalars().all())

        return houses, total

    @classmethod
    async def get_house_by_id(
        cls,
        session: AsyncSession,
        house_id: int
    ) -> Optional[NestTalkHouse]:
        """获取房源详情"""
        stmt = select(NestTalkHouse).where(
            NestTalkHouse.id == house_id,
            NestTalkHouse.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def get_bargain_houses(
        cls,
        session: AsyncSession,
        page: int = 1,
        page_size: int = 20
    ) -> tuple[List[NestTalkHouse], int]:
        """获取捡漏房源列表"""
        stmt = select(NestTalkHouse).where(
            NestTalkHouse.is_deleted == False,
            NestTalkHouse.status == "active",
            NestTalkHouse.is_bargain == True
        )

        # 统计总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await session.execute(count_stmt)
        total = total_result.scalar()

        # 分页
        offset = (page - 1) * page_size
        stmt = stmt.order_by(NestTalkHouse.discount_rate.asc())
        stmt = stmt.offset(offset).limit(page_size)

        result = await session.execute(stmt)
        houses = list(result.scalars().all())

        return houses, total

    @classmethod
    async def get_house_statistics(
        cls,
        session: AsyncSession,
        region: Optional[str] = None
    ) -> HouseStatistics:
        """获取房源统计"""
        stmt = select(
            func.count().label("total_count"),
            func.sum(func.cast(NestTalkHouse.is_bargain, Integer)).label("bargain_count"),
            func.avg(NestTalkHouse.unit_price).label("avg_price"),
            func.avg(NestTalkHouse.area).label("avg_area")
        ).where(
            NestTalkHouse.is_deleted == False,
            NestTalkHouse.status == "active"
        )

        if region:
            stmt = stmt.where(NestTalkHouse.region_name == region)

        result = await session.execute(stmt)
        row = result.first()

        return HouseStatistics(
            total_count=row.total_count or 0,
            bargain_count=int(row.bargain_count or 0),
            avg_price=float(row.avg_price or 0),
            avg_area=float(row.avg_area or 0)
        )

    @classmethod
    async def get_price_distribution(
        cls,
        session: AsyncSession,
        region: Optional[str] = None
    ) -> List[PriceDistribution]:
        """获取价格区间分布"""
        # 定义价格区间
        price_ranges = [
            (0, 100, "100万以下"),
            (100, 150, "100-150万"),
            (150, 200, "150-200万"),
            (200, 300, "200-300万"),
            (300, 500, "300-500万"),
            (500, 10000, "500万以上"),
        ]

        results = []
        for min_price, max_price, label in price_ranges:
            stmt = select(func.count()).where(
                NestTalkHouse.is_deleted == False,
                NestTalkHouse.status == "active",
                NestTalkHouse.total_price >= min_price,
                NestTalkHouse.total_price < max_price
            )
            if region:
                stmt = stmt.where(NestTalkHouse.region_name == region)

            count_result = await session.execute(stmt)
            count = count_result.scalar() or 0

            # 计算占比需要总数
            total_stmt = select(func.count()).where(
                NestTalkHouse.is_deleted == False,
                NestTalkHouse.status == "active"
            )
            if region:
                total_stmt = total_stmt.where(NestTalkHouse.region_name == region)

            total_result = await session.execute(total_stmt)
            total = total_result.scalar() or 1

            results.append(PriceDistribution(
                price_range=label,
                count=count,
                percentage=round(count / total * 100, 2) if total > 0 else 0
            ))

        return results


class UserPreferenceService:
    """用户偏好服务"""

    @classmethod
    async def create_preference(
        cls,
        session: AsyncSession,
        user_id: int,
        data: UserPreferenceCreate
    ) -> NestTalkUserPreference:
        """创建用户偏好"""
        # 检查是否已存在
        existing = await cls.get_preference(session, user_id)
        if existing:
            raise ValueError("用户偏好已存在，请使用更新接口")

        preference = NestTalkUserPreference(
            user_id=user_id,
            budget_min=data.budget_min,
            budget_max=data.budget_max,
            area_min=data.area_min,
            area_max=data.area_max,
            rooms_min=data.rooms_min,
            rooms_max=data.rooms_max,
            preferred_regions=data.preferred_regions,
            exclude_top_floor=data.exclude_top_floor,
            exclude_ground_floor=data.exclude_ground_floor,
            floor_min=data.floor_min,
            floor_max=data.floor_max,
            preferred_orientations=data.preferred_orientations,
            bargain_enabled=data.bargain_enabled,
            bargain_threshold=data.bargain_threshold,
            notify_endpoint=data.notify_endpoint,
        )
        session.add(preference)
        await session.commit()
        await session.refresh(preference)
        return preference

    @classmethod
    async def get_preference(
        cls,
        session: AsyncSession,
        user_id: int
    ) -> Optional[NestTalkUserPreference]:
        """获取用户偏好"""
        stmt = select(NestTalkUserPreference).where(
            NestTalkUserPreference.user_id == user_id,
            NestTalkUserPreference.is_deleted == False
        )
        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def update_preference(
        cls,
        session: AsyncSession,
        user_id: int,
        data: UserPreferenceUpdate
    ) -> Optional[NestTalkUserPreference]:
        """更新用户偏好"""
        preference = await cls.get_preference(session, user_id)
        if not preference:
            return None

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            if value is not None:
                setattr(preference, key, value)

        await session.commit()
        await session.refresh(preference)
        return preference

    @classmethod
    async def delete_preference(
        cls,
        session: AsyncSession,
        user_id: int
    ) -> bool:
        """删除用户偏好（软删除）"""
        preference = await cls.get_preference(session, user_id)
        if not preference:
            return False

        preference.is_deleted = True
        await session.commit()
        return True


class ChatService:
    """AI 智能对话服务"""

    @classmethod
    def _generate_session_id(cls) -> str:
        """生成会话ID"""
        return f"chat_{uuid.uuid4().hex[:16]}_{int(datetime.now().timestamp())}"

    @classmethod
    async def _get_or_create_session(
        cls,
        session: AsyncSession,
        user_id: int,
        session_id: Optional[str] = None
    ) -> NestTalkConversationSession:
        """获取或创建会话"""
        if session_id:
            stmt = select(NestTalkConversationSession).where(
                NestTalkConversationSession.session_id == session_id,
                NestTalkConversationSession.user_id == user_id,
                NestTalkConversationSession.status == "active"
            )
            result = await session.execute(stmt)
            existing = result.scalars().first()
            if existing:
                return existing

        # 创建新会话
        new_session = NestTalkConversationSession(
            user_id=user_id,
            session_id=cls._generate_session_id(),
            status="active",
            extracted_requirements="{}",
            requirement_complete=False
        )
        session.add(new_session)
        await session.commit()
        await session.refresh(new_session)
        return new_session

    @classmethod
    async def _get_conversation_history(
        cls,
        session: AsyncSession,
        session_pk: int
    ) -> List[Dict[str, str]]:
        """获取对话历史"""
        stmt = select(NestTalkConversationMessage).where(
            NestTalkConversationMessage.session_id == session_pk,
            NestTalkConversationMessage.is_deleted == False
        ).order_by(NestTalkConversationMessage.created_at.asc())

        result = await session.execute(stmt)
        messages = result.scalars().all()

        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

    @classmethod
    async def _save_message(
        cls,
        session: AsyncSession,
        session_pk: int,
        role: str,
        content: str,
        message_type: str = "text",
        house_ids: Optional[List[int]] = None
    ) -> NestTalkConversationMessage:
        """保存消息"""
        message = NestTalkConversationMessage(
            session_id=session_pk,
            role=role,
            content=content,
            message_type=message_type,
            house_ids=json.dumps(house_ids) if house_ids else None
        )
        session.add(message)
        await session.commit()
        await session.refresh(message)
        return message

    @classmethod
    def _extract_requirements_from_text(cls, text: str) -> Dict[str, Any]:
        """
        从用户输入中提取购房需求（简化版，实际应调用 AI 服务）
        """
        # 这里是一个简化的实现，实际应该调用 OpenAI 等 AI 服务
        requirements = {}

        text_lower = text.lower()

        # 预算提取
        import re
        budget_pattern = r'(\d+)\s*[万w]'
        budgets = re.findall(budget_pattern, text)
        if budgets:
            budgets = [int(b) for b in budgets]
            if len(budgets) >= 2:
                requirements['budget_min'] = min(budgets)
                requirements['budget_max'] = max(budgets)
            elif len(budgets) == 1:
                requirements['budget_max'] = budgets[0]

        # 面积提取
        area_pattern = r'(\d+)\s*[平㎡米]'
        areas = re.findall(area_pattern, text)
        if areas:
            areas = [int(a) for a in areas]
            if len(areas) >= 2:
                requirements['area_min'] = min(areas)
                requirements['area_max'] = max(areas)
            elif len(areas) == 1:
                requirements['area_max'] = max(areas)

        # 居室提取
        rooms_pattern = r'(\d)\s*[居室]'
        rooms = re.findall(rooms_pattern, text)
        if rooms:
            requirements['rooms'] = int(rooms[0])

        # 区域提取
        regions = []
        known_regions = ["高新区", "天府新区", "锦江区", "青羊区", "武侯区", "成华区", "金牛区", "双流区", "温江区", "郫都区", "龙泉驿区", "新都区"]
        for region in known_regions:
            if region in text:
                regions.append(region)
        if regions:
            requirements['regions'] = regions

        return requirements

    @classmethod
    def _check_requirements_complete(cls, requirements: Dict[str, Any]) -> tuple[bool, str]:
        """
        检查需求是否完整
        返回: (是否完整, 追问消息)
        """
        # 简化逻辑：至少需要预算和区域
        if not requirements.get('budget_max') and not requirements.get('budget_min'):
            return False, "请问您的预算大概是多少呢？比如200万以内。"
        if not requirements.get('regions'):
            return False, "请问您希望在哪些区域购房呢？比如高新区、天府新区等。"

        return True, ""

    @classmethod
    async def process_chat(
        cls,
        db_session: AsyncSession,
        user_id: int,
        data: ChatRequest
    ) -> ChatResponse:
        """处理对话请求"""
        # 获取或创建会话
        conv_session = await cls._get_or_create_session(
            db_session, user_id, data.session_id
        )

        # 保存用户消息
        await cls._save_message(
            db_session, conv_session.id, "user", data.message
        )

        # 获取当前已提取的需求
        current_requirements = json.loads(conv_session.extracted_requirements or "{}")

        # 提取新需求
        new_requirements = cls._extract_requirements_from_text(data.message)

        # 合并需求
        current_requirements.update(new_requirements)

        # 更新会话中的需求
        conv_session.extracted_requirements = json.dumps(current_requirements)

        # 检查需求是否完整
        is_complete, clarification_msg = cls._check_requirements_complete(current_requirements)

        if not is_complete:
            # 需要追问
            await cls._save_message(
                db_session, conv_session.id, "assistant", clarification_msg
            )
            await db_session.commit()

            return ChatResponse(
                session_id=conv_session.session_id,
                response_type="clarification",
                message=clarification_msg,
                requirements=ExtractedRequirements(**current_requirements) if current_requirements else None
            )

        # 需求完整，搜索房源
        conv_session.requirement_complete = True

        search_params = HouseSearchRequest(
            budget_min=current_requirements.get('budget_min'),
            budget_max=current_requirements.get('budget_max'),
            area_min=current_requirements.get('area_min'),
            area_max=current_requirements.get('area_max'),
            rooms=current_requirements.get('rooms'),
            regions=current_requirements.get('regions'),
            floor_min=current_requirements.get('floor_min'),
            floor_max=current_requirements.get('floor_max'),
            exclude_top_floor=current_requirements.get('exclude_top_floor'),
            exclude_ground_floor=current_requirements.get('exclude_ground_floor'),
            orientations=current_requirements.get('orientations'),
            page=1,
            page_size=10
        )

        houses, total = await HouseService.search_houses(db_session, search_params)

        # 构建回复消息
        if total > 0:
            response_msg = f"为您找到 {total} 套符合条件的房源，以下是部分推荐："
        else:
            response_msg = "抱歉，没有找到完全符合您需求的房源。您可以尝试调整一下条件。"

        # 保存 AI 回复
        house_ids = [h.id for h in houses] if houses else None
        await cls._save_message(
            db_session, conv_session.id, "assistant", response_msg,
            message_type="houses" if houses else "text",
            house_ids=house_ids
        )

        await db_session.commit()

        return ChatResponse(
            session_id=conv_session.session_id,
            response_type="results",
            message=response_msg,
            houses=[HouseOut.model_validate(h) for h in houses] if houses else None,
            requirements=ExtractedRequirements(**current_requirements)
        )

    @classmethod
    async def clear_session(
        cls,
        db_session: AsyncSession,
        user_id: int,
        session_id: str
    ) -> bool:
        """清除会话"""
        stmt = select(NestTalkConversationSession).where(
            NestTalkConversationSession.session_id == session_id,
            NestTalkConversationSession.user_id == user_id
        )
        result = await db_session.execute(stmt)
        conv_session = result.scalars().first()

        if not conv_session:
            return False

        conv_session.status = "closed"
        await db_session.commit()
        return True


class ReportService:
    """报表服务"""

    @classmethod
    async def get_daily_report(
        cls,
        session: AsyncSession,
        region: Optional[str] = None,
        report_date: Optional[date] = None
    ) -> Optional[NestTalkDailyReport]:
        """获取每日行情报表"""
        if not report_date:
            report_date = date.today()

        stmt = select(NestTalkDailyReport).where(
            NestTalkDailyReport.is_deleted == False,
            NestTalkDailyReport.report_date == report_date
        )

        if region:
            stmt = stmt.where(NestTalkDailyReport.region_name == region)
        else:
            stmt = stmt.where(NestTalkDailyReport.region_name == None)

        stmt = stmt.order_by(NestTalkDailyReport.created_at.desc()).limit(1)

        result = await session.execute(stmt)
        return result.scalars().first()

    @classmethod
    async def list_reports(
        cls,
        session: AsyncSession,
        region: Optional[str] = None,
        days: int = 7
    ) -> List[NestTalkDailyReport]:
        """获取报表列表"""
        start_date = date.today() - timedelta(days=days)

        stmt = select(NestTalkDailyReport).where(
            NestTalkDailyReport.is_deleted == False,
            NestTalkDailyReport.report_date >= start_date
        )

        if region:
            stmt = stmt.where(NestTalkDailyReport.region_name == region)

        stmt = stmt.order_by(NestTalkDailyReport.report_date.desc())

        result = await session.execute(stmt)
        return list(result.scalars().all())


class RegionService:
    """区域服务"""

    @classmethod
    async def list_regions(
        cls,
        session: AsyncSession,
        active_only: bool = True
    ) -> List[NestTalkRegion]:
        """获取区域列表"""
        stmt = select(NestTalkRegion).where(
            NestTalkRegion.is_deleted == False
        )

        if active_only:
            stmt = stmt.where(NestTalkRegion.is_active == True)

        stmt = stmt.order_by(NestTalkRegion.name)

        result = await session.execute(stmt)
        return list(result.scalars().all())

    @classmethod
    async def get_region_price_history(
        cls,
        session: AsyncSession,
        region_name: str,
        days: int = 30
    ) -> List[NestTalkRegionPriceLog]:
        """获取区域均价历史"""
        start_date = date.today() - timedelta(days=days)

        stmt = select(NestTalkRegionPriceLog).join(NestTalkRegion).where(
            NestTalkRegionPriceLog.is_deleted == False,
            NestTalkRegion.name == region_name,
            NestTalkRegionPriceLog.record_date >= start_date
        ).order_by(NestTalkRegionPriceLog.record_date.asc())

        result = await session.execute(stmt)
        return list(result.scalars().all())
