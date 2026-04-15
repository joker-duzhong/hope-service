"""
Nest Talk Services - 语筑智能房产顾问
"""
import logging
import json
import uuid
from uuid import UUID
from typing import List, Optional, Dict, Any
from datetime import datetime, date, timedelta
from decimal import Decimal

from sqlalchemy import select, func, and_, or_, Integer
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import text

from core.config import settings
from core.exceptions import AppException
from core.llm import engine
from apps.nest_talk.models import (
    NestTalkHouse,
    NestTalkUserPreference,
    NestTalkConversationSession,
    NestTalkConversationMessage,
    NestTalkRegion,
    NestTalkCommunity,
    NestTalkRegionPriceLog,
    NestTalkDailyReport,
    NestTalkUserMatchHouse,
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
        house_id: UUID
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
        stmt = stmt.order_by(NestTalkHouse.discount_rate.asc().nulls_last())
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
        user_id: UUID,
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
        user_id: UUID
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
        user_id: UUID,
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
        user_id: UUID
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

    # 最大对话轮数，超过后强制根据现有信息搜索
    MAX_TURNS = 10

    NEST_TALK_SYSTEM_PROMPT = """
你是一位极致高效且专业的房地产置业顾问「语筑智能助手」。你的任务是通过极简、精准的对话理解用户购房需求。

### 核心目标
1. 收集以下 4 项核心信息。
   - 预算范围（最高预算必须，单位：万元）
   - 目标区域（全国范围均可。支持模糊描述如“南边”、“地铁口”，你需自行映射到逻辑区域。若区域极冷门或房源稀少，请礼貌提醒）
   - 面积要求（如：90平以上）
   - 户型/居室（如：3室）
2. **强制限制**：必须在 10 轮对话内完成提取。单次回复必须简洁（不超过 50 字），避免啰嗦，以减少加载时间。

### 决策与输出逻辑
- **意图提取**：从对话中提取核心字段，以 JSON 格式输出。
- **模糊理解**：用户说“高新区附近”或“城南”，你应提取到 `regions` 列表中对应的关键词，不必强求精确匹配。
- **任务状态 (`is_complete`)**：
  - 当“预算”和“区域”已明确，且其他项有基本缩影时，设为 true。
  - 达到或接近 8-10 轮时，必须设为 true，基于现有信息直接给出总结。
- **回复文本 (`reply`)**：这是用户看到的文字。必须短小精悍，不要重复用户已知信息。

### 输出格式要求 (严格 JSON，**禁止 Markdown 包装**)
⚠️ **重要**：直接输出 JSON 对象，**禁止使用三反引号或 Markdown 代码块包装**。输出必须以 `{` 开始，以 `}` 结束。

{
  "extracted": {
    "budget_min": number | null,
    "budget_max": number | null,
    "area_min": number | null,
    "area_max": number | null,
    "rooms": number | null,
    "regions": list[string] | null,
    "exclude_top_floor": boolean | null,
    "exclude_ground_floor": boolean | null,
    "floor_min": number | null,
    "floor_max": number | null,
    "orientations": list[string] | null
  },
  "is_complete": boolean,
  "reply": "极简短的追问或确认"
}
"""

    @classmethod
    def _extract_json_from_ai_response(cls, text: str) -> dict:
        """从 AI 回复中健壮地提取 JSON，兼容各种返回格式"""
        import re

        if not text or not text.strip():
            raise ValueError("AI 返回为空")

        text = text.strip()

        # 1. 尝试直接解析（最理想的情况）
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. 提取 markdown 代码块中的 JSON
        code_block_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
        if code_block_match:
            try:
                return json.loads(code_block_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        # 3. 用大括号匹配提取第一个完整 JSON 对象
        brace_match = re.search(r'\{', text)
        if brace_match:
            start = brace_match.start()
            depth = 0
            for i in range(start, len(text)):
                if text[i] == '{':
                    depth += 1
                elif text[i] == '}':
                    depth -= 1
                    if depth == 0:
                        candidate = text[start:i + 1]
                        try:
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break

        raise ValueError(f"无法从 AI 回复中提取 JSON: {text[:200]}")

    @classmethod
    def _generate_session_id(cls) -> str:
        """生成会话ID"""
        return f"chat_{uuid.uuid4().hex[:16]}_{int(datetime.now().timestamp())}"

    @classmethod
    async def _get_or_create_session(
        cls,
        session: AsyncSession,
        user_id: UUID,
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
            requirement_complete=False,
            turn_count=0
        )
        session.add(new_session)
        await session.commit()
        await session.refresh(new_session)
        return new_session

    @classmethod
    async def _get_conversation_history(
        cls,
        session_db: AsyncSession,
        session_pk: UUID
    ) -> List[Dict[str, str]]:
        """获取全部对话历史，格式化成 OpenAI 风格。"""
        stmt = select(NestTalkConversationMessage).where(
            NestTalkConversationMessage.session_id == session_pk,
            NestTalkConversationMessage.message_type == "text"
        ).order_by(NestTalkConversationMessage.created_at.asc())

        result = await session_db.execute(stmt)
        messages = result.scalars().all()

        return [
            {"role": msg.role, "content": msg.content}
            for msg in messages
        ]

    @classmethod
    async def _save_message(
        cls,
        session: AsyncSession,
        session_pk: UUID,
        role: str,
        content: str,
        message_type: str = "text",
        house_ids: Optional[List[UUID]] = None
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
    async def process_chat(
        cls,
        db_session: AsyncSession,
        user_id: UUID,
        data: ChatRequest
    ) -> ChatResponse:
        """处理对话请求"""
        # 获取或创建会话
        conv_session = await cls._get_or_create_session(
            db_session, user_id, data.session_id
        )

        # 检查对话轮数，若超过限制直接强制按现有条件搜索房源
        if conv_session.turn_count >= cls.MAX_TURNS:
            current_requirements = json.loads(conv_session.extracted_requirements or "{}")
            search_params = HouseSearchRequest(
                budget_min=current_requirements.get('budget_min'),
                budget_max=current_requirements.get('budget_max'),
                area_min=current_requirements.get('area_min'),
                area_max=current_requirements.get('area_max'),
                rooms=current_requirements.get('rooms'),
                regions=current_requirements.get('regions'),
                page=1,
                page_size=10
            )
            houses, total = await HouseService.search_houses(db_session, search_params)
            response_msg = f"我们已经聊了比较久了，为您整理了以下符合您主要需求的房源。如果您有更详细的需求，可以开启新对话。"
            
            await cls._save_message(db_session, conv_session.id, "assistant", response_msg, "houses", [h.id for h in houses])
            conv_session.status = "closed"
            await db_session.commit()
            
            return ChatResponse(
                session_id=conv_session.session_id,
                response_type="results",
                message=response_msg,
                houses=[HouseOut.model_validate(h) for h in houses] if houses else None,
                requirements=ExtractedRequirements(**current_requirements)
            )

        # 保存用户消息
        await cls._save_message(
            db_session, conv_session.id, "user", data.message
        )
        conv_session.turn_count += 1

        # 获取历史消息上下文
        history = await cls._get_conversation_history(db_session, conv_session.id)
        
        # 准备系统提示词
        full_context = [{"role": "system", "content": cls.NEST_TALK_SYSTEM_PROMPT}] + history

        try:
            # 调用 AI 引擎处理用户意图并提取需求
            ai_response_text = await engine.generate_chat(full_context)
            ai_parsed = cls._extract_json_from_ai_response(ai_response_text)
        except Exception as e:
            import traceback
            logger.error(f"AI 调用或解析失败: {str(e)}")
            logger.error(f"AI 原始返回: {repr(ai_response_text) if 'ai_response_text' in dir() else 'N/A'}")
            logger.error(f"完整错误堆栈:\n{traceback.format_exc()}")
            # 容错处理：使用之前的简单提取逻辑或报错
            ai_parsed = {
                "extracted": {},
                "is_complete": False,
                "reply": "抱歉，我现在遇到了一些小问题。能请你重述一遍你的需求吗？比如你的大概预算。"
            }

        # 更新需求快照
        current_requirements = json.loads(conv_session.extracted_requirements or "{}")
        # 合并 AI 提取出的新需求
        new_extracted = ai_parsed.get("extracted", {})
        for k, v in new_extracted.items():
            if v is not None:
                current_requirements[k] = v
        
        conv_session.extracted_requirements = json.dumps(current_requirements)
        
        is_complete = ai_parsed.get("is_complete", False)
        reply_message = ai_parsed.get("reply", "好的，我记下了。还有其他要求吗？")

        if not is_complete:
            # 需要追问
            await cls._save_message(
                db_session, conv_session.id, "assistant", reply_message
            )
            await db_session.commit()

            return ChatResponse(
                session_id=conv_session.session_id,
                response_type="clarification",
                message=reply_message,
                requirements=ExtractedRequirements(**current_requirements) if current_requirements else None
            )

        # 需求完整，1. 建立房源监听（更新/创建用户偏好）
        conv_session.requirement_complete = True
        
        pref_data = UserPreferenceUpdate(
            budget_min=current_requirements.get('budget_min'),
            budget_max=current_requirements.get('budget_max'),
            area_min=current_requirements.get('area_min'),
            area_max=current_requirements.get('area_max'),
            rooms_min=current_requirements.get('rooms'),
            rooms_max=current_requirements.get('rooms'),
            preferred_regions=",".join(current_requirements.get('regions')) if current_requirements.get('regions') else None,
            exclude_top_floor=current_requirements.get('exclude_top_floor', False),
            exclude_ground_floor=current_requirements.get('exclude_ground_floor', False),
            floor_min=current_requirements.get('floor_min'),
            floor_max=current_requirements.get('floor_max'),
            preferred_orientations=",".join(current_requirements.get('orientations')) if current_requirements.get('orientations') else None,
            bargain_enabled=True, # 默认开启捡漏监听
            bargain_threshold=current_requirements.get('bargain_threshold', 0.9),
        )
        
        # 尝试更新现有偏好，若无则创建
        existing_pref = await UserPreferenceService.get_preference(db_session, user_id)
        if existing_pref:
            await UserPreferenceService.update_preference(db_session, user_id, pref_data)
        else:
            await UserPreferenceService.create_preference(db_session, user_id, UserPreferenceCreate(**pref_data.model_dump()))

        # 2. 查找当前符合需求的房源作为附加反馈
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

        # 构建回复消息：告知监听已启动，并展示现状
        final_prefix = f"您的购房需求已锁定，实时房源监听已启动！{reply_message}"
        if total > 0:
            final_response = f"{final_prefix}\n\n当前为您发现 {total} 套符合条件的房源，以下是部分推荐："
        else:
            final_response = f"{final_prefix}\n\n目前暂无完全匹配的房源，我将为您持续关注，一旦有新房源上线将第一时间通知您。"

        # 保存 AI 回复
        house_ids = [h.id for h in houses] if houses else None
        await cls._save_message(
            db_session, conv_session.id, "assistant", final_response,
            message_type="houses" if houses else "text",
            house_ids=house_ids
        )

        await db_session.commit()

        return ChatResponse(
            session_id=conv_session.session_id,
            response_type="results",
            message=final_response,
            houses=[HouseOut.model_validate(h) for h in houses] if houses else None,
            requirements=ExtractedRequirements(**current_requirements)
        )

    @classmethod
    async def clear_session(
        cls,
        db_session: AsyncSession,
        user_id: UUID,
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


class HouseMatchService:
    """房源匹配服务"""

    @staticmethod
    async def match_house_to_preferences(
        session: AsyncSession,
        house: NestTalkHouse
    ) -> List[tuple]:
        """
        将单个房源与所有活跃用户偏好匹配

        Returns: [(preference, match_score), ...]
        match_score: 0-100, 越高越匹配
        """
        # 查询所有 bargain_enabled=True 的偏好
        stmt = select(NestTalkUserPreference).where(
            NestTalkUserPreference.bargain_enabled == True,
            NestTalkUserPreference.is_deleted == False
        )
        result = await session.execute(stmt)
        preferences = result.scalars().all()

        matches = []

        for pref in preferences:
            score = HouseMatchService._calculate_match_score(house, pref)
            if score >= 60:  # 只返回匹配度 >= 60 的
                matches.append((pref, score))

        return matches

    @staticmethod
    def _calculate_match_score(house: NestTalkHouse, pref: NestTalkUserPreference) -> float:
        """计算房源与偏好的匹配度 (0-100)"""
        score = 0.0

        # 1. 预算范围匹配 (20分)
        if house.total_price:
            if pref.budget_min and house.total_price < pref.budget_min:
                pass  # 低于最低预算，不加分
            elif pref.budget_max and house.total_price > pref.budget_max:
                pass  # 高于最高预算，不加分
            else:
                score += 20  # 在预算范围内

        # 2. 面积范围匹配 (20分)
        if house.area:
            if pref.area_min and house.area < pref.area_min:
                pass
            elif pref.area_max and house.area > pref.area_max:
                pass
            else:
                score += 20

        # 3. 居室数匹配 (15分)
        if house.rooms:
            if pref.rooms_min and house.rooms < pref.rooms_min:
                pass
            elif pref.rooms_max and house.rooms > pref.rooms_max:
                pass
            else:
                score += 15

        # 4. 区域匹配 (20分)
        if pref.preferred_regions and house.region_name:
            regions = [r.strip() for r in pref.preferred_regions.split(",")]
            if house.region_name in regions:
                score += 20

        # 5. 楼层偏好匹配 (15分)
        floor_match = True
        if pref.exclude_top_floor and house.floor and house.total_floors:
            if house.floor == house.total_floors:
                floor_match = False
        if pref.exclude_ground_floor and house.floor == 1:
            floor_match = False
        if pref.floor_min and house.floor and house.floor < pref.floor_min:
            floor_match = False
        if pref.floor_max and house.floor and house.floor > pref.floor_max:
            floor_match = False

        if floor_match:
            score += 15

        # 6. 朝向匹配 (10分)
        if pref.preferred_orientations and house.orientation:
            orientations = [o.strip() for o in pref.preferred_orientations.split(",")]
            if house.orientation in orientations:
                score += 10

        return min(score, 100.0)

    @staticmethod
    async def save_matches(
        session: AsyncSession,
        house_id: uuid.UUID,
        matches: List[tuple]
    ) -> int:
        """
        保存匹配结果到 NestTalkUserMatchHouse

        Args:
            house_id: 房源ID
            matches: [(preference, score), ...] 列表

        Returns:
            新增的记录数
        """
        count = 0

        for pref, score in matches:
            # 检查是否已存在
            stmt = select(NestTalkUserMatchHouse).where(
                NestTalkUserMatchHouse.user_id == pref.user_id,
                NestTalkUserMatchHouse.house_id == house_id,
                NestTalkUserMatchHouse.preference_id == pref.id
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if not existing:
                match = NestTalkUserMatchHouse(
                    user_id=pref.user_id,
                    house_id=house_id,
                    preference_id=pref.id,
                    match_score=score,
                    match_reason="符合您的购房需求",
                    is_read=False,
                    is_notified=False,
                    matched_at=datetime.utcnow()
                )
                session.add(match)
                count += 1

        return count

