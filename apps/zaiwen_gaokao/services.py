import uuid
import random
import logging
from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, func, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from apps.zaiwen_gaokao.models import (
    CommunityPersona, TreeholePost, TreeholeReply,
    BoardPost, BoardVote, LimitedRoom, RoomMessage
)
from apps.zaiwen_gaokao.schemas import (
    TreeholePostCreate, BoardPostCreate, BoardVoteCreate,
    RoomCreate, PersonaUpdate
)
from core.exceptions import CustomException

logger = logging.getLogger(__name__)

class GaokaoService:
    # --- 个人中心与马甲管理 ---
    
    @staticmethod
    async def get_or_create_persona(db: AsyncSession, core_user_id: uuid.UUID) -> CommunityPersona:
        """获取或初始化用户的社区马甲"""
        stmt = select(CommunityPersona).where(CommunityPersona.core_user_id == core_user_id)
        result = await db.execute(stmt)
        persona = result.scalar_one_or_none()
        
        if not persona:
            # 随机生成初始马甲
            adjectives = ["焦虑的", "乐观的", "深思的", "热血的", "佛系的", "勤奋的"]
            nouns = ["修狗", "考研党", "理综受害者", "文综小天才", "刷题机器", "锦鲤"]
            nickname = f"{random.choice(adjectives)}{random.choice(nouns)}"
            
            persona = CommunityPersona(
                core_user_id=core_user_id,
                nickname=nickname,
                avatar_url=f"https://api.dicebear.com/7.x/avataaars/svg?seed={uuid.uuid4().hex[:8]}",
                status_emoji="✍️"
            )
            db.add(persona)
            await db.commit()
            await db.refresh(persona)
        return persona

    @staticmethod
    async def randomize_persona(db: AsyncSession, core_user_id: uuid.UUID) -> CommunityPersona:
        """随机重置马甲昵称和头像"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        
        adjectives = ["焦绿的", "紫腚行的", "满分的", "全对的", "逆袭的", "稳点的"]
        nouns = ["小松鼠", "学霸君", "锦鲤本鲤", "大考士", "梦想家", "奋斗批"]
        persona.nickname = f"{random.choice(adjectives)}{random.choice(nouns)}"
        persona.avatar_url = f"https://api.dicebear.com/7.x/avataaars/svg?seed={uuid.uuid4().hex[:8]}"
        
        await db.commit()
        await db.refresh(persona)
        return persona

    @staticmethod
    async def get_profile_me(db: AsyncSession, core_user_id: uuid.UUID):
        """聚合查询个人面板数据"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        
        # 统计收到的抱抱 (TreeholePost.hug_count 其中 persona_id 为当前用户的)
        stmt_received = select(func.sum(TreeholePost.hug_count)).where(TreeholePost.persona_id == persona.id, TreeholePost.is_deleted == False)
        res_received = await db.execute(stmt_received)
        received_hugs = res_received.scalar() or 0
        
        # 统计送出的抱抱
        sent_hugs = 0 
        
        return {
            "persona": persona,
            "received_hugs": received_hugs,
            "sent_hugs": sent_hugs
        }

    @staticmethod
    async def update_persona_settings(db: AsyncSession, core_user_id: uuid.UUID, data: PersonaUpdate) -> CommunityPersona:
        """更新隐私设置或马甲信息"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(persona, key, value)
        await db.commit()
        await db.refresh(persona)
        return persona

    @staticmethod
    async def wipe_persona_data(db: AsyncSession, core_user_id: uuid.UUID):
        """抹除个人数据并重置马甲 (合规核心)"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        persona_id = persona.id
        
        # 1. 软删除所有树洞帖子
        await db.execute(
            update(TreeholePost).where(TreeholePost.persona_id == persona_id).values(is_deleted=True)
        )
        
        # 2. 榜单脱敏：之前发布的所有的 BoardPost 标记为 is_wiped = True
        await db.execute(
            update(BoardPost).where(BoardPost.persona_id == persona_id).values(is_wiped=True)
        )

        # 3. 归零统计：重置当前马甲统计，并重置马甲
        await GaokaoService.randomize_persona(db, core_user_id)
        await db.commit()
        return True

    @staticmethod
    async def get_my_treeholes(db: AsyncSession, core_user_id: uuid.UUID, limit: int = 20, offset: int = 0):
        """获取我的树洞记录"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        stmt = select(TreeholePost).where(
            TreeholePost.persona_id == persona.id,
            TreeholePost.is_deleted == False
        ).order_by(TreeholePost.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_my_board_votes(db: AsyncSession, core_user_id: uuid.UUID, limit: int = 20, offset: int = 0):
        """获取我的红黑榜投票记录"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        stmt = select(BoardVote).where(
            BoardVote.persona_id == persona.id,
            BoardVote.is_deleted == False
        ).order_by(BoardVote.created_at.desc()).limit(limit).offset(offset)
        result = await db.execute(stmt)
        return result.scalars().all()

    # --- 双面树洞 ---

    @staticmethod
    async def create_treehole_post(db: AsyncSession, core_user_id: uuid.UUID, data: TreeholePostCreate) -> TreeholePost:
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        post = TreeholePost(
            persona_id=persona.id,
            content=data.content,
            type=data.type
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        
        # 触发异步风控和 AI 回复任务
        try:
            from apps.zaiwen_gaokao.tasks import process_treehole_ai_reply
            task = process_treehole_ai_reply.delay(str(post.id), post.type, post.content)
            logger.info(f"树洞 AI 回复任务已提交: post_id={post.id}, task_id={task.id}")
        except Exception as e:
            logger.error(f"提交树洞 AI 回复任务失败: {e}", exc_info=True)
            
        return post

    @staticmethod
    async def delete_treehole_post(db: AsyncSession, core_user_id: uuid.UUID, post_id: uuid.UUID):
        """销毁单条树洞"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        stmt = select(TreeholePost).where(
            TreeholePost.id == post_id,
            TreeholePost.persona_id == persona.id
        )
        result = await db.execute(stmt)
        post = result.scalar_one_or_none()
        if not post:
            raise CustomException(message="帖子不存在或无权操作", code=404)
        
        post.is_deleted = True
        await db.commit()
        return True

    @staticmethod
    async def get_treehole_feed(db: AsyncSession, cursor: Optional[uuid.UUID] = None, limit: int = 20):
        """获取树洞列表 (游标分页) 并包含作者信息"""
        stmt = select(TreeholePost, CommunityPersona).join(
            CommunityPersona, TreeholePost.persona_id == CommunityPersona.id
        ).where(TreeholePost.is_deleted == False)

        if cursor:
            cursor_stmt = select(TreeholePost.created_at).where(TreeholePost.id == cursor)
            cursor_res = await db.execute(cursor_stmt)
            cursor_time = cursor_res.scalar()
            if cursor_time:
                stmt = stmt.where(TreeholePost.created_at < cursor_time)

        stmt = stmt.order_by(TreeholePost.created_at.desc()).limit(limit)
        result = await db.execute(stmt)
        
        items = []
        for post, author in result.all():
            reply_stmt = select(TreeholeReply).where(
                TreeholeReply.post_id == post.id,
                TreeholeReply.is_ai_reply == True
            ).limit(1)
            reply_res = await db.execute(reply_stmt)
            ai_reply = reply_res.scalar_one_or_none()
            
            items.append({
                "post": post,
                "author": author,
                "ai_reply": ai_reply
            })
        return items

    @staticmethod
    async def hug_treehole_post(db: AsyncSession, core_user_id: uuid.UUID, post_id: uuid.UUID):
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        from apps.zaiwen_gaokao.models import TreeholeHug
        
        check_stmt = select(TreeholeHug).where(
            TreeholeHug.persona_id == persona.id,
            TreeholeHug.post_id == post_id
        )
        existing = (await db.execute(check_stmt)).scalar_one_or_none()
        if existing:
            raise CustomException(message="您已经抱过啦", code=400)
            
        hug = TreeholeHug(persona_id=persona.id, post_id=post_id)
        db.add(hug)
        
        stmt = update(TreeholePost).where(TreeholePost.id == post_id).values(hug_count=TreeholePost.hug_count + 1)
        await db.execute(stmt)
        await db.commit()

    # --- 志愿红黑榜 ---

    @staticmethod
    async def create_board_post(db: AsyncSession, core_user_id: uuid.UUID, data: BoardPostCreate) -> BoardPost:
        """红黑榜发帖"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        post = BoardPost(
            persona_id=persona.id,
            **data.model_dump()
        )
        db.add(post)
        await db.commit()
        await db.refresh(post)
        return post

    @staticmethod
    async def get_board_feed(
        db: AsyncSession, 
        school_name: Optional[str] = None, 
        sort_by: str = "new", 
        limit: int = 20, 
        offset: int = 0
    ):
        """红黑榜列表/搜索"""
        stmt = select(BoardPost, CommunityPersona).join(
            CommunityPersona, BoardPost.persona_id == CommunityPersona.id
        )
        
        if school_name:
            stmt = stmt.where(BoardPost.school_name.contains(school_name))
        
        if sort_by == "hot":
            stmt = stmt.order_by(BoardPost.vote_count.desc())
        else:
            stmt = stmt.order_by(BoardPost.created_at.desc())
            
        stmt = stmt.limit(limit).offset(offset)
        result = await db.execute(stmt)
        
        items = []
        for post, author in result.all():
            if getattr(post, 'is_wiped', False):
                author_data = {
                    "nickname": "已抹除痕迹的旅行者",
                    "avatar_url": None,
                    "status_emoji": None
                }
            else:
                author_data = {
                    "nickname": author.nickname,
                    "avatar_url": author.avatar_url,
                    "status_emoji": author.status_emoji
                }
            items.append({
                "post": post,
                "author": author_data
            })
        return items

    @staticmethod
    async def get_board_detail(db: AsyncSession, post_id: uuid.UUID):
        """红黑榜详情页（带投票记录与作者）"""
        stmt = select(BoardPost, CommunityPersona).join(
            CommunityPersona, BoardPost.persona_id == CommunityPersona.id
        ).where(BoardPost.id == post_id)
        result = await db.execute(stmt)
        row = result.first()
        
        if not row:
            raise CustomException(message="记录不存在", code=404)
            
        post, author = row
        
        if getattr(post, 'is_wiped', False):
            author_data = {
                "nickname": "已抹除痕迹的旅行者",
                "avatar_url": None,
                "status_emoji": None
            }
        else:
            author_data = {
                "nickname": author.nickname,
                "avatar_url": author.avatar_url,
                "status_emoji": author.status_emoji
            }
        
        vote_stmt = select(BoardVote).where(
            BoardVote.post_id == post_id, 
            BoardVote.comment != None
        ).order_by(BoardVote.created_at.desc()).limit(20)
        vote_result = await db.execute(vote_stmt)
        votes = vote_result.scalars().all()
        
        return {
            "post": post,
            "author": author_data,
            "votes": votes
        }

    @staticmethod
    async def vote_board_post(db: AsyncSession, core_user_id: uuid.UUID, data: BoardVoteCreate):
        """红黑榜投票 (带互斥与自动AI总结触发)"""
        persona = await GaokaoService.get_or_create_persona(db, core_user_id)
        
        check_stmt = select(BoardVote).where(
            BoardVote.persona_id == persona.id,
            BoardVote.post_id == data.post_id
        )
        existing = (await db.execute(check_stmt)).scalar_one_or_none()
        if existing:
            raise CustomException(message="每个志愿意向仅限投票一次", code=400)
            
        post_stmt = select(BoardPost).where(BoardPost.id == data.post_id)
        post = (await db.execute(post_stmt)).scalar_one_or_none()
        if not post:
            raise CustomException(message="帖子不存在", code=404)
        
        vote = BoardVote(
            persona_id=persona.id,
            post_id=data.post_id,
            option=data.option,
            comment=data.comment
        )
        db.add(vote)
        
        # 原子更新投票数
        update_values = {"vote_count": BoardPost.vote_count + 1}
        if data.option == "red":
            update_values["red_count"] = BoardPost.red_count + 1
        elif data.option == "green":
            update_values["green_count"] = BoardPost.green_count + 1
            
        await db.execute(
            update(BoardPost).where(BoardPost.id == data.post_id).values(**update_values)
        )
        
        await db.commit()
        await db.refresh(post)
        
        if post.vote_count == 5 and not post.has_ai_summary:
            try:
                from apps.zaiwen_gaokao.tasks import generate_board_ai_summary
                generate_board_ai_summary.delay(str(post.id))
            except Exception:
                pass
            
        return vote
