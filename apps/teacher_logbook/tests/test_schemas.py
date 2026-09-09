from datetime import time
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.teacher_logbook import schemas
from apps.teacher_logbook.services import BackupService, SeatService


def test_layout_rejects_excessive_total_columns() -> None:
    with pytest.raises(ValidationError):
        schemas.LayoutUpdate(rows=12, columnGroups=[10, 10, 10, 1])


def test_seat_batch_rejects_duplicate_students_and_positions() -> None:
    student_id = uuid4()
    with pytest.raises(ValidationError):
        schemas.SeatBatch(assignments=[
            {"studentId": student_id, "row": 1, "column": 1},
            {"studentId": student_id, "row": 1, "column": 2},
        ])


def test_course_end_time_must_be_later() -> None:
    with pytest.raises(ValidationError):
        schemas.CourseData(course="语文", teacher="王老师", day="周一",
                           startTime=time(9, 0), endTime=time(8, 0))


def test_link_rejects_dangerous_protocol() -> None:
    with pytest.raises(ValidationError):
        schemas.LinkData(title="危险链接", url="javascript:alert(1)")


def test_if_match_parses_seat_board_version() -> None:
    assert SeatService.parse_version('"seat-board-7"') == 7
    with pytest.raises(HTTPException) as exc_info:
        SeatService.parse_version(None)
    assert exc_info.value.status_code == 428


def test_backup_validation_rejects_broken_student_reference() -> None:
    payload = {
        "schemaVersion": 1,
        "resources": {
            "teacher_logbook_students": [],
            "teacher_logbook_talks": [{"student_id": str(uuid4())}],
        },
    }
    with pytest.raises(HTTPException) as exc_info:
        BackupService.validate(payload)
    assert exc_info.value.status_code == 400


def test_backup_validation_returns_counts() -> None:
    student_id = str(uuid4())
    payload = {
        "schemaVersion": 1,
        "resources": {
            "teacher_logbook_students": [{"id": student_id, "name": "测试学生", "gender": "男"}],
            "teacher_logbook_talks": [{"id": str(uuid4()), "student_id": student_id, "date": "2026-09-09", "note": "谈话记录"}],
        },
    }
    result = BackupService.validate(payload)
    assert result["valid"] is True
    assert result["resourceCounts"]["teacher_logbook_talks"] == 1
