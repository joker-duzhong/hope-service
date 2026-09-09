import pytest
from fastapi import HTTPException
from apps.teacher_logbook.migration import prepare_legacy
from apps.teacher_logbook.services import _json_value
from datetime import time


def test_migration_maps_student_ids_and_course_times():
    plan = prepare_legacy({"students": [{"id": "old-id", "name": "学生", "gender": "男"}],
                           "leave": [{"student": "学生", "reason": "病假", "date": "2026-09-09"}],
                           "courses": [{"course": "语文", "teacher": "教师", "day": "周一", "time": "08:00-08:45"}]})
    student_id = plan.records[0][1]["id"]
    assert plan.records[1][1]["student_id"] == student_id
    assert plan.records[2][1]["start_time"] == time(8, 0)
    assert plan.counts == {"students": 1, "leave": 1, "courses": 1}


def test_migration_rejects_ambiguous_names_and_unknown_collections():
    with pytest.raises(HTTPException):
        prepare_legacy({"students": [{"name": "同名", "gender": "男"}, {"name": "同名", "gender": "女"}],
                        "leave": [{"student": "同名", "reason": "事假", "date": "2026-09-09"}]})
    with pytest.raises(HTTPException):
        prepare_legacy({"students": [], "unknown": []})


def test_migration_preserves_explicit_identity_for_duplicate_names():
    plan = prepare_legacy({"students": [{"id": "one", "name": "同名", "gender": "男"}, {"id": "two", "name": "同名", "gender": "女"}],
                           "seatBoard": {"rows": 1, "columnGroups": [2], "placements": [{"studentId": "two", "row": 1, "column": 2}]}})
    assert plan.records[-1][1]["student_id"] == plan.records[1][1]["id"]


def test_backups_serialize_course_times():
    assert _json_value(time(8, 30)) == "08:30:00"
