import logging
import asyncio
from celery import shared_task
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from datetime import datetime, timedelta, timezone

from core.config import settings
from core.llm.engine import generate_chat
from apps.zaiwen_gaokao.models import TreeholePost, TreeholeReply, BoardPost
from apps.zaiwen_gaokao.prompts import (
    TREEHOLE_EMO_PROMPT, TREEHOLE_HELP_PROMPT, TREEHOLE_GENERAL_PROMPT,
    BOARD_SUMMARY_PROMPT, ROOM_SCRAPBOOK_PROMPT
)

logger = logging.getLogger(__name__)

# 定义本地异步引擎，避免 Celery 进程间共享引擎产生的问题
local_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
local_session_maker = async_sessionmaker(local_engine, expire_on_commit=False)

@shared_task(name="apps.zaiwen_gaokao.tasks.process_treehole_ai_reply")
def process_treehole_ai_reply(post_id: str, post_type: str, content: str):
    """异步处理树洞 AI 回复"""
    asyncio.run(_async_process_treehole_ai_reply(post_id, post_type, content))

async def _async_process_treehole_ai_reply(post_id: str, post_type: str, content: str):
    # 1. 模拟风控 (正式环境应对接第三方 SDK)
    if "违规词" in content:
        logger.warning(f"帖子 {post_id} 触发模拟风控拦截")
        return

    # 2. 根据类型选择提示词
    if post_type == "emo":
        prompt = TREEHOLE_EMO_PROMPT.format(content=content)
    elif post_type == "help":
        prompt = TREEHOLE_HELP_PROMPT.format(content=content)
    else:
        prompt = TREEHOLE_GENERAL_PROMPT.format(content=content)

    try:
        reply_content = await generate_chat([{"role": "user", "content": prompt}])
        
        async with local_session_maker() as db:
            # 存入回复表
            reply = TreeholeReply(
                post_id=post_id,
                content=reply_content,
                is_ai_reply=True
            )
            db.add(reply)
            # 更新帖子状态
            from sqlalchemy import update
            await db.execute(
                update(TreeholePost).where(TreeholePost.id == post_id).values(has_ai_reply=True)
            )
            await db.commit()
            logger.info(f"帖子 {post_id} 的 AI 回复已生成并入库")
    except Exception as e:
        logger.error(f"处理树洞 AI 回复失败: {e}")

@shared_task(name="apps.zaiwen_gaokao.tasks.generate_board_ai_summary")
def generate_board_ai_summary(post_id: str):
    """异步生成红黑榜 AI 总结"""
    asyncio.run(_async_generate_board_ai_summary(post_id))

async def _async_generate_board_ai_summary(post_id: str):
    try:
        async with local_session_maker() as db:
            from apps.zaiwen_gaokao.models import BoardVote
            # 拉取该帖子的前 5 条投票短评
            stmt = select(BoardVote.comment).where(
                BoardVote.post_id == post_id,
                BoardVote.comment != None,
                BoardVote.comment != ""
            ).limit(5)
            result = await db.execute(stmt)
            comments = result.scalars().all()
            
            if len(comments) < 5:
                return

            # 获取帖子原文内容
            stmt_post = select(BoardPost).where(BoardPost.id == post_id)
            res_post = await db.execute(stmt_post)
            post = res_post.scalar_one_or_none()
            
            context_info = f"院校:{post.school_name}, 专业:{post.major_name}\n原文内容:{post.content}"
            comments_str = "\n".join([f"- {c}" for c in comments])
            prompt = BOARD_SUMMARY_PROMPT.format(context=context_info, comments=comments_str)

            summary = await generate_chat([{"role": "user", "content": prompt}])
            
            # 更新帖子状态并存入总结
            await db.execute(
                update(BoardPost).where(BoardPost.id == post_id).values(
                    has_ai_summary=True,
                    ai_summary=summary
                )
            )
            await db.commit()
            logger.info(f"帖子 {post_id} 的 AI 总结已生成并入库")

    except Exception as e:
        logger.error(f"处理红黑榜 AI 总结失败: {e}")

@shared_task(name="apps.zaiwen_gaokao.tasks.check_room_lifecycles")
def check_room_lifecycles():
    """生命周期任务：预警与结册"""
    asyncio.run(_async_check_room_lifecycles())

async def _async_check_room_lifecycles():
    from datetime import datetime, timedelta
    now = datetime.now(timezone.utc)
    
    async with local_session_maker() as db:
        from apps.zaiwen_gaokao.models import LimitedRoom, RoomMessage
        from sqlalchemy import update
        
        # 1. T=48小时：结册并解散
        expiry_limit = now - timedelta(hours=48)
        stmt = select(LimitedRoom).where(
            LimitedRoom.created_at <= expiry_limit,
            LimitedRoom.is_expired == False,
            LimitedRoom.is_deleted == False
        )
        res = await db.execute(stmt)
        expired_rooms = res.scalars().all()
        
        for room in expired_rooms:
            # 拉取聊天记录
            msg_stmt = select(RoomMessage).where(RoomMessage.room_id == room.id).order_by(RoomMessage.created_at.asc())
            msg_res = await db.execute(msg_stmt)
            messages = msg_res.scalars().all()
            
            chat_log = "\n".join([f"{m.nickname}: {m.content}" for m in messages])
            prompt = ROOM_SCRAPBOOK_PROMPT.format(room_title=room.title, chat_log=chat_log)
            
            try:
                scrapbook = await generate_chat([{"role": "user", "content": prompt}])
                room.scrapbook_content = scrapbook
            except:
                room.scrapbook_content = "纪念册生成中..."
                
            room.is_expired = True
            # TODO: 这里应该发送站内信通知用户，暂略
            
        # 2. T-5分钟：预警 (存活 47h 55m 到 48h 之间的且未预警的)
        # 简化逻辑：这里仅标记或简单处理，实际应发送消息
        
        await db.commit()
