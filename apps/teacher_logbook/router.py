"""Teacher Logbook HTTP routes."""
import csv
import io
import json
import uuid
from datetime import date
from math import ceil
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Header, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from apps.teacher_logbook import models, schemas, services
from core.database import get_db
from core.response import PaginatedData, PaginatedResponse, ResponseModel
from core.users.dependencies import get_current_user
from core.users.models import User

router = APIRouter()


def dump(item: Any) -> dict[str, Any]:
    return {schemas.to_camel(column.name): getattr(item, column.name) for column in item.__table__.columns
            if column.name not in {"is_deleted", "user_id"}}


@router.get("/classes", response_model=ResponseModel[list[schemas.ClassRead]])
async def list_classes(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.ClassService.list(db, user.id))


@router.post("/classes", response_model=ResponseModel[schemas.ClassRead], status_code=status.HTTP_201_CREATED)
async def create_class(payload: schemas.ClassCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.ClassService.create(db, user.id, payload))


@router.patch("/classes/{class_id}", response_model=ResponseModel[schemas.ClassRead])
async def update_class(class_id: uuid.UUID, payload: schemas.ClassUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.ClassService.update(db, class_id, user.id, payload))


@router.delete("/classes/{class_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_class(class_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await services.ClassService.remove(db, class_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/classes/{class_id}/students", response_model=PaginatedResponse[schemas.StudentRead])
async def list_students(class_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100),
                        keyword: Optional[str] = None, gender: Optional[str] = None, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    items, total = await services.StudentService.list(db, class_id, user.id, page, page_size, keyword, gender)
    return PaginatedResponse(data=PaginatedData(items=items, total=total, page=page, page_size=page_size, total_pages=ceil(total / page_size)))


@router.post("/classes/{class_id}/students", response_model=ResponseModel[schemas.StudentRead], status_code=status.HTTP_201_CREATED)
async def create_student(class_id: uuid.UUID, payload: schemas.StudentCreate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.StudentService.create(db, class_id, user.id, payload))


@router.get("/classes/{class_id}/students/export")
async def export_students(class_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    items, _ = await services.StudentService.list(db, class_id, user.id, 1, 100, None, None)
    output = io.StringIO(); writer = csv.writer(output); writer.writerow(["name", "gender", "contact"])
    writer.writerows((item.name, item.gender, item.contact or "") for item in items)
    content = "\ufeff" + output.getvalue()
    return StreamingResponse(iter([content.encode("utf-8")]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="students.csv"'})


@router.post("/classes/{class_id}/students/import", response_model=ResponseModel[schemas.ImportResult])
async def import_students(class_id: uuid.UUID, file: UploadFile = File(...), dry_run: bool = Form(False, alias="dryRun"),
                          duplicate_strategy: str = Form("skip", alias="duplicateStrategy", pattern="^(skip|create)$"),
                          db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.StudentService.import_csv(db, class_id, user.id, await file.read(), dry_run, duplicate_strategy))


@router.get("/classes/{class_id}/students/{student_id}", response_model=ResponseModel[schemas.StudentRead])
async def get_student(class_id: uuid.UUID, student_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.StudentService.get(db, class_id, user.id, student_id))


@router.patch("/classes/{class_id}/students/{student_id}", response_model=ResponseModel[schemas.StudentRead])
async def update_student(class_id: uuid.UUID, student_id: uuid.UUID, payload: schemas.StudentUpdate,
                         db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.StudentService.update(db, class_id, user.id, student_id, payload))


@router.delete("/classes/{class_id}/students/{student_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_student(class_id: uuid.UUID, student_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await services.StudentService.remove(db, class_id, user.id, student_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/classes/{class_id}/seat-board")
async def get_seat_board(class_id: uuid.UUID, response: Response, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    data, etag = await services.SeatService.get(db, class_id, user.id); response.headers["ETag"] = etag
    return ResponseModel(data=data)


@router.put("/classes/{class_id}/seat-board/layout")
async def update_seat_layout(class_id: uuid.UUID, payload: schemas.LayoutUpdate, if_match: Optional[str] = Header(None, alias="If-Match"),
                             db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SeatService.layout(db, class_id, user.id, payload, if_match))


@router.put("/classes/{class_id}/seat-board/assignments/{student_id}")
async def move_student(class_id: uuid.UUID, student_id: uuid.UUID, payload: schemas.SeatMove,
                       if_match: Optional[str] = Header(None, alias="If-Match"), db: AsyncSession = Depends(get_db),
                       user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SeatService.move(db, class_id, user.id, student_id, payload, if_match))


@router.delete("/classes/{class_id}/seat-board/assignments/{student_id}")
async def remove_student_seat(class_id: uuid.UUID, student_id: uuid.UUID, if_match: Optional[str] = Header(None, alias="If-Match"),
                              db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SeatService.remove(db, class_id, user.id, student_id, if_match))


@router.delete("/classes/{class_id}/seat-board/assignments")
async def clear_seats(class_id: uuid.UUID, if_match: Optional[str] = Header(None, alias="If-Match"),
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SeatService.remove(db, class_id, user.id, None, if_match))


@router.put("/classes/{class_id}/seat-board/assignments")
async def batch_seats(class_id: uuid.UUID, payload: schemas.SeatBatch,
                      if_match: Optional[str] = Header(None, alias="If-Match"),
                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SeatService.batch(db, class_id, user.id, payload, if_match))


@router.get("/classes/{class_id}/dashboard")
async def dashboard(class_id: uuid.UUID, target_date: date = Query(default_factory=date.today, alias="date"),
                    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SummaryService.dashboard(db, class_id, user.id, target_date))


@router.get("/classes/{class_id}/finance-summary")
async def finance_summary(class_id: uuid.UUID, date_from: Optional[date] = Query(None, alias="dateFrom"),
                          date_to: Optional[date] = Query(None, alias="dateTo"), db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SummaryService.finance(db, class_id, user.id, date_from, date_to))


@router.get("/classes/{class_id}/training-summary")
async def training_summary(class_id: uuid.UUID, date_from: Optional[date] = Query(None, alias="dateFrom"),
                           date_to: Optional[date] = Query(None, alias="dateTo"), db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.SummaryService.training(db, class_id, user.id, date_from, date_to))


@router.patch("/classes/{class_id}/alerts/{item_id}/status")
async def update_alert_status(class_id: uuid.UUID, item_id: uuid.UUID, payload: schemas.StatusUpdate,
                              db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    validated = schemas.RESOURCE_UPDATE_SCHEMAS["alerts"].model_validate(payload.model_dump(exclude_none=True))
    return ResponseModel(data=dump(await services.CrudService.update(db, "alerts", class_id, user.id, item_id, validated)))


@router.patch("/classes/{class_id}/todos/{item_id}/status")
async def update_todo_status(class_id: uuid.UUID, item_id: uuid.UUID, payload: schemas.StatusUpdate,
                             db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    validated = schemas.RESOURCE_UPDATE_SCHEMAS["todos"].model_validate({"status": payload.status})
    return ResponseModel(data=dump(await services.CrudService.update(db, "todos", class_id, user.id, item_id, validated)))


@router.get("/users/me/preferences/ui", response_model=ResponseModel[schemas.UiPreferenceRead])
async def get_ui_preference(db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.PreferenceService.get(db, user.id))


@router.patch("/users/me/preferences/ui", response_model=ResponseModel[schemas.UiPreferenceRead])
async def update_ui_preference(payload: schemas.UiPreferenceUpdate, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    return ResponseModel(data=await services.PreferenceService.update(db, user.id, payload.skin))


async def read_json_upload(file: UploadFile) -> dict:
    content = await file.read()
    if len(content) > 10 * 1024 * 1024:
        from fastapi import HTTPException
        raise HTTPException(status_code=413, detail="备份文件不能超过 10 MB")
    try:
        return json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="备份文件必须是 UTF-8 JSON") from exc


@router.get("/classes/{class_id}/backup")
async def export_backup(class_id: uuid.UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    payload = await services.BackupService.export(db, class_id, user.id)
    content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return StreamingResponse(iter([content]), media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="teacher-logbook-backup.json"'})


@router.post("/classes/{class_id}/backup/validate")
async def validate_backup(class_id: uuid.UUID, file: UploadFile = File(...), db: AsyncSession = Depends(get_db),
                          user: User = Depends(get_current_user)):
    await services.ClassService.require(db, class_id, user.id)
    return ResponseModel(data=services.BackupService.validate(await read_json_upload(file)))


@router.post("/classes/{class_id}/backup/restore")
async def restore_backup(class_id: uuid.UUID, file: UploadFile = File(...), mode: str = Form(..., pattern="^(replace|merge)$"),
                         confirmation: str = Form(..., pattern="^RESTORE_CLASS_DATA$"), db: AsyncSession = Depends(get_db),
                         user: User = Depends(get_current_user)):
    payload = await read_json_upload(file)
    return ResponseModel(data=await services.BackupService.restore(db, class_id, user.id, payload, mode))


@router.post("/classes/{class_id}/data/clear")
async def clear_class_data(class_id: uuid.UUID, payload: schemas.ClearDataRequest, db: AsyncSession = Depends(get_db),
                           user: User = Depends(get_current_user)):
    await services.BackupService.clear(db, class_id, user.id)
    return ResponseModel(data=True)


def register_crud_routes() -> None:
    for resource, create_schema in schemas.RESOURCE_SCHEMAS.items():
        update_schema = schemas.RESOURCE_UPDATE_SCHEMAS[resource]

        def endpoint_factory(resource_name: str, create_type, update_type):
            async def list_endpoint(class_id: uuid.UUID, page: int = Query(1, ge=1), page_size: int = Query(20, alias="pageSize", ge=1, le=100),
                                student_id: Optional[uuid.UUID] = Query(None, alias="studentId"), date_from: Optional[date] = Query(None, alias="dateFrom"),
                                date_to: Optional[date] = Query(None, alias="dateTo"), keyword: Optional[str] = None, status_filter: Optional[str] = Query(None, alias="status"),
                                reason: Optional[str] = None, subject: Optional[str] = None, type_filter: Optional[str] = Query(None, alias="type"),
                                level: Optional[str] = None, day: Optional[str] = None, method: Optional[str] = None, category: Optional[str] = None,
                                db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
                items, total = await services.CrudService.list(db, resource_name, class_id, user.id, page, page_size,
                    {"student_id": student_id, "date_from": date_from, "date_to": date_to, "keyword": keyword,
                     "status": status_filter, "reason": reason, "subject": subject, "type": type_filter,
                     "level": level, "day": day, "method": method, "category": category})
                return PaginatedResponse(data=PaginatedData(items=[dump(item) for item in items], total=total, page=page,
                    page_size=page_size, total_pages=ceil(total / page_size)))

            async def create_endpoint(class_id: uuid.UUID, payload: create_type, db: AsyncSession = Depends(get_db),
                                      user: User = Depends(get_current_user)):
                return ResponseModel(data=dump(await services.CrudService.create(db, resource_name, class_id, user.id, payload)))

            async def get_endpoint(class_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                                   user: User = Depends(get_current_user)):
                return ResponseModel(data=dump(await services.CrudService.get(db, resource_name, class_id, user.id, item_id)))

            async def patch_endpoint(class_id: uuid.UUID, item_id: uuid.UUID, payload: update_type, db: AsyncSession = Depends(get_db),
                                     user: User = Depends(get_current_user)):
                return ResponseModel(data=dump(await services.CrudService.update(db, resource_name, class_id, user.id, item_id, payload)))

            async def delete_endpoint(class_id: uuid.UUID, item_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                                      user: User = Depends(get_current_user)):
                await services.CrudService.remove(db, resource_name, class_id, user.id, item_id)
                return Response(status_code=status.HTTP_204_NO_CONTENT)
            return list_endpoint, create_endpoint, get_endpoint, patch_endpoint, delete_endpoint

        list_endpoint, create_endpoint, get_endpoint, patch_endpoint, delete_endpoint = endpoint_factory(
            resource, create_schema, update_schema
        )

        router.add_api_route(f"/classes/{{class_id}}/{resource}", list_endpoint, methods=["GET"], name=f"list_{resource}")
        router.add_api_route(f"/classes/{{class_id}}/{resource}", create_endpoint, methods=["POST"], status_code=201, name=f"create_{resource}")
        router.add_api_route(f"/classes/{{class_id}}/{resource}/{{item_id}}", get_endpoint, methods=["GET"], name=f"get_{resource}")
        router.add_api_route(f"/classes/{{class_id}}/{resource}/{{item_id}}", patch_endpoint, methods=["PATCH"], name=f"update_{resource}")
        router.add_api_route(f"/classes/{{class_id}}/{resource}/{{item_id}}", delete_endpoint, methods=["DELETE"], status_code=204, name=f"delete_{resource}")


register_crud_routes()
