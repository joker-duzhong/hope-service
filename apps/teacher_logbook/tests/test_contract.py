import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI

from apps.teacher_logbook import schemas
from apps.teacher_logbook.router import router
from apps.teacher_logbook.services import ClassService, StudentService


def test_every_resource_has_typed_responses_and_compatible_update():
    app = FastAPI()
    app.include_router(router)
    paths = app.openapi()["paths"]
    for resource in schemas.RESOURCE_SCHEMAS:
        collection = paths[f"/classes/{{class_id}}/{resource}"]
        item = paths[f"/classes/{{class_id}}/{resource}/{{item_id}}"]
        assert {"get", "post", "patch", "delete"} <= item.keys()
        assert "$ref" in collection["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert "$ref" in item["post"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert "post" in paths["/classes/{class_id}/students/{student_id}"]
    assert "post" in paths["/users/me/preferences/ui"]


def test_student_export_does_not_truncate_at_one_hundred():
    students = [SimpleNamespace(id=uuid4()) for _ in range(125)]
    result = SimpleNamespace(all=lambda: students)
    db = SimpleNamespace(scalars=AsyncMock(return_value=result))
    with patch.object(ClassService, "require", new=AsyncMock()):
        exported = asyncio.run(StudentService.export_rows(db, uuid4(), uuid4()))
    assert exported == students
    statement = db.scalars.call_args.args[0]
    assert statement._limit_clause is None
    assert "user_id" in " ".join(str(clause) for clause in statement._where_criteria)


def test_dashboard_schema_uses_real_records():
    definition = schemas.DashboardRead.model_json_schema(by_alias=True)
    assert "$ref" in definition["properties"]["highRiskStudents"]["items"]
    assert "$ref" in definition["properties"]["recentWorkRecords"]["items"]
