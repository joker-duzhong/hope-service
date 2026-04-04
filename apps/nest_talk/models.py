"""
Nest Talk Models - 语筑智能房产顾问
表名前缀: nest_talk_
"""
from typing import Optional, List
from datetime import date, datetime
from sqlalchemy import String, Float, Integer, Date, DateTime, Text, Boolean, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import CoreModel


class NestTalkRegion(CoreModel):
    """区域信息表"""
    __tablename__ = "nest_talk_regions"

    name: Mapped[str] = mapped_column(String(50), unique=True, index=True, comment="区域名称")
    code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="区域编码")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用抓取")


class NestTalkCommunity(CoreModel):
    """小区信息表"""
    __tablename__ = "nest_talk_communities"

    name: Mapped[str] = mapped_column(String(200), index=True, comment="小区名称")
    region_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("nest_talk_regions.id", ondelete="SET NULL"), nullable=True, comment="所属区域ID"
    )
    region_name: Mapped[str] = mapped_column(String(50), index=True, comment="区域名称(冗余)")
    average_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="小区均价(元/㎡)")
    address: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="小区地址")
    source_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="来源平台ID")

    region = relationship("NestTalkRegion", backref="communities")


class NestTalkHouse(CoreModel):
    """房源信息表"""
    __tablename__ = "nest_talk_houses"

    house_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="房源唯一ID(来源平台)")
    title: Mapped[str] = mapped_column(String(500), comment="房源标题")
    total_price: Mapped[float] = mapped_column(Float, comment="总价(万元)")
    unit_price: Mapped[float] = mapped_column(Float, comment="单价(元/㎡)")
    area: Mapped[float] = mapped_column(Float, comment="建筑面积(㎡)")
    layout: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="户型(如: 3室2厅)")
    rooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="居室数")
    floor: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="所在楼层")
    total_floors: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="总楼层")
    orientation: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="朝向")
    decoration: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="装修情况")

    region_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("nest_talk_regions.id", ondelete="SET NULL"), nullable=True, comment="所属区域ID"
    )
    region_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True, comment="区域名称(冗余)")

    community_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("nest_talk_communities.id", ondelete="SET NULL"), nullable=True, comment="所属小区ID"
    )
    community_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="小区名称(冗余)")

    source: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default="beike", comment="来源平台")
    url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="房源链接")
    image_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="房源图片")

    # 捡漏相关
    is_bargain: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否捡漏房")
    bargain_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="捡漏原因")
    discount_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="折扣率(如0.9表示90%)")
    community_avg_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="小区均价(用于捡漏计算)")

    # 状态
    status: Mapped[str] = mapped_column(String(20), default="active", index=True, comment="房源状态: active(在售), sold(已售)")

    region = relationship("NestTalkRegion", backref="houses")
    community = relationship("NestTalkCommunity", backref="houses")


class NestTalkUserPreference(CoreModel):
    """用户购房偏好表"""
    __tablename__ = "nest_talk_user_preferences"

    user_id: Mapped[int] = mapped_column(Integer, index=True, unique=True, comment="所属用户ID")

    # 预算
    budget_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最低预算(万元)")
    budget_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最高预算(万元)")

    # 面积
    area_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最小面积(㎡)")
    area_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="最大面积(㎡)")

    # 居室
    rooms_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="最少居室数")
    rooms_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="最多居室数")

    # 区域偏好
    preferred_regions: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="偏好区域(逗号分隔)")

    # 楼层偏好
    exclude_top_floor: Mapped[bool] = mapped_column(Boolean, default=False, comment="排除顶楼")
    exclude_ground_floor: Mapped[bool] = mapped_column(Boolean, default=False, comment="排除底楼")
    floor_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="最低楼层")
    floor_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="最高楼层")

    # 朝向偏好
    preferred_orientations: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="偏好朝向(逗号分隔)")

    # 捡漏设置
    bargain_enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="启用捡漏推送")
    bargain_threshold: Mapped[float] = mapped_column(Float, default=0.9, comment="捡漏折扣阈值(如0.9表示90%)")

    # 通知设置
    notify_endpoint: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="推送接收地址")


class NestTalkConversationSession(CoreModel):
    """对话会话表"""
    __tablename__ = "nest_talk_conversation_sessions"

    user_id: Mapped[int] = mapped_column(Integer, index=True, comment="所属用户ID")
    session_id: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="会话ID")
    status: Mapped[str] = mapped_column(String(20), default="active", comment="会话状态: active(活跃), closed(已关闭)")

    # 当前提取的需求快照
    extracted_requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="已提取的需求(JSON)")
    requirement_complete: Mapped[bool] = mapped_column(Boolean, default=False, comment="需求是否已完整")


class NestTalkConversationMessage(CoreModel):
    """对话消息表"""
    __tablename__ = "nest_talk_conversation_messages"

    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nest_talk_conversation_sessions.id", ondelete="CASCADE"), index=True, comment="会话ID"
    )
    role: Mapped[str] = mapped_column(String(20), comment="角色: user(用户) / assistant(AI)")
    content: Mapped[str] = mapped_column(Text, comment="消息内容")
    message_type: Mapped[str] = mapped_column(String(20), default="text", comment="消息类型: text(文本), houses(房源列表)")

    # 如果是房源推荐消息，存储房源ID列表
    house_ids: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="推荐的房源ID列表(JSON)")


class NestTalkRegionPriceLog(CoreModel):
    """区域均价历史记录表"""
    __tablename__ = "nest_talk_region_price_logs"

    region_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("nest_talk_regions.id", ondelete="CASCADE"), index=True, comment="区域ID"
    )
    region_name: Mapped[str] = mapped_column(String(50), comment="区域名称(冗余)")
    record_date: Mapped[date] = mapped_column(Date, index=True, comment="记录日期")
    average_price: Mapped[float] = mapped_column(Float, comment="区域均价(元/㎡)")
    change_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="环比涨跌幅")

    region = relationship("NestTalkRegion", backref="price_logs")


class NestTalkDailyReport(CoreModel):
    """每日行情报表表"""
    __tablename__ = "nest_talk_daily_reports"

    report_date: Mapped[date] = mapped_column(Date, index=True, comment="报表日期")
    region_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="区域名称(空表示全局)")
    report_type: Mapped[str] = mapped_column(String(20), default="daily", comment="报表类型: daily(日报), weekly(周报)")
    image_url: Mapped[str] = mapped_column(String(500), comment="报表图片URL")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="报表摘要")
