"""
时空图书馆 C 端接口 (Router)
面向普通用户，只读查询
"""
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from core.response import ResponseModel, PaginatedResponse
from apps.time_library import schemas, services

router = APIRouter()


@router.get("/books", response_model=ResponseModel[List[schemas.BookListRead]], summary="按年份范围获取书籍列表")
async def list_books(
    start_year: Optional[int] = Query(None, description="起始年份 (负数为公元前)"),
    end_year: Optional[int] = Query(None, description="结束年份 (负数为公元前)"),
    db: AsyncSession = Depends(get_db)
):
    """
    ## 用户视角：漫游 3D 地图书架
    - 结合年份筛选，为前端 3D 合集提供数据同步。
    - 仅返回已启用的书籍 (is_deleted=False)。
    """
    books = await services.BookService.get_books_by_year(db, start_year, end_year)
    return ResponseModel(data=books)


@router.get("/books/{book_id}", response_model=ResponseModel[schemas.BookDetailRead], summary="获取书籍详情")
async def get_book_detail(
    book_id: uuid.UUID,
    db: AsyncSession = Depends(get_db)
):
    """
    ## 用户视角：点击一本书进行沉浸式阅读
    - 返回书籍详情、章节列表以及 AI 人设基础信息。
    """
    book = await services.BookService.get_book_detail(db, book_id)
    if not book:
        raise HTTPException(status_code=404, detail="书籍不存在或已下架")
    return ResponseModel(data=book)
