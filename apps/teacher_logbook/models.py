"""Teacher Logbook ORM models."""
import uuid
from datetime import date, time
from decimal import Decimal
from typing import Optional

from sqlalchemy import Date, Index, Integer, Numeric, String, Text, Time, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.database import CoreModel


class OwnedClass(CoreModel):
    __tablename__ = "teacher_logbook_classes"

    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Shanghai")


class ClassRecord(CoreModel):
    __abstract__ = True

    class_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)


class Student(ClassRecord):
    __tablename__ = "teacher_logbook_students"

    name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    gender: Mapped[str] = mapped_column(String(10), nullable=False)
    contact: Mapped[Optional[str]] = mapped_column(String(100))


class SeatBoard(ClassRecord):
    __tablename__ = "teacher_logbook_seat_boards"
    __table_args__ = (UniqueConstraint("class_id", name="uq_teacher_logbook_seat_board_class"),)

    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    column_groups: Mapped[list[int]] = mapped_column(JSONB, nullable=False, default=list)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


class SeatAssignment(ClassRecord):
    __tablename__ = "teacher_logbook_seat_assignments"
    __table_args__ = (
        Index("uq_teacher_logbook_seat_student", "class_id", "student_id", unique=True,
              postgresql_where=text("is_deleted = false")),
        Index("uq_teacher_logbook_seat_position", "class_id", "row", "column", unique=True,
              postgresql_where=text("is_deleted = false")),
    )

    student_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    row: Mapped[int] = mapped_column(Integer, nullable=False)
    column: Mapped[int] = mapped_column(Integer, nullable=False)


class StudentRecord(ClassRecord):
    __abstract__ = True
    student_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)


class LeaveRequest(StudentRecord):
    __tablename__ = "teacher_logbook_leave_requests"
    reason: Mapped[str] = mapped_column(String(20), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class HomeworkRecord(ClassRecord):
    __tablename__ = "teacher_logbook_homework_records"
    subject: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    unsubmitted: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class Violation(StudentRecord):
    __tablename__ = "teacher_logbook_violations"
    type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)


class Alert(StudentRecord):
    __tablename__ = "teacher_logbook_alerts"
    __table_args__ = (Index("ix_teacher_logbook_alert_state", "class_id", "status", "level"),)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    level: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)


class Todo(ClassRecord):
    __tablename__ = "teacher_logbook_todos"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    due: Mapped[Optional[date]] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class WorkRecord(ClassRecord):
    __tablename__ = "teacher_logbook_work_records"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)


class Exam(ClassRecord):
    __tablename__ = "teacher_logbook_exams"
    subject: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    average: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class CommitteeRole(ClassRecord):
    __tablename__ = "teacher_logbook_committee_roles"
    __table_args__ = (Index("uq_teacher_logbook_committee_role", "class_id", "role", unique=True,
                            postgresql_where=text("is_deleted = false")),)
    role: Mapped[str] = mapped_column(String(100), nullable=False)
    duty: Mapped[str] = mapped_column(Text, nullable=False)


class CommitteeMember(StudentRecord):
    __tablename__ = "teacher_logbook_committee_members"
    role_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)


class HygieneAssignment(StudentRecord):
    __tablename__ = "teacher_logbook_hygiene_assignments"
    area: Mapped[str] = mapped_column(String(100), nullable=False)
    day: Mapped[str] = mapped_column(String(20), nullable=False, index=True)


class Activity(ClassRecord):
    __tablename__ = "teacher_logbook_activities"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[Optional[str]] = mapped_column(Text)


class FinanceRecord(ClassRecord):
    __tablename__ = "teacher_logbook_finance_records"
    type: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    note: Mapped[str] = mapped_column(String(1000), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class Award(StudentRecord):
    __tablename__ = "teacher_logbook_awards"
    type: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class Course(ClassRecord):
    __tablename__ = "teacher_logbook_courses"
    course: Mapped[str] = mapped_column(String(100), nullable=False)
    teacher: Mapped[str] = mapped_column(String(100), nullable=False)
    day: Mapped[str] = mapped_column(String(10), nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)


class Talk(StudentRecord):
    __tablename__ = "teacher_logbook_talks"
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class Contact(StudentRecord):
    __tablename__ = "teacher_logbook_contacts"
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)


class TrainingRecord(ClassRecord):
    __tablename__ = "teacher_logbook_training_records"
    category: Mapped[str] = mapped_column(String(10), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    hours: Mapped[Decimal] = mapped_column(Numeric(8, 2), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)


class Link(ClassRecord):
    __tablename__ = "teacher_logbook_links"
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)


class UiPreference(CoreModel):
    __tablename__ = "teacher_logbook_ui_preferences"
    __table_args__ = (UniqueConstraint("user_id", name="uq_teacher_logbook_ui_user"),)
    user_id: Mapped[uuid.UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    skin: Mapped[str] = mapped_column(String(50), nullable=False, default="mr")


class AuditLog(ClassRecord):
    __tablename__ = "teacher_logbook_audit_logs"
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
