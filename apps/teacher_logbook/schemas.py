"""Teacher Logbook Pydantic schemas."""
import re
import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Annotated, Literal, Optional

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, create_model, field_validator, model_validator


def to_camel(value: str) -> str:
    first, *rest = value.split("_")
    return first + "".join(word.capitalize() for word in rest)


class SchemaBase(BaseModel):
    model_config = ConfigDict(from_attributes=True, alias_generator=to_camel, populate_by_name=True)


class RecordRead(SchemaBase):
    id: uuid.UUID
    class_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ClassCreate(SchemaBase):
    name: str = Field(min_length=1, max_length=100)
    timezone: str = Field(default="Asia/Shanghai", min_length=1, max_length=64)


class ClassUpdate(SchemaBase):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    timezone: Optional[str] = Field(default=None, min_length=1, max_length=64)


class ClassRead(ClassCreate):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class StudentCreate(SchemaBase):
    name: str = Field(min_length=1, max_length=100)
    gender: Literal["男", "女", "其他"]
    contact: Optional[str] = Field(default=None, max_length=100)


class StudentUpdate(SchemaBase):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    gender: Optional[Literal["男", "女", "其他"]] = None
    contact: Optional[str] = Field(default=None, max_length=100)


class StudentRead(RecordRead, StudentCreate):
    pass


class LayoutUpdate(SchemaBase):
    rows: int = Field(ge=1, le=30)
    column_groups: list[int] = Field(min_length=1)

    @field_validator("column_groups")
    @classmethod
    def validate_columns(cls, value: list[int]) -> list[int]:
        if any(column < 1 or column > 10 for column in value) or sum(value) > 30:
            raise ValueError("每组列数须为 1-10，总列数不能超过 30")
        return value


class SeatMove(SchemaBase):
    row: int = Field(ge=1)
    column: int = Field(ge=1)
    swap: bool = False


class SeatItem(SchemaBase):
    student_id: uuid.UUID
    student_name: Optional[str] = None
    row: int
    column: int


class SeatBatch(SchemaBase):
    mode: Literal["replace", "merge"] = "replace"
    assignments: list[SeatItem] = Field(default_factory=list, max_length=900)

    @model_validator(mode="after")
    def unique_assignments(self):
        students = [item.student_id for item in self.assignments]
        positions = [(item.row, item.column) for item in self.assignments]
        if len(students) != len(set(students)) or len(positions) != len(set(positions)):
            raise ValueError("学生和座位坐标不能重复")
        return self


class SeatBoardRead(SchemaBase):
    layout: dict
    assignments: list[SeatItem]
    version: int
    updated_at: datetime


class StudentRelation(SchemaBase):
    student_id: uuid.UUID


class LeaveRequestData(StudentRelation):
    reason: Literal["病假", "事假", "其他"]
    date: date


class HomeworkRecordData(SchemaBase):
    subject: str = Field(min_length=1, max_length=50)
    title: str = Field(min_length=1, max_length=200)
    unsubmitted: int = Field(ge=0)
    date: date


class ViolationData(StudentRelation):
    type: str = Field(min_length=1, max_length=50)
    date: date
    note: Optional[str] = Field(default=None, max_length=2000)


class AlertData(StudentRelation):
    type: Literal["情绪预警", "特殊体质", "辍学风险", "未返校", "其他"]
    level: Literal["高", "中", "低"]
    status: Literal["待处理", "跟进中", "已关闭"]
    note: Optional[str] = Field(default=None, max_length=5000)


class TodoData(SchemaBase):
    title: str = Field(min_length=1, max_length=200)
    due: Optional[date] = None
    status: Literal["待完成", "已完成"]


class DatedNoteData(SchemaBase):
    title: str = Field(min_length=1, max_length=200)
    date: date
    note: Optional[str] = None


class ExamData(SchemaBase):
    subject: str = Field(min_length=1, max_length=50)
    name: str = Field(min_length=1, max_length=100)
    average: Decimal = Field(ge=0)
    date: date


class CommitteeRoleData(SchemaBase):
    role: str = Field(min_length=1, max_length=100)
    duty: str = Field(min_length=1)


class CommitteeMemberData(StudentRelation):
    role_id: uuid.UUID


class HygieneAssignmentData(StudentRelation):
    area: str = Field(min_length=1, max_length=100)
    day: str = Field(min_length=1, max_length=20)


class FinanceRecordData(SchemaBase):
    type: Literal["收入", "支出"]
    amount: Decimal = Field(gt=0, max_digits=14, decimal_places=2)
    note: str = Field(min_length=1, max_length=1000)
    date: date


class AwardData(StudentRelation):
    type: Literal["表扬", "奖励", "批评", "处分"]
    note: str = Field(min_length=1)
    date: date


class CourseData(SchemaBase):
    course: str = Field(min_length=1, max_length=100)
    teacher: str = Field(min_length=1, max_length=100)
    day: Literal["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    start_time: time
    end_time: time

    @model_validator(mode="after")
    def validate_time_range(self):
        if self.end_time <= self.start_time:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class TalkData(StudentRelation):
    date: date
    note: str = Field(min_length=1)


class ContactData(TalkData):
    method: Literal["电话", "微信", "面谈", "家访"]


class TrainingRecordData(SchemaBase):
    category: Literal["培训", "讲座", "活动"]
    title: str = Field(min_length=1, max_length=200)
    hours: Decimal = Field(gt=0, max_digits=8, decimal_places=2)
    date: date


class LinkData(SchemaBase):
    title: str = Field(min_length=1, max_length=200)
    url: AnyHttpUrl


class StatusUpdate(SchemaBase):
    status: str
    note: Optional[str] = Field(default=None, max_length=5000)


class UiPreferenceUpdate(SchemaBase):
    skin: str = Field(pattern=r"^[A-Za-z0-9_-]{1,50}$")


class UiPreferenceRead(UiPreferenceUpdate):
    pass


class ClearDataRequest(SchemaBase):
    confirmation: Literal["CLEAR_CLASS_DATA"]


class BackupRestoreRequest(SchemaBase):
    mode: Literal["replace", "merge"]
    confirmation: Literal["RESTORE_CLASS_DATA"]


class ImportResult(SchemaBase):
    total_rows: int
    created: int
    skipped: int
    failed: int
    errors: list[dict]


class FinanceSummary(SchemaBase):
    income: Decimal
    expense: Decimal
    balance: Decimal


class TrainingSummary(SchemaBase):
    total_hours: Decimal
    categories: dict[str, dict[str, int | Decimal]]


RESOURCE_SCHEMAS: dict[str, type[SchemaBase]] = {
    "leave-requests": LeaveRequestData,
    "homework-records": HomeworkRecordData,
    "violations": ViolationData,
    "alerts": AlertData,
    "todos": TodoData,
    "work-records": DatedNoteData,
    "exams": ExamData,
    "committee-roles": CommitteeRoleData,
    "committee-members": CommitteeMemberData,
    "hygiene-assignments": HygieneAssignmentData,
    "activities": DatedNoteData,
    "finance-records": FinanceRecordData,
    "awards": AwardData,
    "courses": CourseData,
    "talks": TalkData,
    "contacts": ContactData,
    "training-records": TrainingRecordData,
    "links": LinkData,
}


def optional_schema(name: str, schema: type[SchemaBase]) -> type[SchemaBase]:
    fields = {
        field_name: (Optional[field.annotation], None)
        for field_name, field in schema.model_fields.items()
    }
    return create_model(name, __base__=SchemaBase, **fields)


RESOURCE_UPDATE_SCHEMAS = {
    resource: optional_schema(f"{schema.__name__}Update", schema)
    for resource, schema in RESOURCE_SCHEMAS.items()
}
