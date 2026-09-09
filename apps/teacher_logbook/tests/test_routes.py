from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from apps.teacher_logbook import schemas
from apps.teacher_logbook.router import get_current_user, get_db, router
from apps.teacher_logbook.services import CrudService, RESOURCE_MODELS

STUDENT_ID = str(uuid4())
CASES = {
    "leave-requests": {"studentId": STUDENT_ID, "reason": "病假", "date": "2026-09-09"},
    "homework-records": {"subject": "数学", "title": "作业", "unsubmitted": 2, "date": "2026-09-09"},
    "violations": {"studentId": STUDENT_ID, "type": "迟到", "date": "2026-09-09"},
    "alerts": {"studentId": STUDENT_ID, "type": "情绪预警", "level": "高", "status": "待处理"},
    "todos": {"title": "班会", "status": "待完成"},
    "work-records": {"title": "班会", "date": "2026-09-09"},
    "exams": {"subject": "数学", "name": "月考", "average": "82.50", "date": "2026-09-09"},
    "committee-roles": {"role": "班长", "duty": "班级事务"},
    "committee-members": {"studentId": STUDENT_ID, "roleId": str(uuid4())},
    "hygiene-assignments": {"studentId": STUDENT_ID, "area": "教室", "day": "周一"},
    "activities": {"title": "班级活动", "date": "2026-09-09"},
    "finance-records": {"type": "收入", "amount": "12.50", "note": "班费", "date": "2026-09-09"},
    "awards": {"studentId": STUDENT_ID, "type": "表扬", "note": "课堂表现", "date": "2026-09-09"},
    "courses": {"course": "语文", "teacher": "王老师", "day": "周一", "startTime": "08:00:00", "endTime": "08:45:00"},
    "talks": {"studentId": STUDENT_ID, "date": "2026-09-09", "note": "学习情况"},
    "contacts": {"studentId": STUDENT_ID, "date": "2026-09-09", "note": "学习情况", "method": "家访"},
    "training-records": {"category": "培训", "title": "班主任培训", "hours": "2.50", "date": "2026-09-09"},
    "links": {"title": "教学资源", "url": "https://example.test/"},
}


@pytest.mark.parametrize("resource", CASES)
def test_resource_roundtrip_contract_for_both_update_methods(resource):
    class_id, item_id, user_id = uuid4(), uuid4(), uuid4()
    values = schemas.RESOURCE_SCHEMAS[resource].model_validate(CASES[resource]).model_dump()
    if resource == "links":
        values["url"] = str(values["url"])
    item = SimpleNamespace(__table__=RESOURCE_MODELS[resource].__table__, id=item_id, class_id=class_id,
        user_id=user_id, is_deleted=False, created_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc), **values)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=user_id)
    collection = f"/classes/{class_id}/{resource}"
    with TestClient(app) as client, patch.object(CrudService, "list", new=AsyncMock(return_value=([item], 1))), \
            patch.object(CrudService, "get", new=AsyncMock(return_value=item)), \
            patch.object(CrudService, "create", new=AsyncMock(return_value=item)), \
            patch.object(CrudService, "update", new=AsyncMock(return_value=item)), \
            patch.object(CrudService, "remove", new=AsyncMock()):
        listed = client.get(collection)
        assert listed.status_code == 200
        assert listed.json()["data"]["total"] == 1
        for method, path, body in [("post", collection, CASES[resource]), ("get", f"{collection}/{item_id}", None),
                ("post", f"{collection}/{item_id}", CASES[resource]), ("patch", f"{collection}/{item_id}", CASES[resource])]:
            response = client.request(method, path, **({"json": body} if body else {}))
            assert response.status_code in {200, 201}, response.text
            assert response.json()["data"]["classId"] == str(class_id)
            assert "userId" not in response.json()["data"]
        assert client.delete(f"{collection}/{item_id}").status_code == 204


def test_compatible_update_preserves_auth_dependency():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: None
    def deny():
        raise HTTPException(401, "unauthorized")
    app.dependency_overrides[get_current_user] = deny
    with TestClient(app) as client, patch.object(CrudService, "update", new=AsyncMock()) as update:
        response = client.post(f"/classes/{uuid4()}/todos/{uuid4()}", json={"status": "已完成"})
        assert response.status_code == 401
        update.assert_not_awaited()
