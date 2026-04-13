from uuid import UUID
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, Field

# --- 社区马甲与个人中心 ---

class PersonaRead(BaseModel):
    id: UUID
    nickname: str
    avatar_url: Optional[str] = None
    status_emoji: Optional[str] = None
    ai_collection_enabled: bool
    burn_after_reading_hours: int

    class Config:
        from_attributes = True

class PersonaUpdate(BaseModel):
    nickname: Optional[str] = Field(None, min_length=1, max_length=50)
    avatar_url: Optional[str] = None
    status_emoji: Optional[str] = None
    ai_collection_enabled: Optional[bool] = None
    burn_after_reading_hours: Optional[int] = Field(None, ge=1, le=168)

class ProfileMeRead(BaseModel):
    persona: PersonaRead
    received_hugs: int = 0
    sent_hugs: int = 0

# --- 双面树洞 ---

class TreeholePostCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=1000)
    type: str = Field(..., pattern="^(emo|help)$")

class TreeholePostRead(BaseModel):
    id: UUID
    persona_id: UUID
    content: str
    type: str
    hug_count: int
    has_ai_reply: bool
    created_at: datetime

    class Config:
        from_attributes = True

class TreeholeReplyRead(BaseModel):
    id: UUID
    content: str
    is_ai_reply: bool
    persona_id: Optional[UUID] = None
    created_at: datetime

    class Config:
        from_attributes = True

class TreeholeAuthorInfo(BaseModel):
    nickname: str
    avatar_url: Optional[str] = None
    status_emoji: Optional[str] = None

class TreeholeFeedItem(TreeholePostRead):
    author: Optional[TreeholeAuthorInfo] = None
    ai_reply: Optional[TreeholeReplyRead] = None

# --- 志愿红黑榜 ---

class BoardPostCreate(BaseModel):
    school_name: str = Field(..., min_length=1, max_length=100)
    major_name: str = Field(..., min_length=1, max_length=100)
    content: str = Field(..., min_length=1, max_length=1000)

class BoardVoteRead(BaseModel):
    id: UUID
    option: str
    comment: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True

class BoardAuthorInfo(BaseModel):
    nickname: str
    avatar_url: Optional[str] = None
    status_emoji: Optional[str] = None

class BoardPostRead(BaseModel):
    id: UUID
    persona_id: UUID
    school_name: str
    major_name: str
    content: str
    vote_count: int
    red_count: int
    green_count: int
    ai_summary: Optional[str] = None
    has_ai_summary: bool
    created_at: datetime
    author: Optional[BoardAuthorInfo] = None
    is_wiped: bool = False

    class Config:
        from_attributes = True

class BoardDetailRead(BoardPostRead):
    votes: List[BoardVoteRead] = []

class BoardVoteCreate(BaseModel):
    post_id: UUID
    option: str = Field(..., pattern="^(red|green)$")
    comment: Optional[str] = Field(None, max_length=200)

# --- 48小时限时搭子 ---

class RoomCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None

class RoomRead(BaseModel):
    id: UUID
    title: str
    description: Optional[str]
    member_count: int
    max_members: int
    created_at: datetime

    class Config:
        from_attributes = True

class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1)

class MessageRead(BaseModel):
    id: UUID
    nickname: str
    avatar_id: int
    content: str
    created_at: datetime

    class Config:
        from_attributes = True
