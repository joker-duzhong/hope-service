"""Teacher Logbook business services."""
import csv
import io
import json
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Type

from fastapi import HTTPException
from sqlalchemy import Date as SADate, DateTime as SADateTime, Numeric, and_, delete, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.teacher_logbook import models, schemas


RESOURCE_MODELS: dict[str, Type[models.ClassRecord]] = {
    "leave-requests": models.LeaveRequest,
    "homework-records": models.HomeworkRecord,
    "violations": models.Violation,
    "alerts": models.Alert,
    "todos": models.Todo,
    "work-records": models.WorkRecord,
    "exams": models.Exam,
    "committee-roles": models.CommitteeRole,
    "committee-members": models.CommitteeMember,
    "hygiene-assignments": models.HygieneAssignment,
    "activities": models.Activity,
    "finance-records": models.FinanceRecord,
    "awards": models.Award,
    "courses": models.Course,
    "talks": models.Talk,
    "contacts": models.Contact,
    "training-records": models.TrainingRecord,
    "links": models.Link,
}

STUDENT_MODELS = (
    models.LeaveRequest, models.Violation, models.Alert, models.CommitteeMember,
    models.HygieneAssignment, models.Award, models.Talk, models.Contact,
)


def _not_found(message: str = "资源不存在") -> HTTPException:
    return HTTPException(status_code=404, detail=message)


def _conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


class ClassService:
    @staticmethod
    async def require(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> models.OwnedClass:
        item = await db.scalar(select(models.OwnedClass).where(
            models.OwnedClass.id == class_id,
            models.OwnedClass.user_id == user_id,
            models.OwnedClass.is_deleted.is_(False),
        ))
        if not item:
            raise _not_found("班级不存在")
        return item

    @staticmethod
    async def list(db: AsyncSession, user_id: uuid.UUID) -> list[models.OwnedClass]:
        result = await db.scalars(select(models.OwnedClass).where(
            models.OwnedClass.user_id == user_id, models.OwnedClass.is_deleted.is_(False)
        ).order_by(models.OwnedClass.created_at.desc()))
        return list(result.all())

    @staticmethod
    async def create(db: AsyncSession, user_id: uuid.UUID, payload: schemas.ClassCreate) -> models.OwnedClass:
        item = models.OwnedClass(user_id=user_id, **payload.model_dump())
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def update(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, payload: schemas.ClassUpdate) -> models.OwnedClass:
        item = await ClassService.require(db, class_id, user_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def remove(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> None:
        item = await ClassService.require(db, class_id, user_id)
        item.is_deleted = True
        await db.commit()


class StudentService:
    @staticmethod
    async def list(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, page: int, page_size: int,
                   keyword: Optional[str] = None, gender: Optional[str] = None) -> tuple[list[models.Student], int]:
        await ClassService.require(db, class_id, user_id)
        filters = [models.Student.class_id == class_id, models.Student.user_id == user_id, models.Student.is_deleted.is_(False)]
        if keyword:
            filters.append(or_(models.Student.name.ilike(f"%{keyword}%"), models.Student.contact.ilike(f"%{keyword}%")))
        if gender:
            filters.append(models.Student.gender == gender)
        total = await db.scalar(select(func.count()).select_from(models.Student).where(*filters)) or 0
        result = await db.scalars(select(models.Student).where(*filters).order_by(models.Student.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
        return list(result.all()), total

    @staticmethod
    async def get(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, student_id: uuid.UUID) -> models.Student:
        await ClassService.require(db, class_id, user_id)
        item = await db.scalar(select(models.Student).where(models.Student.id == student_id, models.Student.class_id == class_id,
            models.Student.user_id == user_id, models.Student.is_deleted.is_(False)))
        if not item:
            raise _not_found("学生不存在")
        return item

    @staticmethod
    async def create(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, payload: schemas.StudentCreate) -> models.Student:
        await ClassService.require(db, class_id, user_id)
        item = models.Student(class_id=class_id, user_id=user_id, **payload.model_dump())
        db.add(item)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def update(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, student_id: uuid.UUID,
                     payload: schemas.StudentUpdate) -> models.Student:
        item = await StudentService.get(db, class_id, user_id, student_id)
        for key, value in payload.model_dump(exclude_unset=True).items():
            setattr(item, key, value)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def remove(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, student_id: uuid.UUID) -> None:
        item = await StudentService.get(db, class_id, user_id, student_id)
        counts = {}
        for model in STUDENT_MODELS:
            count = await db.scalar(select(func.count()).select_from(model).where(
                model.class_id == class_id, model.student_id == student_id, model.is_deleted.is_(False))) or 0
            if count:
                counts[model.__tablename__] = count
        if counts:
            raise _conflict(f"学生仍有关联记录: {counts}")
        item.is_deleted = True
        await db.execute(update(models.SeatAssignment).where(
            models.SeatAssignment.class_id == class_id, models.SeatAssignment.student_id == student_id
        ).values(is_deleted=True))
        await db.commit()

    @staticmethod
    async def import_csv(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, content: bytes,
                         dry_run: bool, duplicate_strategy: str) -> schemas.ImportResult:
        await ClassService.require(db, class_id, user_id)
        if len(content) > 2 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="CSV 文件不能超过 2 MB")
        try:
            rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=400, detail="CSV 必须使用 UTF-8 编码") from exc
        created = skipped = failed = 0
        errors: list[dict] = []
        for number, row in enumerate(rows, start=2):
            try:
                payload = schemas.StudentCreate(name=(row.get("name") or "").strip(), gender=(row.get("gender") or "").strip(), contact=(row.get("contact") or "").strip() or None)
                duplicate = await db.scalar(select(models.Student.id).where(
                    models.Student.class_id == class_id, models.Student.is_deleted.is_(False), models.Student.name == payload.name,
                    models.Student.gender == payload.gender, models.Student.contact == payload.contact))
                if duplicate and duplicate_strategy == "skip":
                    skipped += 1
                else:
                    created += 1
                    if not dry_run:
                        db.add(models.Student(class_id=class_id, user_id=user_id, **payload.model_dump()))
            except Exception as exc:
                failed += 1
                errors.append({"row": number, "field": "row", "message": str(exc)})
        result = schemas.ImportResult(total_rows=len(rows), created=created, skipped=skipped, failed=failed, errors=errors)
        db.add(models.AuditLog(class_id=class_id, user_id=user_id, action="student_import",
                               detail={"dryRun": dry_run, "totalRows": len(rows), "created": created,
                                       "skipped": skipped, "failed": failed}))
        await db.commit()
        return result


class CrudService:
    @staticmethod
    async def _validate_relations(db: AsyncSession, model: Type[models.ClassRecord], class_id: uuid.UUID,
                                  user_id: uuid.UUID, data: dict[str, Any]) -> None:
        if "student_id" in data:
            await StudentService.get(db, class_id, user_id, data["student_id"])
        if model is models.CommitteeMember and "role_id" in data:
            role = await db.scalar(select(models.CommitteeRole.id).where(models.CommitteeRole.id == data["role_id"],
                models.CommitteeRole.class_id == class_id, models.CommitteeRole.is_deleted.is_(False)))
            if not role:
                raise _not_found("班委职位不存在")

    @staticmethod
    async def list(db: AsyncSession, resource: str, class_id: uuid.UUID, user_id: uuid.UUID, page: int,
                   page_size: int, filters: dict[str, Any]) -> tuple[list[Any], int]:
        await ClassService.require(db, class_id, user_id)
        model = RESOURCE_MODELS[resource]
        clauses = [model.class_id == class_id, model.user_id == user_id, model.is_deleted.is_(False)]
        for key, value in filters.items():
            if value is None or not hasattr(model, key):
                continue
            if key == "date_from" and hasattr(model, "date"):
                clauses.append(model.date >= value)
            elif key == "date_to" and hasattr(model, "date"):
                clauses.append(model.date <= value)
            elif key == "keyword":
                text_columns = [getattr(model, name) for name in ("title", "name", "note") if hasattr(model, name)]
                if text_columns:
                    clauses.append(or_(*(column.ilike(f"%{value}%") for column in text_columns)))
            else:
                clauses.append(getattr(model, key) == value)
        total = await db.scalar(select(func.count()).select_from(model).where(*clauses)) or 0
        order = getattr(model, "date", model.created_at).desc()
        result = await db.scalars(select(model).where(*clauses).order_by(order).offset((page - 1) * page_size).limit(page_size))
        return list(result.all()), total

    @staticmethod
    async def get(db: AsyncSession, resource: str, class_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID):
        await ClassService.require(db, class_id, user_id)
        model = RESOURCE_MODELS[resource]
        item = await db.scalar(select(model).where(model.id == item_id, model.class_id == class_id,
            model.user_id == user_id, model.is_deleted.is_(False)))
        if not item:
            raise _not_found()
        return item

    @staticmethod
    async def create(db: AsyncSession, resource: str, class_id: uuid.UUID, user_id: uuid.UUID, payload: schemas.SchemaBase):
        await ClassService.require(db, class_id, user_id)
        model = RESOURCE_MODELS[resource]
        data = payload.model_dump(mode="python")
        if model is models.Link:
            data["url"] = str(data["url"])
        await CrudService._validate_relations(db, model, class_id, user_id, data)
        item = model(class_id=class_id, user_id=user_id, **data)
        db.add(item)
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _conflict("记录与现有数据冲突") from exc
        await db.refresh(item)
        return item

    @staticmethod
    async def update(db: AsyncSession, resource: str, class_id: uuid.UUID, user_id: uuid.UUID,
                     item_id: uuid.UUID, payload: schemas.SchemaBase):
        item = await CrudService.get(db, resource, class_id, user_id, item_id)
        data = payload.model_dump(exclude_unset=True, mode="python")
        if not data:
            raise HTTPException(status_code=400, detail="PATCH 至少需要一个字段")
        create_schema = schemas.RESOURCE_SCHEMAS[resource]
        complete = {name: getattr(item, name) for name in create_schema.model_fields}
        complete.update(data)
        create_schema.model_validate(complete)
        if resource == "links" and "url" in data:
            data["url"] = str(data["url"])
        await CrudService._validate_relations(db, type(item), class_id, user_id, data)
        for key, value in data.items():
            setattr(item, key, value)
        await db.commit()
        await db.refresh(item)
        return item

    @staticmethod
    async def remove(db: AsyncSession, resource: str, class_id: uuid.UUID, user_id: uuid.UUID, item_id: uuid.UUID) -> None:
        item = await CrudService.get(db, resource, class_id, user_id, item_id)
        item.is_deleted = True
        await db.commit()


class SeatService:
    @staticmethod
    def parse_version(if_match: Optional[str]) -> int:
        if not if_match:
            raise HTTPException(status_code=428, detail="缺少 If-Match 请求头")
        try:
            return int(if_match.strip('"').rsplit("-", 1)[1])
        except (ValueError, IndexError) as exc:
            raise HTTPException(status_code=400, detail="If-Match 格式不正确") from exc

    @staticmethod
    async def board(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, lock: bool = False) -> models.SeatBoard:
        await ClassService.require(db, class_id, user_id)
        stmt = select(models.SeatBoard).where(models.SeatBoard.class_id == class_id, models.SeatBoard.user_id == user_id,
            models.SeatBoard.is_deleted.is_(False))
        if lock:
            stmt = stmt.with_for_update()
        board = await db.scalar(stmt)
        if not board:
            board = models.SeatBoard(class_id=class_id, user_id=user_id, rows=1, column_groups=[1], version=1)
            db.add(board)
            await db.flush()
        return board

    @staticmethod
    async def require_version(board: models.SeatBoard, if_match: Optional[str]) -> None:
        if SeatService.parse_version(if_match) != board.version:
            raise HTTPException(status_code=412, detail={"code": "VERSION_MISMATCH", "currentVersion": board.version})

    @staticmethod
    async def get(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> tuple[dict, str]:
        board = await SeatService.board(db, class_id, user_id)
        result = await db.execute(select(models.SeatAssignment, models.Student.name).join(
            models.Student, models.Student.id == models.SeatAssignment.student_id).where(
            models.SeatAssignment.class_id == class_id, models.SeatAssignment.is_deleted.is_(False), models.Student.is_deleted.is_(False)))
        assignments = [schemas.SeatItem(student_id=item.student_id, student_name=name, row=item.row, column=item.column) for item, name in result.all()]
        await db.commit()
        data = {"layout": {"rows": board.rows, "columnGroups": board.column_groups, "columns": sum(board.column_groups)},
                "assignments": assignments, "version": board.version, "updatedAt": board.updated_at}
        return data, f'"seat-board-{board.version}"'

    @staticmethod
    async def layout(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, payload: schemas.LayoutUpdate,
                     if_match: Optional[str]) -> dict:
        board = await SeatService.board(db, class_id, user_id, lock=True)
        await SeatService.require_version(board, if_match)
        removed_result = await db.scalars(select(models.SeatAssignment.student_id).where(
            models.SeatAssignment.class_id == class_id, models.SeatAssignment.is_deleted.is_(False),
            or_(models.SeatAssignment.row > payload.rows, models.SeatAssignment.column > sum(payload.column_groups))))
        removed = list(removed_result.all())
        if removed:
            await db.execute(update(models.SeatAssignment).where(models.SeatAssignment.class_id == class_id,
                models.SeatAssignment.student_id.in_(removed)).values(is_deleted=True))
        board.rows, board.column_groups, board.version = payload.rows, payload.column_groups, board.version + 1
        await db.commit()
        return {"layout": {"rows": board.rows, "columnGroups": board.column_groups, "columns": sum(board.column_groups)},
                "removedStudentIds": removed, "version": board.version}

    @staticmethod
    async def move(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, student_id: uuid.UUID,
                   payload: schemas.SeatMove, if_match: Optional[str]) -> dict:
        await StudentService.get(db, class_id, user_id, student_id)
        board = await SeatService.board(db, class_id, user_id, lock=True)
        await SeatService.require_version(board, if_match)
        if payload.row > board.rows or payload.column > sum(board.column_groups):
            raise HTTPException(status_code=422, detail="座位坐标超出布局")
        source = await db.scalar(select(models.SeatAssignment).where(models.SeatAssignment.class_id == class_id,
            models.SeatAssignment.student_id == student_id, models.SeatAssignment.is_deleted.is_(False)).with_for_update())
        target = await db.scalar(select(models.SeatAssignment).where(models.SeatAssignment.class_id == class_id,
            models.SeatAssignment.row == payload.row, models.SeatAssignment.column == payload.column,
            models.SeatAssignment.is_deleted.is_(False)).with_for_update())
        if target and target.student_id != student_id and not payload.swap:
            raise _conflict("目标座位已有学生")
        old_position = (source.row, source.column) if source else None
        if target and target.student_id != student_id:
            if old_position:
                target.row, target.column = old_position
            else:
                target.is_deleted = True
        if source:
            source.row, source.column = payload.row, payload.column
        else:
            db.add(models.SeatAssignment(class_id=class_id, user_id=user_id, student_id=student_id,
                row=payload.row, column=payload.column))
        board.version += 1
        await db.commit()
        return {"studentId": student_id, "row": payload.row, "column": payload.column, "version": board.version}

    @staticmethod
    async def remove(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, student_id: Optional[uuid.UUID],
                     if_match: Optional[str]) -> dict:
        board = await SeatService.board(db, class_id, user_id, lock=True)
        await SeatService.require_version(board, if_match)
        clauses = [models.SeatAssignment.class_id == class_id, models.SeatAssignment.is_deleted.is_(False)]
        if student_id:
            clauses.append(models.SeatAssignment.student_id == student_id)
        await db.execute(update(models.SeatAssignment).where(*clauses).values(is_deleted=True))
        board.version += 1
        await db.commit()
        return {"version": board.version}

    @staticmethod
    async def batch(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, payload: schemas.SeatBatch,
                    if_match: Optional[str]) -> dict:
        board = await SeatService.board(db, class_id, user_id, lock=True)
        await SeatService.require_version(board, if_match)
        for assignment in payload.assignments:
            await StudentService.get(db, class_id, user_id, assignment.student_id)
            if assignment.row > board.rows or assignment.column > sum(board.column_groups):
                raise HTTPException(status_code=422, detail="座位坐标超出布局")
        if payload.mode == "replace":
            await db.execute(update(models.SeatAssignment).where(
                models.SeatAssignment.class_id == class_id,
                models.SeatAssignment.is_deleted.is_(False),
            ).values(is_deleted=True))
        else:
            student_ids = [item.student_id for item in payload.assignments]
            positions = [(item.row, item.column) for item in payload.assignments]
            existing = list((await db.scalars(select(models.SeatAssignment).where(
                models.SeatAssignment.class_id == class_id,
                models.SeatAssignment.is_deleted.is_(False),
                or_(
                    models.SeatAssignment.student_id.in_(student_ids),
                    tuple_(models.SeatAssignment.row, models.SeatAssignment.column).in_(positions),
                ),
            ))).all())
            for item in existing:
                item.is_deleted = True
        await db.flush()
        for assignment in payload.assignments:
            db.add(models.SeatAssignment(class_id=class_id, user_id=user_id,
                student_id=assignment.student_id, row=assignment.row, column=assignment.column))
        board.version += 1
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _conflict("批量座位与现有分配冲突") from exc
        return {"assignments": payload.assignments, "version": board.version}


class SummaryService:
    @staticmethod
    async def finance(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, date_from: Optional[date], date_to: Optional[date]):
        await ClassService.require(db, class_id, user_id)
        clauses = [models.FinanceRecord.class_id == class_id, models.FinanceRecord.user_id == user_id,
                   models.FinanceRecord.is_deleted.is_(False)]
        if date_from: clauses.append(models.FinanceRecord.date >= date_from)
        if date_to: clauses.append(models.FinanceRecord.date <= date_to)
        rows = (await db.execute(select(models.FinanceRecord.type, func.coalesce(func.sum(models.FinanceRecord.amount), 0)).where(*clauses).group_by(models.FinanceRecord.type))).all()
        values = dict(rows)
        income, expense = Decimal(values.get("收入", 0)), Decimal(values.get("支出", 0))
        return schemas.FinanceSummary(income=income, expense=expense, balance=income - expense)

    @staticmethod
    async def training(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, date_from: Optional[date], date_to: Optional[date]):
        await ClassService.require(db, class_id, user_id)
        clauses = [models.TrainingRecord.class_id == class_id, models.TrainingRecord.user_id == user_id,
                   models.TrainingRecord.is_deleted.is_(False)]
        if date_from: clauses.append(models.TrainingRecord.date >= date_from)
        if date_to: clauses.append(models.TrainingRecord.date <= date_to)
        rows = (await db.execute(select(models.TrainingRecord.category, func.count(), func.coalesce(func.sum(models.TrainingRecord.hours), 0)).where(*clauses).group_by(models.TrainingRecord.category))).all()
        categories = {category: {"count": count, "hours": hours} for category, count, hours in rows}
        return schemas.TrainingSummary(total_hours=sum((Decimal(row[2]) for row in rows), Decimal("0")), categories=categories)

    @staticmethod
    async def dashboard(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, target: date) -> dict:
        await ClassService.require(db, class_id, user_id)
        async def count(model, *extra):
            return await db.scalar(select(func.count()).select_from(model).where(model.class_id == class_id,
                model.user_id == user_id, model.is_deleted.is_(False), *extra)) or 0
        genders = dict((await db.execute(select(models.Student.gender, func.count()).where(models.Student.class_id == class_id,
            models.Student.user_id == user_id, models.Student.is_deleted.is_(False)).group_by(models.Student.gender))).all())
        return {"studentSummary": {"total": sum(genders.values()), "male": genders.get("男", 0), "female": genders.get("女", 0)},
            "leaveToday": await count(models.LeaveRequest, models.LeaveRequest.date == target),
            "unsubmittedHomework": await db.scalar(select(func.coalesce(func.sum(models.HomeworkRecord.unsubmitted), 0)).where(models.HomeworkRecord.class_id == class_id, models.HomeworkRecord.date == target, models.HomeworkRecord.is_deleted.is_(False))),
            "violationCount": await count(models.Violation), "workRecordsThisMonth": await count(
                models.WorkRecord,
                func.extract("year", models.WorkRecord.date) == target.year,
                func.extract("month", models.WorkRecord.date) == target.month,
            ),
            "pendingTodoCount": await count(models.Todo, models.Todo.status == "待完成"),
            "alertSummary": {
                "emotion": await count(models.Alert, models.Alert.type == "情绪预警", models.Alert.status != "已关闭"),
                "specialHealth": await count(models.Alert, models.Alert.type == "特殊体质", models.Alert.status != "已关闭"),
                "dropoutRisk": await count(models.Alert, models.Alert.type == "辍学风险", models.Alert.status != "已关闭"),
                "notReturned": await count(models.Alert, models.Alert.type == "未返校", models.Alert.status != "已关闭"),
                "pending": await count(models.Alert, models.Alert.status != "已关闭"),
            },
            "highRiskStudents": [], "upcomingTodos": [], "latestExam": None, "recentWorkRecords": []}


class PreferenceService:
    @staticmethod
    async def get(db: AsyncSession, user_id: uuid.UUID) -> models.UiPreference:
        item = await db.scalar(select(models.UiPreference).where(models.UiPreference.user_id == user_id,
            models.UiPreference.is_deleted.is_(False)))
        if not item:
            item = models.UiPreference(user_id=user_id, skin="mr")
            db.add(item)
            await db.commit(); await db.refresh(item)
        return item

    @staticmethod
    async def update(db: AsyncSession, user_id: uuid.UUID, skin: str) -> models.UiPreference:
        item = await PreferenceService.get(db, user_id)
        item.skin = skin
        await db.commit(); await db.refresh(item)
        return item


BACKUP_MODELS = [models.Student, models.SeatBoard, models.SeatAssignment, *RESOURCE_MODELS.values()]


def _json_value(value: Any) -> Any:
    if isinstance(value, (uuid.UUID, date, datetime, Decimal)):
        return str(value)
    return value


class BackupService:
    @staticmethod
    async def export(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> dict:
        owned_class = await ClassService.require(db, class_id, user_id)
        resources: dict[str, list[dict]] = {}
        for model in BACKUP_MODELS:
            rows = list((await db.scalars(select(model).where(
                model.class_id == class_id, model.user_id == user_id, model.is_deleted.is_(False)
            ))).all())
            resources[model.__tablename__] = [
                {column.name: _json_value(getattr(row, column.name)) for column in model.__table__.columns
                 if column.name not in {"user_id", "class_id", "is_deleted"}}
                for row in rows
            ]
        await BackupService.audit(db, class_id, user_id, "backup_export", {})
        return {"schemaVersion": 1, "exportedAt": datetime.now(timezone.utc).isoformat(),
                "class": {"id": str(owned_class.id), "name": owned_class.name, "timezone": owned_class.timezone},
                "resources": resources}

    @staticmethod
    def validate(payload: dict) -> dict:
        if payload.get("schemaVersion") != 1 or not isinstance(payload.get("resources"), dict):
            raise HTTPException(status_code=400, detail="备份版本或结构不受支持")
        known = {model.__tablename__ for model in BACKUP_MODELS}
        unknown = set(payload["resources"]) - known
        if unknown:
            raise HTTPException(status_code=400, detail=f"备份包含未知资源: {sorted(unknown)}")
        students = {row.get("id") for row in payload["resources"].get(models.Student.__tablename__, [])}
        for model in STUDENT_MODELS + (models.SeatAssignment,):
            for row in payload["resources"].get(model.__tablename__, []):
                if row.get("student_id") not in students:
                    raise HTTPException(status_code=400, detail=f"{model.__tablename__} 包含无效学生引用")
        return {"valid": True, "resourceCounts": {name: len(rows) for name, rows in payload["resources"].items()}}

    @staticmethod
    async def restore(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, payload: dict, mode: str) -> dict:
        await ClassService.require(db, class_id, user_id)
        summary = BackupService.validate(payload)
        if payload.get("class", {}).get("id") != str(class_id):
            raise HTTPException(status_code=409, detail="备份仅允许恢复到原班级")
        if mode == "replace":
            for model in BACKUP_MODELS:
                await db.execute(update(model).where(model.class_id == class_id, model.user_id == user_id,
                    model.is_deleted.is_(False)).values(is_deleted=True))
        by_table = {model.__tablename__: model for model in BACKUP_MODELS}
        for table_name, rows in payload["resources"].items():
            model = by_table[table_name]
            for raw in rows:
                data = dict(raw)
                existing = await db.get(model, uuid.UUID(data["id"]))
                if existing and (existing.class_id != class_id or existing.user_id != user_id):
                    raise HTTPException(status_code=409, detail="备份记录 ID 与其他班级资源冲突")
                for column in model.__table__.columns:
                    if column.name not in data or data[column.name] is None:
                        continue
                    if isinstance(column.type, PG_UUID):
                        data[column.name] = uuid.UUID(data[column.name])
                    elif isinstance(column.type, SADate) and not isinstance(column.type, SADateTime):
                        data[column.name] = date.fromisoformat(data[column.name])
                    elif isinstance(column.type, SADateTime):
                        data[column.name] = datetime.fromisoformat(data[column.name])
                    elif isinstance(column.type, Numeric):
                        data[column.name] = Decimal(data[column.name])
                data.update(class_id=class_id, user_id=user_id, is_deleted=False)
                await db.merge(model(**data))
        db.add(models.AuditLog(class_id=class_id, user_id=user_id, action="backup_restore",
                               detail={"mode": mode, **summary}))
        await db.commit()
        return summary

    @staticmethod
    async def clear(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID) -> None:
        await ClassService.require(db, class_id, user_id)
        for model in BACKUP_MODELS:
            await db.execute(update(model).where(model.class_id == class_id, model.user_id == user_id,
                model.is_deleted.is_(False)).values(is_deleted=True))
        db.add(models.AuditLog(class_id=class_id, user_id=user_id, action="data_clear", detail={}))
        await db.commit()

    @staticmethod
    async def audit(db: AsyncSession, class_id: uuid.UUID, user_id: uuid.UUID, action: str, detail: dict) -> None:
        db.add(models.AuditLog(class_id=class_id, user_id=user_id, action=action, detail=detail))
        await db.commit()
