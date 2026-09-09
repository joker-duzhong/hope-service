import asyncio
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import HTTPException

from apps.teacher_logbook import models, schemas
from apps.teacher_logbook.services import BackupService, ClassService, CrudService, SeatService, StudentService, SummaryService


def test_date_and_keyword_filters_reach_the_database():
    db = SimpleNamespace(scalar=AsyncMock(return_value=0), scalars=AsyncMock(return_value=SimpleNamespace(all=lambda: [])))
    with patch.object(ClassService, "require", new=AsyncMock()):
        asyncio.run(CrudService.list(db, "work-records", uuid4(), uuid4(), 1, 20,
            {"date_from": date(2026, 9, 1), "date_to": date(2026, 9, 30), "keyword": "班会"}))
    statement = db.scalars.call_args.args[0]
    clauses = " ".join(str(clause) for clause in statement._where_criteria)
    assert "date >=" in clauses and "date <=" in clauses
    assert "title" in clauses and "note" in clauses and "LIKE" in clauses
    assert len(statement._order_by_clauses) == 2


def test_seat_swap_releases_both_unique_positions_before_reassigning():
    source = SimpleNamespace(student_id=uuid4(), row=1, column=1, is_deleted=False)
    target = SimpleNamespace(student_id=uuid4(), row=1, column=2, is_deleted=False)
    board = SimpleNamespace(rows=2, column_groups=[2], version=3)
    async def flush():
        assert source.is_deleted and target.is_deleted
        assert source.column == 1 and target.column == 2
    db = SimpleNamespace(scalar=AsyncMock(side_effect=[source, target]), flush=AsyncMock(side_effect=flush), commit=AsyncMock())
    with patch.object(StudentService, "get", new=AsyncMock()), patch.object(SeatService, "board", new=AsyncMock(return_value=board)):
        result = asyncio.run(SeatService.move(db, uuid4(), uuid4(), source.student_id,
            schemas.SeatMove(row=1, column=2, swap=True), '"seat-board-3"'))
    assert source.column == 2 and target.column == 1
    assert not source.is_deleted and not target.is_deleted
    assert result["version"] == 4
    db.flush.assert_awaited_once()


def test_stale_seat_version_never_writes():
    db = SimpleNamespace(commit=AsyncMock())
    board = SimpleNamespace(version=4)
    with patch.object(SeatService, "board", new=AsyncMock(return_value=board)), pytest.raises(HTTPException) as error:
        asyncio.run(SeatService.remove(db, uuid4(), uuid4(), None, '"seat-board-3"'))
    assert error.value.status_code == 412
    db.commit.assert_not_awaited()


@pytest.mark.parametrize("resources", [
    {"teacher_logbook_students": "not an array"},
    {"teacher_logbook_students": [{"id": "bad", "name": "学生", "gender": "男"}]},
    {"teacher_logbook_courses": [{"id": str(uuid4()), "course": "语文", "teacher": "教师", "day": "周一", "start_time": "10:00", "end_time": "09:00"}]},
    {"teacher_logbook_students": [{"id": str(uuid4()), "name": "学生", "gender": "男", "user_id": str(uuid4())}]},
])
def test_backup_validation_rejects_malformed_data_before_restore(resources):
    with pytest.raises(HTTPException) as error:
        BackupService.validate({"schemaVersion": 1, "resources": resources})
    assert error.value.status_code == 400


def test_backup_restore_rolls_back_cross_class_id_conflicts():
    class_id = uuid4()
    user_id = uuid4()
    payload = {"schemaVersion": 1, "class": {"id": str(class_id)}, "resources": {
        "teacher_logbook_students": [{"id": str(uuid4()), "name": "学生", "gender": "男"}]}}
    db = SimpleNamespace(execute=AsyncMock(), get=AsyncMock(return_value=SimpleNamespace(class_id=uuid4(), user_id=user_id)), rollback=AsyncMock(), commit=AsyncMock())
    with patch.object(ClassService, "require", new=AsyncMock()), pytest.raises(HTTPException) as error:
        asyncio.run(BackupService.restore(db, class_id, user_id, payload, "replace"))
    assert error.value.status_code == 409
    db.rollback.assert_awaited_once()
    db.commit.assert_not_awaited()


def test_dashboard_returns_queried_records_instead_of_empty_placeholders():
    alert, todo, exam, work = [SimpleNamespace(id=uuid4()) for _ in range(4)]
    results = [SimpleNamespace(all=lambda row=row: [row]) for row in (alert, todo, exam, work)]
    db = SimpleNamespace(scalars=AsyncMock(side_effect=results), scalar=AsyncMock(return_value=1),
        execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [("男", 2), ("女", 3)])))
    with patch.object(ClassService, "require", new=AsyncMock()):
        response = asyncio.run(SummaryService.dashboard(db, uuid4(), uuid4(), date(2026, 9, 9)))
    assert response["studentSummary"]["total"] == 5
    assert response["highRiskStudents"] == [alert]
    assert response["upcomingTodos"] == [todo]
    assert response["latestExam"] is exam
    assert response["recentWorkRecords"] == [work]
    for call in db.scalars.call_args_list:
        assert "user_id" in " ".join(str(clause) for clause in call.args[0]._where_criteria)


def test_referenced_committee_roles_cannot_be_deleted():
    role = SimpleNamespace(is_deleted=False)
    db = SimpleNamespace(scalar=AsyncMock(return_value=1), commit=AsyncMock())
    with patch.object(CrudService, "get", new=AsyncMock(return_value=role)), pytest.raises(HTTPException) as error:
        asyncio.run(CrudService.remove(db, "committee-roles", uuid4(), uuid4(), uuid4()))
    assert error.value.status_code == 409
    assert not role.is_deleted
    db.commit.assert_not_awaited()
