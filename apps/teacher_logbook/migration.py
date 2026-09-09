"""Explicit migration of the archived JavaScript data into an empty class."""
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select

from apps.teacher_logbook import models, schemas
from apps.teacher_logbook.services import ClassService, RESOURCE_MODELS


LEGACY_RESOURCES = {
    "leave": "leave-requests", "homework": "homework-records", "violations": "violations",
    "alerts": "alerts", "todos": "todos", "work": "work-records", "exams": "exams",
    "classCommittee": "committee-members", "hygiene": "hygiene-assignments",
    "activities": "activities", "finance": "finance-records", "awards": "awards",
    "courses": "courses", "talks": "talks", "contacts": "contacts",
    "training": "training-records", "links": "links",
}


@dataclass
class MigrationPlan:
    records: list[tuple[type, dict]]
    layout: schemas.LayoutUpdate | None
    counts: dict[str, int]


def prepare_legacy(payload: dict) -> MigrationPlan:
    if not isinstance(payload, dict) or not isinstance(payload.get("students"), list):
        raise HTTPException(400, "请选择原生 JavaScript 版本导出的 JSON")
    unknown = set(payload) - set(LEGACY_RESOURCES) - {"students", "committeeRoles", "seatBoard", "seats"}
    if unknown:
        raise HTTPException(400, "旧数据包含未知集合，请先核对导出版本")
    if payload.get("seats"):
        raise HTTPException(400, "旧座位文字记录尚未转换为座位板，请在原版核对后再迁移")
    records = []
    counts = {}
    names = defaultdict(list)
    student_ids = {}
    roles = {}

    def collection(key):
        value = payload.get(key, [])
        if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
            raise HTTPException(400, f"{key} 必须是记录数组")
        return value

    def add(key, model, schema, raw, number):
        try:
            values = schema.model_validate(raw).model_dump(mode="python")
        except (ValidationError, ValueError, TypeError) as exc:
            raise HTTPException(422, f"{key} 第 {number} 条字段不符合新接口，请修正后重试") from exc
        if model is models.Link:
            values["url"] = str(values["url"])
        values["id"] = uuid.uuid4()
        records.append((model, values))
        counts[key] = counts.get(key, 0) + 1
        return values

    for number, raw in enumerate(collection("students"), 1):
        values = add("students", models.Student, schemas.StudentCreate, raw, number)
        names[values["name"]].append(values["id"])
        if raw.get("id"):
            if str(raw["id"]) in student_ids:
                raise HTTPException(422, "旧学生 ID 重复，无法安全迁移")
            student_ids[str(raw["id"])] = values["id"]
    for number, raw in enumerate(collection("committeeRoles"), 1):
        values = add("committeeRoles", models.CommitteeRole, schemas.CommitteeRoleData, raw, number)
        if values["role"] in roles:
            raise HTTPException(422, "职位名称重复，请先合并职位")
        roles[values["role"]] = values["id"]

    def student(raw):
        identifier = raw.get("studentId")
        if identifier:
            if str(identifier) not in student_ids:
                raise HTTPException(422, "记录引用了不存在的学生 ID")
            return student_ids[str(identifier)]
        matches = names.get(raw.get("student"), [])
        if len(matches) != 1:
            raise HTTPException(422, "存在同名或缺失学生引用，请在旧记录中补齐 studentId")
        return matches[0]

    for key, resource in LEGACY_RESOURCES.items():
        schema = schemas.RESOURCE_SCHEMAS[resource]
        for number, original in enumerate(collection(key), 1):
            raw = dict(original)
            if "student_id" in schema.model_fields:
                raw["studentId"] = student(raw)
            if resource == "committee-members":
                if raw.get("role") not in roles:
                    raise HTTPException(422, "班委引用了不存在的职位")
                raw["roleId"] = roles[raw["role"]]
            if resource == "courses":
                value = str(raw.get("time", ""))
                day = re.search(r"(?:星期|周)[一二三四五六日天]", value)
                if not raw.get("day") and day:
                    raw["day"] = day[0].replace("星期", "周").replace("周天", "周日")
                times = re.findall(r"\d{1,2}:\d{2}", value)
                if len(times) == 2:
                    raw["startTime"], raw["endTime"] = times
            add(key, RESOURCE_MODELS[resource], schema, raw, number)
    layout = None
    board = payload.get("seatBoard")
    if board:
        if not isinstance(board, dict):
            raise HTTPException(400, "座位板结构无效")
        groups = board.get("columnGroups")
        if groups is None:
            columns = board.get("columns", 30)
            if not isinstance(columns, int) or not 1 <= columns <= 30:
                raise HTTPException(422, "座位列数无效")
            groups = [2] * (columns // 2) + ([1] if columns % 2 else [])
        try:
            layout = schemas.LayoutUpdate(rows=board.get("rows", 30), columnGroups=groups)
            placements = board.get("placements", [])
            if not isinstance(placements, list):
                raise ValueError("placements")
            seats = schemas.SeatBatch(assignments=[{"studentId": student(raw), "row": raw["row"], "column": raw["column"]} for raw in placements])
            for seat in seats.assignments:
                if not 1 <= seat.row <= layout.rows or not 1 <= seat.column <= sum(layout.column_groups):
                    raise ValueError("bounds")
                records.append((models.SeatAssignment, {"id": uuid.uuid4(), **seat.model_dump(exclude={"student_name"})}))
            counts["seatAssignments"] = len(seats.assignments)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, "座位布局或学生位置无效") from exc
    return MigrationPlan(records, layout, counts)


async def import_legacy(db, class_id, user_id, payload, dry_run):
    await ClassService.require(db, class_id, user_id)
    plan = prepare_legacy(payload)
    if dry_run:
        return {"valid": True, "resourceCounts": plan.counts}
    await db.scalar(select(models.OwnedClass).where(models.OwnedClass.id == class_id,
        models.OwnedClass.user_id == user_id, models.OwnedClass.is_deleted.is_(False)).with_for_update())
    for model in [models.Student, *RESOURCE_MODELS.values(), models.SeatAssignment]:
        count = await db.scalar(select(func.count()).select_from(model).where(
            model.class_id == class_id, model.user_id == user_id, model.is_deleted.is_(False)))
        if count:
            raise HTTPException(409, "旧版数据仅允许迁入空班级，请先创建新班级")
    try:
        for model, values in plan.records:
            db.add(model(class_id=class_id, user_id=user_id, **values))
        if plan.layout:
            board = await db.scalar(select(models.SeatBoard).where(models.SeatBoard.class_id == class_id, models.SeatBoard.user_id == user_id))
            if not board:
                board = models.SeatBoard(class_id=class_id, user_id=user_id, version=1)
                db.add(board)
            board.rows = plan.layout.rows
            board.column_groups = plan.layout.column_groups
            board.is_deleted = False
            board.version = (board.version or 0) + 1
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    return {"valid": True, "resourceCounts": plan.counts}
