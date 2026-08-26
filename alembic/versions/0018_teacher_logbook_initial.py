"""Create Teacher Logbook tables.

Revision ID: 0018_teacher_logbook
Revises: 0017_ledger_mate_ai_chat
Create Date: 2026-08-26
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0018_teacher_logbook"
down_revision = "0017_ledger_mate_ai_chat"
branch_labels = None
depends_on = None

UUID = postgresql.UUID(as_uuid=True)


def audit_columns():
    return [
        sa.Column("id", UUID, primary_key=True),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    ]


def class_columns():
    return audit_columns() + [
        sa.Column("class_id", UUID, nullable=False),
        sa.Column("user_id", UUID, nullable=False),
    ]


def create_record_table(name, *columns):
    op.create_table(name, *class_columns(), *columns)
    op.create_index(f"ix_{name}_class_id", name, ["class_id"])
    op.create_index(f"ix_{name}_user_id", name, ["user_id"])


def upgrade() -> None:
    op.create_table("teacher_logbook_classes", *audit_columns(), sa.Column("user_id", UUID, nullable=False),
                    sa.Column("name", sa.String(100), nullable=False), sa.Column("timezone", sa.String(64), nullable=False))
    op.create_index("ix_teacher_logbook_classes_user_id", "teacher_logbook_classes", ["user_id"])
    create_record_table("teacher_logbook_students", sa.Column("name", sa.String(100), nullable=False),
                        sa.Column("gender", sa.String(10), nullable=False), sa.Column("contact", sa.String(100)))
    create_record_table("teacher_logbook_seat_boards", sa.Column("rows", sa.Integer(), nullable=False),
                        sa.Column("column_groups", postgresql.JSONB(), nullable=False), sa.Column("version", sa.Integer(), nullable=False),
                        sa.UniqueConstraint("class_id", name="uq_teacher_logbook_seat_board_class"))
    create_record_table("teacher_logbook_seat_assignments", sa.Column("student_id", UUID, nullable=False),
                        sa.Column("row", sa.Integer(), nullable=False), sa.Column("column", sa.Integer(), nullable=False))
    op.create_index("uq_teacher_logbook_seat_student", "teacher_logbook_seat_assignments", ["class_id", "student_id"], unique=True,
                    postgresql_where=sa.text("is_deleted = false"))
    op.create_index("uq_teacher_logbook_seat_position", "teacher_logbook_seat_assignments", ["class_id", "row", "column"], unique=True,
                    postgresql_where=sa.text("is_deleted = false"))
    create_record_table("teacher_logbook_leave_requests", sa.Column("student_id", UUID, nullable=False), sa.Column("reason", sa.String(20), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_homework_records", sa.Column("subject", sa.String(50), nullable=False), sa.Column("title", sa.String(200), nullable=False), sa.Column("unsubmitted", sa.Integer(), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_violations", sa.Column("student_id", UUID, nullable=False), sa.Column("type", sa.String(50), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("note", sa.Text()))
    create_record_table("teacher_logbook_alerts", sa.Column("student_id", UUID, nullable=False), sa.Column("type", sa.String(20), nullable=False), sa.Column("level", sa.String(10), nullable=False), sa.Column("status", sa.String(20), nullable=False), sa.Column("note", sa.Text()))
    op.create_index("ix_teacher_logbook_alert_state", "teacher_logbook_alerts", ["class_id", "status", "level"])
    create_record_table("teacher_logbook_todos", sa.Column("title", sa.String(200), nullable=False), sa.Column("due", sa.Date()), sa.Column("status", sa.String(20), nullable=False))
    create_record_table("teacher_logbook_work_records", sa.Column("title", sa.String(200), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("note", sa.Text()))
    create_record_table("teacher_logbook_exams", sa.Column("subject", sa.String(50), nullable=False), sa.Column("name", sa.String(100), nullable=False), sa.Column("average", sa.Numeric(8, 2), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_committee_roles", sa.Column("role", sa.String(100), nullable=False), sa.Column("duty", sa.Text(), nullable=False))
    op.create_index("uq_teacher_logbook_committee_role", "teacher_logbook_committee_roles", ["class_id", "role"], unique=True, postgresql_where=sa.text("is_deleted = false"))
    create_record_table("teacher_logbook_committee_members", sa.Column("student_id", UUID, nullable=False), sa.Column("role_id", UUID, nullable=False))
    create_record_table("teacher_logbook_hygiene_assignments", sa.Column("student_id", UUID, nullable=False), sa.Column("area", sa.String(100), nullable=False), sa.Column("day", sa.String(20), nullable=False))
    create_record_table("teacher_logbook_activities", sa.Column("title", sa.String(200), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("note", sa.Text()))
    create_record_table("teacher_logbook_finance_records", sa.Column("type", sa.String(10), nullable=False), sa.Column("amount", sa.Numeric(14, 2), nullable=False), sa.Column("note", sa.String(1000), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_awards", sa.Column("student_id", UUID, nullable=False), sa.Column("type", sa.String(10), nullable=False), sa.Column("note", sa.Text(), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_courses", sa.Column("course", sa.String(100), nullable=False), sa.Column("teacher", sa.String(100), nullable=False), sa.Column("day", sa.String(10), nullable=False), sa.Column("start_time", sa.Time(), nullable=False), sa.Column("end_time", sa.Time(), nullable=False))
    create_record_table("teacher_logbook_talks", sa.Column("student_id", UUID, nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("note", sa.Text(), nullable=False))
    create_record_table("teacher_logbook_contacts", sa.Column("student_id", UUID, nullable=False), sa.Column("method", sa.String(10), nullable=False), sa.Column("date", sa.Date(), nullable=False), sa.Column("note", sa.Text(), nullable=False))
    create_record_table("teacher_logbook_training_records", sa.Column("category", sa.String(10), nullable=False), sa.Column("title", sa.String(200), nullable=False), sa.Column("hours", sa.Numeric(8, 2), nullable=False), sa.Column("date", sa.Date(), nullable=False))
    create_record_table("teacher_logbook_links", sa.Column("title", sa.String(200), nullable=False), sa.Column("url", sa.String(2048), nullable=False))
    op.create_table("teacher_logbook_ui_preferences", *audit_columns(), sa.Column("user_id", UUID, nullable=False),
                    sa.Column("skin", sa.String(50), nullable=False), sa.UniqueConstraint("user_id", name="uq_teacher_logbook_ui_user"))
    op.create_index("ix_teacher_logbook_ui_preferences_user_id", "teacher_logbook_ui_preferences", ["user_id"])
    create_record_table("teacher_logbook_audit_logs", sa.Column("action", sa.String(50), nullable=False),
                        sa.Column("detail", postgresql.JSONB(), nullable=False))


def downgrade() -> None:
    tables = ["audit_logs", "ui_preferences", "links", "training_records", "contacts", "talks", "courses", "awards",
              "finance_records", "activities", "hygiene_assignments", "committee_members", "committee_roles", "exams",
              "work_records", "todos", "alerts", "violations", "homework_records", "leave_requests", "seat_assignments",
              "seat_boards", "students", "classes"]
    for table in tables:
        op.drop_table(f"teacher_logbook_{table}")
