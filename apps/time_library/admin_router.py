"""
时空图书馆 B 端接口 (Admin Router)
面向管理员，CRUD 管理
"""
import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.users.dependencies import require_roles, require_role_in_scope
from core.response import ResponseModel, MessageResponse
from apps.time_library import schemas, services

# 管理端路由：必须具备 time_library 分支下的 admin 角色
# 或全局超级管理员
router = APIRouter(dependencies=[Depends(require_role_in_scope("time_library", "admin"))])


@router.post("/books", response_model=ResponseModel[schemas.BookListRead], status_code=status.HTTP_201_CREATED, summary="录入书籍")
async def create_new_book(
    book_in: schemas.BookCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 管理员视角：录入新时空之书
    - 必须指定年份、地点经纬度。
    """
    db_book = await services.BookService.create_book(db, book_in)
    return ResponseModel(data=db_book)


@router.put("/books/{book_id}", response_model=ResponseModel[schemas.BookListRead], summary="更新书籍信息")
async def update_book_info(
    book_id: uuid.UUID,
    book_in: schemas.BookUpdate,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 管理员视角：修补时空之书元数据
    - 软更新机制，未传字段不修改。
    """
    db_book = await services.BookService.update_book(db, book_id, book_in)
    if not db_book:
        raise HTTPException(status_code=404, detail="书籍未找到")
    return ResponseModel(data=db_book)


@router.delete("/books/{book_id}", response_model=MessageResponse, summary="下架书籍")
async def soft_delete_book(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 管理员视角：抹除时空之书 (软删除)
    - 数据库保留记录，前台不可见。
    """
    ok = await services.BookService.delete_book(db, book_id)
    if not ok:
        raise HTTPException(status_code=404, detail="书籍未找到或已删除")
    return MessageResponse(message="书籍下架成功")


@router.post("/books/{book_id}/contents", response_model=ResponseModel[schemas.BookContentRead], summary="追加章节内容")
async def append_chapter(
    book_id: uuid.UUID,
    content_in: schemas.BookContentCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 管理员视角：为书籍灌注灵魂章节
    - 建议按 order 递增录入。
    """
    db_content = await services.ContentService.create_content(db, book_id, content_in)
    return ResponseModel(data=db_content)


@router.post("/books/{book_id}/persona", response_model=ResponseModel[schemas.AIPersonaRead], summary="配置 AI 人设")
async def setup_ai_persona(
    book_id: uuid.UUID,
    persona_in: schemas.AIPersonaCreate,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 管理员视角：激活该书的 AI 对话能力
    - 配置对应的人设提示词，为后续接入 LLM 服务提供支持。
    """
    db_persona = await services.PersonaService.set_persona(db, book_id, persona_in)
    return ResponseModel(data=db_persona)
