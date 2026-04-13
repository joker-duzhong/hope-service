import uuid
from typing import Optional
from sqlalchemy import String, Text, ForeignKey, Integer, Boolean, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from core.database import CoreModel

class CommunityPersona(CoreModel):
    """社区专属马甲表 (User Persona)"""
    __tablename__ = "gaokao_community_personas"

    core_user_id: Mapped[uuid.UUID] = mapped_column(index=True, unique=True, comment="关联主业务的用户ID")
    nickname: Mapped[str] = mapped_column(String(50), comment="马甲名")
    avatar_url: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, comment="头像Seed/URL")
    status_emoji: Mapped[Optional[str]] = mapped_column(String(20), nullable=True, comment="状态Emoji")
    
    # 隐私设置
    ai_collection_enabled: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否允许AI收录")
    burn_after_reading_hours: Mapped[int] = mapped_column(Integer, default=48, comment="阅后即焚时间(小时)")

class TreeholePost(CoreModel):
    """双面树洞帖子表"""
    __tablename__ = "gaokao_treehole_posts"

    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_community_personas.id"), index=True, comment="马甲ID")
    content: Mapped[str] = mapped_column(Text, comment="帖子内容")
    type: Mapped[str] = mapped_column(String(20), comment="类型: emo | help")
    hug_count: Mapped[int] = mapped_column(Integer, default=0, comment="抱抱数/点赞数")
    has_ai_reply: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已有AI回复")

class TreeholeReply(CoreModel):
    """双面树洞回复表"""
    __tablename__ = "gaokao_treehole_replies"

    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_treehole_posts.id"), index=True)
    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("gaokao_community_personas.id"), nullable=True, comment="回复人马甲ID，AI回复为None")
    content: Mapped[str] = mapped_column(Text, comment="回复内容")
    is_ai_reply: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为AI回复")

class TreeholeHug(CoreModel):
    """树洞抱抱记录表(防刷)"""
    __tablename__ = "gaokao_treehole_hugs"
    __table_args__ = (
        Index("idx_treehole_hug_persona_post", "persona_id", "post_id", unique=True),
    )

    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_community_personas.id"), index=True)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_treehole_posts.id"), index=True)

class BoardPost(CoreModel):
    """志愿红黑榜帖子表"""
    __tablename__ = "gaokao_board_posts"

    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_community_personas.id"), index=True)
    school_name: Mapped[str] = mapped_column(String(100), comment="院校名称")
    major_name: Mapped[str] = mapped_column(String(100), comment="专业名称")
    content: Mapped[str] = mapped_column(Text, comment="评价内容")
    vote_count: Mapped[int] = mapped_column(Integer, default=0, comment="总投票数")
    red_count: Mapped[int] = mapped_column(Integer, default=0, comment="红榜数")
    green_count: Mapped[int] = mapped_column(Integer, default=0, comment="绿榜数")
    ai_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="AI 总结")
    has_ai_summary: Mapped[bool] = mapped_column(Boolean, default=False)
    is_wiped: Mapped[bool] = mapped_column(Boolean, default=False, index=True, comment="是否已脱敏(软删)")

class BoardVote(CoreModel):
    """志愿红黑榜投票表"""
    __tablename__ = "gaokao_board_votes"
    __table_args__ = (
        Index("idx_board_vote_persona_post", "persona_id", "post_id", unique=True),
    )

    persona_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("gaokao_community_personas.id"), index=True, nullable=True)
    post_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_board_posts.id"), index=True)
    option: Mapped[str] = mapped_column(String(10), comment="投票选项: red | green")
    comment: Mapped[Optional[str]] = mapped_column(String(200), nullable=True, comment="短评")

class LimitedRoom(CoreModel):
    """48小时限时搭子房间表"""
    __tablename__ = "gaokao_limited_rooms"

    creator_persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_community_personas.id"))
    title: Mapped[str] = mapped_column(String(100))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    member_count: Mapped[int] = mapped_column(Integer, default=1)
    max_members: Mapped[int] = mapped_column(Integer, default=8)
    is_expired: Mapped[bool] = mapped_column(Boolean, default=False)
    scrapbook_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="AI生成的纪念册")

class RoomMessage(CoreModel):
    """房间消息记录表"""
    __tablename__ = "gaokao_room_messages"

    room_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_limited_rooms.id"), index=True)
    persona_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("gaokao_community_personas.id"))
    nickname: Mapped[str] = mapped_column(String(50), comment="马甲昵称")
    avatar_id: Mapped[int] = mapped_column(Integer, comment="头像ID")
    content: Mapped[str] = mapped_column(Text)
