"""
时空图书馆 业务逻辑层 (Services)
"""
import uuid
from typing import List, Optional, Sequence

from sqlalchemy import select, update, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.time_library import models, schemas


class BookService:
    """书籍相关 CRUD"""

    @staticmethod
    async def get_books_by_year(
        db: AsyncSession, start_year: Optional[int] = None, end_year: Optional[int] = None
    ) -> Sequence[models.Book]:
        """按年份区间查询书籍列表 (仅 C 端只读，过滤软删除)"""
        stmt = select(models.Book).where(models.Book.is_deleted == False)
        if start_year is not None:
            stmt = stmt.where(models.Book.year >= start_year)
        if end_year is not None:
            stmt = stmt.where(models.Book.year <= end_year)
        
        stmt = stmt.order_by(models.Book.year.asc())
        result = await db.execute(stmt)
        return result.scalars().all()

    @staticmethod
    async def get_book_detail(db: AsyncSession, book_id: uuid.UUID) -> Optional[models.Book]:
        """获取书籍详情 (带章节和 AI 人设)"""
        stmt = select(models.Book).options(
            selectinload(models.Book.contents.and_(models.BookContent.is_deleted == False)),
            selectinload(models.Book.ai_persona)
        ).where(
            and_(models.Book.id == book_id, models.Book.is_deleted == False)
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def create_book(db: AsyncSession, book_in: schemas.BookCreate) -> models.Book:
        """创建书籍 (管理端)"""
        db_book = models.Book(**book_in.model_dump())
        db.add(db_book)
        await db.commit()
        await db.refresh(db_book)
        return db_book

    @staticmethod
    async def update_book(db: AsyncSession, book_id: uuid.UUID, book_in: schemas.BookUpdate) -> Optional[models.Book]:
        """更新书籍 (管理端)"""
        stmt = update(models.Book).where(
            and_(models.Book.id == book_id, models.Book.is_deleted == False)
        ).values(**book_in.model_dump(exclude_unset=True)).returning(models.Book)
        
        result = await db.execute(stmt)
        db_book = result.scalar_one_or_none()
        if db_book:
            await db.commit()
            await db.refresh(db_book)
        return db_book

    @staticmethod
    async def delete_book(db: AsyncSession, book_id: uuid.UUID) -> bool:
        """软删除书籍 (管理端)"""
        stmt = update(models.Book).where(
            models.Book.id == book_id
        ).values(is_deleted=True)
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


class ContentService:
    """书籍章节相关 CRUD"""

    @staticmethod
    async def create_content(db: AsyncSession, book_id: uuid.UUID, content_in: schemas.BookContentCreate) -> models.BookContent:
        """追加书籍内容"""
        db_content = models.BookContent(book_id=book_id, **content_in.model_dump())
        db.add(db_content)
        await db.commit()
        await db.refresh(db_content)
        return db_content

    @staticmethod
    async def delete_content(db: AsyncSession, content_id: uuid.UUID) -> bool:
        """软删除章节内容"""
        stmt = update(models.BookContent).where(
            models.BookContent.id == content_id
        ).values(is_deleted=True)
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0


class PersonaService:
    """AI 人设相关 CRUD"""

    @staticmethod
    async def set_persona(db: AsyncSession, book_id: uuid.UUID, persona_in: schemas.AIPersonaCreate) -> models.AIPersona:
        """设置或更新书籍人设 (基于唯一约束，先查后更或直接覆盖逻辑)"""
        # 尝试查询现有的人设
        stmt = select(models.AIPersona).where(models.AIPersona.book_id == book_id)
        result = await db.execute(stmt)
        db_persona = result.scalar_one_or_none()

        if db_persona:
            # 更新已有
            for key, value in persona_in.model_dump().items():
                setattr(db_persona, key, value)
            db_persona.is_deleted = False # 可能是之前被删了，重新激活
        else:
            # 创建新的人设
            db_persona = models.AIPersona(book_id=book_id, **persona_in.model_dump())
            db.add(db_persona)
        
        await db.commit()
        await db.refresh(db_persona)
        return db_persona
