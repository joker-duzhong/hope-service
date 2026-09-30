"""Repair the DEBUG/create_all drift between revisions 0015 and 0020.

Run with no arguments for a read-only report. After backing up the database and
stopping application writers, use --apply to repair and record the exact target
revision in one PostgreSQL transaction. Unexpected schema differences abort.
"""
from __future__ import annotations

import argparse
import asyncio
import io
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine


BASE_REVISION = "0015_aurakey_gallery_edit"
TARGET_REVISION = "0020_ledger_mate_cat_templates"
SCHEMA = "public"
REVISIONS = (
    "0016_ledger_mate_initial",
    "0017_ledger_mate_ai_chat",
    "0018_teacher_logbook",
    "0019_core_user_identities",
    TARGET_REVISION,
)
DIALECT = postgresql.dialect()
REPAIRABLE_INDEXES = {"ix_lm_ai_messages_session_user", "ix_lm_ai_refs_lookup"}


class RepairError(RuntimeError):
    """A known prerequisite failed; the caller must roll back."""


@dataclass(frozen=True)
class IndexSpec:
    name: str
    table: str
    columns: tuple[str, ...]
    unique: bool = False
    where: str = ""


@dataclass
class Manifest:
    tables: dict[str, sa.Table] = field(default_factory=dict)
    indexes: list[IndexSpec] = field(default_factory=list)
    seeds: list[dict[str, Any]] = field(default_factory=list)
    strict_audit_tables: set[str] = field(default_factory=set)


@dataclass
class RepairPlan:
    statements: list[str]
    missing_seeds: list[dict[str, Any]]
    revision: str


class _Recorder:
    """Capture the pinned migrations as schema objects, without a connection."""

    def __init__(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self.operations = Operations(MigrationContext.configure(
            dialect_name="postgresql", opts={"as_sql": True, "output_buffer": io.StringIO()}
        ))

    def create_table(self, name: str, *columns: Any, **kwargs: Any) -> sa.Table:
        table = self.operations.create_table(name, *columns, **kwargs)
        self.manifest.tables[name] = table
        return table

    def create_index(self, name: str, table: str, columns: list[str], **kwargs: Any) -> None:
        self.manifest.indexes.append(IndexSpec(
            name, table, tuple(columns), kwargs.get("unique", False),
            str(kwargs.get("postgresql_where", "")),
        ))

    def add_column(self, table: str, column: sa.Column[Any]) -> None:
        self.manifest.tables[table] = sa.Table(table, sa.MetaData(), column)

    def alter_column(self, table: str, column: str, **kwargs: Any) -> None:
        self.manifest.tables[table].c[column].type = kwargs["type_"]

    def bulk_insert(self, table: Any, rows: list[dict[str, Any]], **kwargs: Any) -> None:
        if table.name != "ledger_mate_category_templates":
            raise RepairError("迁移包含不支持的数据写入。")
        self.manifest.seeds.extend(dict(row) for row in rows)


def build_manifest() -> Manifest:
    scripts = ScriptDirectory(str(Path(__file__).resolve().parent / "alembic"))
    revisions = list(reversed(list(scripts.iterate_revisions(TARGET_REVISION, BASE_REVISION))))
    if tuple(revision.revision for revision in revisions) != REVISIONS:
        raise RepairError("0015–0020 迁移链与修复脚本不匹配。")
    manifest = Manifest()
    recorder = _Recorder(manifest)
    for revision in revisions:
        original = revision.module.op
        before = set(manifest.tables)
        try:
            revision.module.op = recorder
            revision.module.upgrade()
        finally:
            revision.module.op = original
        if revision.revision in REVISIONS[:2]:
            manifest.strict_audit_tables.update(set(manifest.tables) - before)
    if len(manifest.tables) != 36 or len(manifest.seeds) != 12:
        raise RepairError("迁移定义已变化，拒绝自动修复。")
    return manifest


def _quote(name: str) -> str:
    return DIALECT.identifier_preparer.quote_identifier(name)


def _table(name: str) -> str:
    return f"{_quote(SCHEMA)}.{_quote(name)}"


def _normalize(value: Any) -> str | None:
    if value is None:
        return None
    value = re.sub(r"::(?:character varying|text|boolean|integer)$", "", str(value).strip())
    while value.startswith("(") and value.endswith(")"):
        value = value[1:-1].strip()
    return value


def _default(column: sa.Column[Any]) -> str | None:
    value = DIALECT.ddl_compiler(DIALECT, None).get_column_default_string(column)
    if isinstance(column.type, sa.Integer) and value and re.fullmatch(r"'-?\d+'", value):
        return str(int(value[1:-1]))
    return value


def _can_set_default(table: str, column: str) -> bool:
    return column == "is_deleted" or (table, column) in {
        ("core_user_identities", "provider"),
        ("ledger_mate_category_templates", "sort_order"),
        ("ledger_mate_category_templates", "is_enabled"),
    }


def _index_matches(actual: dict[str, Any], expected: IndexSpec) -> bool:
    options = actual.get("dialect_options", {})
    return (
        tuple(actual["column_names"]) == expected.columns
        and actual["unique"] == expected.unique
        and _normalize(options.get("postgresql_where", "")) == _normalize(expected.where)
        and not options.get("postgresql_ops")
        and options.get("postgresql_using", "btree") == "btree"
        and not actual.get("column_sorting")
    )


def _missing_seeds(connection: Connection, manifest: Manifest) -> list[dict[str, Any]]:
    table = manifest.tables["ledger_mate_category_templates"].to_metadata(sa.MetaData(), schema=SCHEMA)
    keys = [(row["record_type"], row["name"]) for row in manifest.seeds]
    ids = {row["id"]: (row["record_type"], row["name"]) for row in manifest.seeds}
    rows = connection.execute(sa.select(table.c.id, table.c.record_type, table.c.name).where(sa.or_(
        sa.tuple_(table.c.record_type, table.c.name).in_(keys), table.c.id.in_(ids),
    ))).mappings().all()
    present = set()
    for row in rows:
        key = (row["record_type"], row["name"])
        if row["id"] in ids and ids[row["id"]] != key:
            raise RepairError("默认模板 UUID 已被其他模板占用，需人工核对。")
        present.add(key)
    return [dict(row, is_deleted=False) for row in manifest.seeds
            if (row["record_type"], row["name"]) not in present]


def plan_repair(connection: Connection, manifest: Manifest) -> RepairPlan:
    if connection.dialect.name != "postgresql":
        raise RepairError("修复仅支持 PostgreSQL。")
    if connection.execute(sa.text("SELECT current_schema()")).scalar_one() != SCHEMA:
        raise RepairError("当前 schema 不是 public，拒绝自动修复。")
    db = inspect(connection)
    names = set(db.get_table_names(schema=SCHEMA))
    required = set(manifest.tables) | {"alembic_version"}
    if not required <= names:
        raise RepairError("缺少预期表：" + ", ".join(sorted(required - names)))
    revisions = connection.execute(sa.text(
        'SELECT version_num FROM "public"."alembic_version"'
    )).scalars().all()
    if len(revisions) != 1 or revisions[0] not in {BASE_REVISION, TARGET_REVISION}:
        raise RepairError("仅支持单一版本 0015 或已修复的 0020；当前版本不符合要求。")
    revision = revisions[0]
    valid_indexes = set(connection.execute(sa.text(
        "SELECT idx.relname FROM pg_index i "
        "JOIN pg_class idx ON idx.oid = i.indexrelid "
        "JOIN pg_namespace n ON n.oid = idx.relnamespace "
        "WHERE n.nspname = 'public' AND i.indisvalid AND i.indisready"
    )).scalars().all())
    invalid_constraints = connection.execute(sa.text(
        "SELECT t.relname FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace "
        "WHERE n.nspname = 'public' AND NOT c.convalidated"
    )).scalars().all()
    if set(invalid_constraints) & set(manifest.tables):
        raise RepairError("相关表存在尚未验证的约束，需人工核对。")
    constraint_options = connection.execute(sa.text(
        "SELECT t.relname AS table_name, c.contype AS kind, "
        "c.condeferrable AS deferrable, c.condeferred AS deferred FROM pg_constraint c "
        "JOIN pg_class t ON t.oid = c.conrelid "
        "JOIN pg_namespace n ON n.oid = t.relnamespace WHERE n.nspname = 'public'"
    )).mappings().all()
    for constraint in constraint_options:
        if constraint["table_name"] in manifest.tables and constraint["table_name"] != "core_users":
            if constraint["kind"] in {"c", "x"} or constraint["deferrable"] or constraint["deferred"]:
                raise RepairError("相关表存在意外的 CHECK、排他或可延迟约束。")
    statements: list[str] = []
    indexes: dict[str, list[dict[str, Any]]] = {}
    for name, table in manifest.tables.items():
        columns = {column["name"]: column for column in db.get_columns(name, schema=SCHEMA)}
        expected_names = set(table.c.keys())
        if (name != "core_users" and set(columns) != expected_names) or not expected_names <= set(columns):
            raise RepairError(f"{name} 字段集合不符合预期。")
        for column in table.c:
            actual = columns[column.name]
            location = f"{name}.{column.name}"
            expected_type = column.type.compile(dialect=DIALECT)
            actual_type = actual["type"].compile(dialect=DIALECT)
            if actual_type != expected_type:
                if (name, column.name, actual_type, expected_type) != (
                    "ledger_mate_categories", "icon", "VARCHAR(50)", "VARCHAR(500)"
                ):
                    raise RepairError(f"{location} 类型或时区不符合预期。")
                statements.append(f'ALTER TABLE {_table(name)} ALTER COLUMN "icon" TYPE VARCHAR(500)')
            stricter_audit = (
                name in manifest.strict_audit_tables and column.name in {"created_at", "updated_at"}
                and column.nullable and not actual["nullable"]
            )
            if actual["nullable"] != column.nullable and not stricter_audit:
                raise RepairError(f"{location} 非空约束不符合预期。")
            if actual.get("identity") or actual.get("computed"):
                raise RepairError(f"{location} 存在意外的生成列定义。")
            default = _default(column)
            if _normalize(actual["default"]) != _normalize(default):
                if actual["default"] is not None or default is None or not _can_set_default(name, column.name):
                    raise RepairError(f"{location} 默认值不符合已确认的修复范围。")
                statements.append(
                    f"ALTER TABLE {_table(name)} ALTER COLUMN {_quote(column.name)} SET DEFAULT {default}"
                )
        if name == "core_users":
            continue
        if db.get_pk_constraint(name, schema=SCHEMA)["constrained_columns"] != [c.name for c in table.primary_key]:
            raise RepairError(f"{name} 主键不符合预期。")
        unique_constraints = db.get_unique_constraints(name, schema=SCHEMA)
        expected_unique = {
            (constraint.name, tuple(constraint.columns.keys()))
            for constraint in table.constraints if isinstance(constraint, sa.UniqueConstraint)
        }
        actual_unique = {(u["name"], tuple(u["column_names"])) for u in unique_constraints}
        if actual_unique != expected_unique or any(
            u["name"] not in valid_indexes
            or u.get("dialect_options", {}).get("postgresql_nulls_not_distinct")
            for u in unique_constraints
        ):
            raise RepairError(f"{name} 缺少有效的预期唯一约束或存在未知唯一约束。")
        foreign_keys = db.get_foreign_keys(name, schema=SCHEMA)
        if len(foreign_keys) != len(table.foreign_key_constraints):
            raise RepairError(f"{name} 外键集合不符合预期。")
        for constraint in table.foreign_key_constraints:
            expected_columns = [column.name for column in constraint.columns]
            expected_targets = [element.target_fullname for element in constraint.elements]
            if not any(
                fk["constrained_columns"] == expected_columns
                and fk["referred_schema"] in {None, SCHEMA}
                and [f'{fk["referred_table"]}.{c}' for c in fk["referred_columns"]] == expected_targets
                and not fk.get("options")
                for fk in foreign_keys
            ):
                raise RepairError(f"{name} 外键不符合预期。")
        indexes[name] = db.get_indexes(name, schema=SCHEMA)
        for index in indexes[name]:
            if index["unique"] and not index.get("duplicates_constraint"):
                if not any(spec.table == name and spec.unique and _index_matches(index, spec)
                           for spec in manifest.indexes):
                    raise RepairError(f"{name} 存在未知的唯一索引。")
    for expected in manifest.indexes:
        actual = indexes[expected.table]
        same_name = [index for index in actual if index["name"] == expected.name]
        if same_name and not _index_matches(same_name[0], expected):
            raise RepairError(f"{expected.name} 已存在，但定义不符合预期。")
        matches = [index for index in actual if _index_matches(index, expected)]
        if matches:
            if not any(index["name"] in valid_indexes for index in matches):
                raise RepairError(f"{expected.name} 对应索引尚未生效。")
        elif expected.name in REPAIRABLE_INDEXES:
            columns_sql = ", ".join(_quote(column) for column in expected.columns)
            statements.append(f"CREATE INDEX {_quote(expected.name)} ON {_table(expected.table)} ({columns_sql})")
        else:
            raise RepairError(f"缺少预期索引 {expected.name}，超出已确认修复范围。")
    missing_seeds = _missing_seeds(connection, manifest)
    if revision == TARGET_REVISION and (statements or missing_seeds):
        raise RepairError("版本已是 0020，但结构或模板仍有差异，需人工核对。")
    return RepairPlan(statements, missing_seeds, revision)


def repair(connection: Connection, manifest: Manifest, *, apply: bool = False) -> dict[str, Any]:
    if apply:
        # Hold write locks through validation, DDL, seed insertion and version update.
        tables = sorted(set(manifest.tables) | {"alembic_version"})
        connection.execute(sa.text(
            "LOCK TABLE " + ", ".join(_table(name) for name in tables) + " IN SHARE ROW EXCLUSIVE MODE"
        ))
    plan = plan_repair(connection, manifest)
    result = {
        "mode": "apply" if apply else "check", "revision": plan.revision,
        "target_revision": TARGET_REVISION, "schema_statements": plan.statements,
        "missing_default_templates": len(plan.missing_seeds),
        "inserted_default_templates": 0, "applied": False,
    }
    if not apply or plan.revision == TARGET_REVISION:
        return result
    for statement in plan.statements:
        connection.execute(sa.text(statement))
    if plan.missing_seeds:
        table = manifest.tables["ledger_mate_category_templates"].to_metadata(sa.MetaData(), schema=SCHEMA)
        connection.execute(postgresql.insert(table).values(plan.missing_seeds).on_conflict_do_nothing(
            index_elements=["record_type", "name"]
        ))
    verified = plan_repair(connection, manifest)
    if verified.statements or verified.missing_seeds:
        raise RepairError("修复后复核未通过，事务将回滚。")
    updated = connection.execute(sa.text(
        'UPDATE "public"."alembic_version" SET version_num = :target WHERE version_num = :base'
    ), {"target": TARGET_REVISION, "base": BASE_REVISION})
    if updated.rowcount != 1:
        raise RepairError("版本记录发生变化，事务将回滚。")
    plan_repair(connection, manifest)
    result.update(revision=TARGET_REVISION, applied=True, missing_default_templates=0,
                  inserted_default_templates=len(plan.missing_seeds))
    return result


async def run(engine: AsyncEngine, *, apply: bool = False) -> dict[str, Any]:
    engine.echo = False
    try:
        manifest = build_manifest()
        async with engine.begin() as connection:
            await connection.execute(sa.text(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ" + ("" if apply else ", READ ONLY")
            ))
            await connection.execute(sa.text("SET LOCAL lock_timeout = '5s'"))
            await connection.execute(sa.text("SET LOCAL statement_timeout = '120s'"))
            return await connection.run_sync(lambda conn: repair(conn, manifest, apply=apply))
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="执行修复；默认仅只读检查")
    args = parser.parse_args()
    logging.getLogger("sqlalchemy.engine").setLevel(logging.ERROR)
    try:
        from core.database import engine

        result = asyncio.run(run(engine, apply=args.apply))
    except RepairError as exc:
        print(f"修复已中止：{exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        # Driver exceptions may embed URLs, SQL parameters or credential values.
        print(f"修复未完成：{type(exc).__name__}；请核对连接、锁等待和数据库状态。", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
