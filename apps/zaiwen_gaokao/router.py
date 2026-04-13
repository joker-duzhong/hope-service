from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List, Optional
from uuid import UUID

from core.database import get_db
from core.users.dependencies import get_current_user
from core.response import ResponseModel, PaginatedResponse
from apps.zaiwen_gaokao.schemas import (
    TreeholePostCreate, TreeholePostRead, TreeholeFeedItem, TreeholeAuthorInfo, TreeholeReplyRead,
    BoardPostCreate, BoardPostRead, BoardVoteCreate, BoardDetailRead, BoardVoteRead,
    RoomCreate, RoomRead,
    PersonaRead, PersonaUpdate, ProfileMeRead
)
from apps.zaiwen_gaokao.services import GaokaoService

router = APIRouter()

# --- 个人中心与马甲 ---

@router.get("/profile/me", summary="个人面板数据", response_model=ResponseModel[ProfileMeRead])
async def get_profile_me(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    profile = await GaokaoService.get_profile_me(db, current_user.id)
    return ResponseModel(data=profile)

@router.post("/profile/randomize", summary="随机马甲生成", response_model=ResponseModel[PersonaRead])
async def randomize_persona(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    persona = await GaokaoService.randomize_persona(db, current_user.id)
    return ResponseModel(data=PersonaRead.model_validate(persona))

@router.put("/profile/settings", summary="隐私状态偏好更新", response_model=ResponseModel[PersonaRead])
async def update_persona_settings(
    data: PersonaUpdate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    persona = await GaokaoService.update_persona_settings(db, current_user.id, data)
    return ResponseModel(data=PersonaRead.model_validate(persona))

@router.delete("/profile/wipe", summary="抹除痕迹(斩断前缘)")
async def wipe_persona_data(
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    await GaokaoService.wipe_persona_data(db, current_user.id)
    return ResponseModel(message="痕迹已抹除，已分配新身份")

@router.get("/profile/my-treeholes", summary="获取我的树洞记录", response_model=ResponseModel[List[TreeholePostRead]])
async def get_my_treeholes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    posts = await GaokaoService.get_my_treeholes(db, current_user.id, limit, offset)
    return ResponseModel(data=[TreeholePostRead.model_validate(p) for p in posts])

@router.get("/profile/my-audits", summary="获取我的质询（投票）记录", response_model=ResponseModel[List[BoardVoteRead]])
async def get_my_board_votes(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    votes = await GaokaoService.get_my_board_votes(db, current_user.id, limit, offset)
    return ResponseModel(data=[BoardVoteRead.model_validate(v) for v in votes])

# --- 双面树洞 ---

@router.post("/treehole/post", summary="树洞发帖")
async def create_treehole_post(
    data: TreeholePostCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    post = await GaokaoService.create_treehole_post(db, current_user.id, data)
    return ResponseModel(data=TreeholePostRead.model_validate(post))

@router.delete("/treehole/post/{post_id}", summary="销毁单条树洞")
async def delete_treehole_post(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    await GaokaoService.delete_treehole_post(db, current_user.id, post_id)
    return ResponseModel(message="帖子已从宇宙中抹除")

@router.get("/treehole/feed", summary="树洞信息流", response_model=ResponseModel[List[TreeholeFeedItem]])
async def get_treehole_feed(
    cursor: Optional[UUID] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    feed_data = await GaokaoService.get_treehole_feed(db, cursor)
    res = []
    for item in feed_data:
        post_data = TreeholePostRead.model_validate(item["post"]).model_dump()
        author_data = TreeholeAuthorInfo.model_validate(item["author"])
        ai_reply = TreeholeReplyRead.model_validate(item["ai_reply"]) if item["ai_reply"] else None
        res.append(TreeholeFeedItem(**post_data, author=author_data, ai_reply=ai_reply))
    return ResponseModel(data=res)

@router.post("/treehole/hug/{post_id}", summary="树洞抱抱")
async def hug_treehole_post(
    post_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    await GaokaoService.hug_treehole_post(db, current_user.id, post_id)
    return ResponseModel(message="抱抱成功")

# --- 志愿红黑榜 ---

@router.get("/board/feed", summary="红黑榜信息流/搜索", response_model=ResponseModel[List[BoardPostRead]])
async def get_board_feed(
    school_name: Optional[str] = Query(None),
    sort_by: str = Query("new", pattern="^(new|hot)$"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db)
):
    items = await GaokaoService.get_board_feed(db, school_name, sort_by, limit, offset)
    response_data = []
    for item in items:
        p_dict = BoardPostRead.model_validate(item["post"]).model_dump()
        p_dict["author"] = item["author"]
        response_data.append(BoardPostRead(**p_dict))
    return ResponseModel(data=response_data)

@router.get("/board/{post_id}", summary="红黑榜详情页", response_model=ResponseModel[BoardDetailRead])
async def get_board_detail(
    post_id: UUID,
    db: AsyncSession = Depends(get_db)
):
    detail = await GaokaoService.get_board_detail(db, post_id)
    post_data = BoardPostRead.model_validate(detail["post"]).model_dump()
    post_data["author"] = detail["author"]
    votes_data = [BoardVoteRead.model_validate(v) for v in detail["votes"]]
    return ResponseModel(data=BoardDetailRead(**post_data, votes=votes_data))

@router.post("/board/post", summary="红黑榜发帖", response_model=ResponseModel[BoardPostRead])
async def create_board_post(
    data: BoardPostCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    post = await GaokaoService.create_board_post(db, current_user.id, data)
    return ResponseModel(data=BoardPostRead.model_validate(post))

@router.post("/board/vote", summary="红黑榜投票")
async def vote_board_post(
    data: BoardVoteCreate,
    db: AsyncSession = Depends(get_db),
    current_user = Depends(get_current_user)
):
    await GaokaoService.vote_board_post(db, current_user.id, data)
    return ResponseModel(message="投票成功")
