"""
AI Gateway 模块 API 路由定义
"""
import uuid
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession as SqlAsyncSession

from apps.ai_gateway.models import AISession
from apps.ai_gateway.schemas import (
    AISessionCreate,
    AISessionRead,
    AISessionUpdate,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ImageStreamChatRequest,
    ImageStreamChatResponse,
)
from apps.ai_gateway.services import AISessionService, AIMessageService, AIChatService
from core.database import get_db, async_session_maker
from core.dependencies import get_app_key
from core.users.dependencies import get_current_user
from core.users.models import User
from core.response import ResponseModel, PaginatedResponse
from core.llm import engine
from core.storage.services import StorageService

router = APIRouter()


@router.post("/sessions", response_model=ResponseModel[AISessionRead])
async def create_session(
    data: AISessionCreate,
    current_user: User = Depends(get_current_user),
    scope: str = Depends(get_app_key),
    db: SqlAsyncSession = Depends(get_db),
):
    """创建新会话"""
    session = await AISessionService.create_session(db, current_user.id, data, scope=scope)
    return ResponseModel(data=session)


@router.get("/sessions", response_model=ResponseModel[List[AISessionRead]])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: SqlAsyncSession = Depends(get_db),
):
    """获取会话列表"""
    sessions = await AISessionService.list_sessions(db, current_user.id)
    return ResponseModel(data=sessions)


@router.delete("/sessions/{session_id}", response_model=ResponseModel)
async def delete_session(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: SqlAsyncSession = Depends(get_db),
):
    """软删除会话"""
    await AISessionService.delete_session(db, session_id, current_user.id)
    return ResponseModel(message="会话已删除")


@router.patch("/sessions/{session_id}", response_model=ResponseModel[AISessionRead])
async def update_session(
    session_id: uuid.UUID,
    data: AISessionUpdate,
    current_user: User = Depends(get_current_user),
    db: SqlAsyncSession = Depends(get_db),
):
    """更新会话标题"""
    session = await AISessionService.update_session(db, session_id, current_user.id, data)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    return ResponseModel(data=session)


@router.get("/sessions/{session_id}/messages", response_model=ResponseModel[List[dict]])
async def get_session_history(
    session_id: uuid.UUID,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: SqlAsyncSession = Depends(get_db),
):
    """获取会话历史消息"""
    session = await AISessionService.get_session(db, session_id, current_user.id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    history = await AIMessageService.get_history_messages(db, session_id, limit)
    return ResponseModel(data=history)


@router.post("/chat/completions")
async def chat_completions(
    request_data: ChatCompletionRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    scope: str = Depends(get_app_key),
    db: SqlAsyncSession = Depends(get_db),
):
    """
    对话接口（支持一次性响应或 SSE 流式响应）
    """
    # 1. 自动处理会话：如果没有提供 session_id，则创建一个新会话
    if not request_data.session_id:
        session = await AISessionService.create_session(
            db, current_user.id, AISessionCreate(title=request_data.prompt[:15]),
            scope=scope
        )
        session_id = session.id
        # 此处不需要 await db.commit() 因为 create_session 内部已经 commit 了
    else:
        session = await AISessionService.get_session(db, request_data.session_id, current_user.id)
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        session_id = session.id

    # 2. 获取历史消息
    history = await AIMessageService.get_history_messages(db, session_id, request_data.max_history)

    # 3. 流式响应处理
    if request_data.stream:

        async def stream_with_save():
            full_response = ""
            current_messages = history + [{"role": "user", "content": request_data.prompt}]
            
            async for chunk in engine.generate_stream_chat(
                current_messages, request_data.provider, request_data.model
            ):
                full_response += chunk
                yield f"data: {json.dumps({'content': chunk, 'session_id': str(session_id)})}\n\n"
            
            yield "data: [DONE]\n\n"
            
            # 流式结束后，使用 BackgroundTasks 异步落库
            background_tasks.add_task(
                AIChatService.chat_background_save, 
                async_session_maker, 
                session_id, 
                request_data.prompt, 
                full_response,
                scope
            )

        return StreamingResponse(
            stream_with_save(),
            media_type="text/event-stream"
        )

    # 4. 非流式响应处理
    else:
        full_content = await engine.generate_chat(
            history + [{"role": "user", "content": request_data.prompt}],
            request_data.provider,
            request_data.model
        )
        
        # 同步保存到数据库
        await AIMessageService.create_message(db, session_id, "user", request_data.prompt, scope=scope)
        await AIMessageService.create_message(db, session_id, "assistant", full_content, scope=scope)
        
        return ResponseModel(data=ChatCompletionResponse(
            session_id=session_id,
            content=full_content,
            history_count=len(history) + 2
        ))


@router.post("/image/stream", response_model=ResponseModel[ImageStreamChatResponse])
async def image_stream_chat(
    request_data: ImageStreamChatRequest,
    current_user: User = Depends(get_current_user),
    scope: str = Depends(get_app_key),
    db: SqlAsyncSession = Depends(get_db),
):
    """
    流式图片生成调试接口。

    后端接收第三方 SSE，提取最终 Markdown 图片链接后一次性返回。
    """
    result = await engine.generate_stream_image_chat(
        messages=request_data.messages,
        provider=request_data.provider,
        model=request_data.model,
        size=request_data.size,
        quality=request_data.quality,
        background=request_data.background,
        output_format=request_data.output_format,
        output_compression=request_data.output_compression,
        n=request_data.n,
        temperature=request_data.temperature,
        top_p=request_data.top_p,
        timeout=request_data.timeout,
        extra_body=request_data.extra_body,
    )
    resource = await StorageService.upload_remote_file(
        db=db,
        remote_url=result["image_url"],
        owner_id=current_user.id,
        scope=scope,
    )
    return ResponseModel(data=ImageStreamChatResponse(content=result["content"], resource=resource))
