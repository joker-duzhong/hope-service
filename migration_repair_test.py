"""离线验证迁移修复计划与事务边界，不加载业务配置或连接业务数据库。"""

from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

import repair_migration_state as migration


@pytest.fixture
def manifest():
    return migration.build_manifest()


def test_manifest_includes_all_migrated_tables_and_seed_categories(manifest):
    assert len(manifest.tables) == 36
    assert sum(name.startswith("ledger_mate_") for name in manifest.tables) == 10
    assert sum(name.startswith("teacher_logbook_") for name in manifest.tables) == 24
    assert manifest.tables["core_users"].c.token_version.nullable is False
    assert manifest.tables["ledger_mate_categories"].c.icon.type.length == 500
    assert Counter(seed["record_type"] for seed in manifest.seeds) == {"expense": 7, "income": 5}
    assert len({seed["id"] for seed in manifest.seeds}) == 12


class MemoryInspector:
    default_schema_name = "public"

    def __init__(self, manifest):
        self.columns = {}
        self.primary_keys = {}
        self.unique = {}
        self.foreign_keys = {}
        self.indexes = {name: [] for name in manifest.tables}
        for name, table in manifest.tables.items():
            self.columns[name] = [
                {
                    "name": column.name,
                    "type": deepcopy(column.type),
                    "nullable": column.nullable,
                    "default": self.default_sql(column),
                }
                for column in table.columns
            ]
            self.primary_keys[name] = {
                "name": f"{name}_pkey",
                "constrained_columns": [column.name for column in table.primary_key],
            }
            self.unique[name] = [
                {"name": constraint.name, "column_names": list(constraint.columns.keys())}
                for constraint in table.constraints
                if isinstance(constraint, sa.UniqueConstraint)
            ]
            self.foreign_keys[name] = [
                {
                    "name": constraint.name,
                    "constrained_columns": list(constraint.columns.keys()),
                    "referred_schema": "public",
                    "referred_table": constraint.elements[0].target_fullname.split(".")[-2],
                    "referred_columns": [element.target_fullname.split(".")[-1] for element in constraint.elements],
                    "options": {},
                }
                for constraint in table.constraints
                if isinstance(constraint, sa.ForeignKeyConstraint)
            ]
        for index in manifest.indexes:
            self.indexes[index.table].append({
                "name": index.name,
                "column_names": list(index.columns),
                "unique": index.unique,
                "dialect_options": {"postgresql_where": index.where} if index.where else {},
            })

    @staticmethod
    def default_sql(column):
        if column.server_default is None:
            return None
        value = str(column.server_default.arg)
        return f"'{value}'::character varying" if isinstance(column.type, sa.String) else value

    def get_table_names(self, schema=None):
        return list(self.columns) + ["alembic_version"]

    def get_columns(self, name, schema=None):
        return deepcopy(self.columns[name])

    def get_pk_constraint(self, name, schema=None):
        return deepcopy(self.primary_keys[name])

    def get_unique_constraints(self, name, schema=None):
        return deepcopy(self.unique[name])

    def get_foreign_keys(self, name, schema=None):
        return deepcopy(self.foreign_keys[name])

    def get_indexes(self, name, schema=None):
        return deepcopy(self.indexes[name])


def recorded_sql(connection):
    return [
        str(call.args[0])
        for method in (connection.execute, connection.exec_driver_sql)
        for call in method.call_args_list
        if call.args
    ]


def write_sql(connection):
    return [sql for sql in recorded_sql(connection) if sql.lstrip().upper().startswith(
        ("ALTER ", "CREATE ", "INSERT ", "UPDATE ", "DELETE ", "DROP ")
    )]


@pytest.fixture
def connection():
    connection = Mock()
    connection.dialect = postgresql.dialect()
    connection.execute.return_value.rowcount = 1
    connection.exec_driver_sql.return_value.rowcount = 1
    return connection


@pytest.fixture
def live(monkeypatch, manifest, connection):
    inspector = MemoryInspector(manifest)
    state = SimpleNamespace(
        inspector=inspector, connection=connection, schema="public",
        revisions=[migration.BASE_REVISION],
        seeds=[dict(seed, is_deleted=False) for seed in manifest.seeds],
        invalid_indexes=set(), invalid_constraints=[], constraint_details=[],
    )
    monkeypatch.setattr(migration, "inspect", lambda conn: inspector)

    def execute(statement, *args, **kwargs):
        sql = str(statement)
        if "current_schema()" in sql:
            rows = [state.schema]
        elif "version_num FROM" in sql:
            rows = state.revisions
        elif "FROM pg_index" in sql:
            rows = [
                index["name"] for indexes in inspector.indexes.values() for index in indexes
            ] + [
                constraint["name"] for constraints in inspector.unique.values() for constraint in constraints
            ]
            rows = [name for name in rows if name not in state.invalid_indexes]
        elif "FROM pg_constraint" in sql and "AS table_name" in sql:
            rows = state.constraint_details
        elif "FROM pg_constraint" in sql:
            rows = state.invalid_constraints
        elif sql.startswith("SELECT ") and "ledger_mate_category_templates" in sql:
            rows = deepcopy(state.seeds)
        else:
            raise AssertionError(f"unexpected SQL in read-only planner: {sql}")
        result = Mock()
        result.scalar_one.return_value = rows[0] if rows else None
        result.scalars.return_value.all.return_value = rows
        result.mappings.return_value.all.return_value = rows
        return result

    connection.execute.side_effect = execute
    return state


def column(live, table, name):
    return next(item for item in live.inspector.columns[table] if item["name"] == name)


def index(live, table, name):
    return next(item for item in live.inspector.indexes[table] if item["name"] == name)


def test_confirmed_drift_plans_only_preserving_repairs(manifest, live):
    for table, columns in live.inspector.columns.items():
        for item in columns:
            if item["name"] == "is_deleted" or (table, item["name"]) in {
                ("core_user_identities", "provider"),
                ("ledger_mate_category_templates", "sort_order"),
                ("ledger_mate_category_templates", "is_enabled"),
            }:
                item["default"] = None
            if table in manifest.strict_audit_tables and item["name"] in {"created_at", "updated_at"}:
                item["nullable"] = False
    column(live, "ledger_mate_categories", "icon")["type"] = sa.String(50)
    for indexes in live.inspector.indexes.values():
        indexes[:] = [item for item in indexes if item["name"] not in migration.REPAIRABLE_INDEXES]
    index(live, "ledger_mate_ai_sessions", "ix_lm_ai_sessions_user")["name"] = "ix_ledger_mate_ai_sessions_user_id"

    plan = migration.plan_repair(live.connection, manifest)

    assert len(plan.statements) == 41
    assert sum("SET DEFAULT" in statement for statement in plan.statements) == 38
    assert sum(statement.startswith("CREATE INDEX") for statement in plan.statements) == 2
    assert sum("TYPE VARCHAR(500)" in statement for statement in plan.statements) == 1
    assert not any("NULL" in statement or "DROP" in statement for statement in plan.statements)
    assert plan.missing_seeds == []
    assert write_sql(live.connection) == []


def test_existing_disabled_and_deleted_templates_are_not_reseeded(manifest, live):
    live.seeds[0].update(id=uuid4(), is_enabled=False, is_deleted=True)
    previous = deepcopy(live.seeds)

    plan = migration.plan_repair(live.connection, manifest)

    assert plan.missing_seeds == []
    assert live.seeds == previous


def test_only_missing_seed_is_planned_without_changing_existing_rows(manifest, live):
    missing = live.seeds.pop(0)
    live.seeds[0]["is_enabled"] = False

    plan = migration.plan_repair(live.connection, manifest)

    assert [seed["id"] for seed in plan.missing_seeds] == [missing["id"]]
    assert plan.missing_seeds[0]["is_deleted"] is False
    assert live.seeds[0]["is_enabled"] is False


def test_seed_id_owned_by_another_category_aborts(manifest, live):
    live.seeds[0]["name"] = "Existing custom category"

    with pytest.raises(migration.RepairError, match="UUID"):
        migration.plan_repair(live.connection, manifest)

    assert write_sql(live.connection) == []


@pytest.mark.parametrize("table,name,type_", [
    ("ledger_mate_books", "created_at", sa.DateTime(timezone=False)),
    ("ledger_mate_records", "occurred_at", sa.DateTime(timezone=False)),
    ("core_user_identities", "verified_at", sa.DateTime(timezone=False)),
    ("teacher_logbook_courses", "start_time", sa.Time(timezone=True)),
])
def test_timezone_mismatch_aborts(manifest, live, table, name, type_):
    column(live, table, name)["type"] = type_

    with pytest.raises(migration.RepairError, match="类型或时区"):
        migration.plan_repair(live.connection, manifest)


@pytest.mark.parametrize("changes", [
    {"column_names": ["student_id", "class_id"]},
    {"unique": False},
    {"dialect_options": {"postgresql_where": "is_deleted = true"}},
    {"dialect_options": {"postgresql_using": "hash"}},
])
def test_existing_index_with_wrong_definition_aborts(manifest, live, changes):
    index(live, "teacher_logbook_seat_assignments", "uq_teacher_logbook_seat_student").update(changes)

    with pytest.raises(migration.RepairError, match="定义不符合|未知的唯一索引"):
        migration.plan_repair(live.connection, manifest)


@pytest.mark.parametrize("revisions", [[], ["0014_aurakey_reference_images"], ["0021_unknown"], [migration.BASE_REVISION, migration.TARGET_REVISION]])
def test_unknown_or_multiple_revision_states_abort(manifest, live, revisions):
    live.revisions = revisions

    with pytest.raises(migration.RepairError, match="单一版本"):
        migration.plan_repair(live.connection, manifest)


def test_already_target_with_remaining_drift_aborts(manifest, live):
    live.revisions = [migration.TARGET_REVISION]
    column(live, "ledger_mate_categories", "icon")["type"] = sa.String(50)

    with pytest.raises(migration.RepairError, match="版本已是"):
        migration.plan_repair(live.connection, manifest)


def test_invalid_index_and_unvalidated_constraint_abort(manifest, live):
    live.invalid_indexes.add("uq_teacher_logbook_seat_student")
    with pytest.raises(migration.RepairError, match="尚未生效"):
        migration.plan_repair(live.connection, manifest)
    live.invalid_indexes.clear()
    live.invalid_constraints = ["core_user_identities"]
    with pytest.raises(migration.RepairError, match="尚未验证"):
        migration.plan_repair(live.connection, manifest)


def test_named_unique_constraint_cannot_be_replaced_by_an_alias(manifest, live):
    live.inspector.unique["ledger_mate_category_templates"][0]["name"] = "different_unique_name"

    with pytest.raises(migration.RepairError):
        migration.plan_repair(live.connection, manifest)


@pytest.mark.parametrize("kind", ["unique", "foreign_key", "unique_index"])
def test_additional_restrictive_constraints_abort(manifest, live, kind):
    table = "ledger_mate_books"
    if kind == "unique":
        live.inspector.unique[table].append({"name": "unexpected_unique", "column_names": ["name"]})
    elif kind == "foreign_key":
        live.inspector.foreign_keys[table].append({
            "name": "unexpected_fk", "constrained_columns": ["user_id"],
            "referred_schema": "public", "referred_table": "core_users",
            "referred_columns": ["id"], "options": {},
        })
    else:
        live.inspector.indexes[table].append({
            "name": "unexpected_unique_index", "column_names": ["name"],
            "unique": True, "dialect_options": {},
        })

    with pytest.raises(migration.RepairError):
        migration.plan_repair(live.connection, manifest)


@pytest.mark.parametrize("kind,deferrable", [("c", False), ("x", False), ("u", True)])
def test_check_exclusion_or_deferrable_constraint_aborts(manifest, live, kind, deferrable):
    live.constraint_details = [{
        "table_name": "ledger_mate_books", "kind": kind,
        "deferrable": deferrable, "deferred": deferrable,
    }]

    with pytest.raises(migration.RepairError):
        migration.plan_repair(live.connection, manifest)


def repair_plan(*, revision=None, statements=None, missing_seeds=None):
    return migration.RepairPlan(
        revision=revision or migration.BASE_REVISION,
        statements=statements or [],
        missing_seeds=missing_seeds or [],
    )


def test_dry_run_never_executes_planned_writes(monkeypatch, manifest, connection):
    plan = repair_plan(
        statements=["ALTER TABLE public.ledger_mate_categories ALTER COLUMN icon TYPE VARCHAR(500)"],
        missing_seeds=[manifest.seeds[0]],
    )
    monkeypatch.setattr(migration, "plan_repair", Mock(return_value=plan))

    migration.repair(connection, manifest)

    assert write_sql(connection) == []


def test_verified_target_is_idempotent_without_writes(monkeypatch, manifest, connection):
    monkeypatch.setattr(migration, "plan_repair", Mock(return_value=repair_plan(
        revision=migration.TARGET_REVISION,
    )))

    migration.repair(connection, manifest, apply=True)

    assert write_sql(connection) == []


def test_failed_postcheck_prevents_version_update(monkeypatch, manifest, connection):
    plans = Mock(side_effect=[
        repair_plan(statements=["ALTER TABLE public.ledger_mate_categories ALTER COLUMN icon TYPE VARCHAR(500)"]),
        migration.RepairError("postcheck failed"),
    ])
    monkeypatch.setattr(migration, "plan_repair", plans)

    with pytest.raises(migration.RepairError):
        migration.repair(connection, manifest, apply=True)

    assert any(sql.startswith("ALTER TABLE") for sql in write_sql(connection))
    assert not any("alembic_version" in sql for sql in write_sql(connection))


def test_version_compare_and_set_rejects_concurrent_change(monkeypatch, manifest, connection):
    monkeypatch.setattr(migration, "plan_repair", Mock(return_value=repair_plan()))
    connection.execute.return_value.rowcount = 0
    connection.exec_driver_sql.return_value.rowcount = 0

    with pytest.raises(migration.RepairError):
        migration.repair(connection, manifest, apply=True)


def test_apply_preserves_seed_conflicts_and_updates_version_after_validation(monkeypatch, manifest, connection):
    events = []
    plans = iter([
        repair_plan(missing_seeds=[dict(manifest.seeds[0], is_deleted=False)]),
        repair_plan(),
        repair_plan(revision=migration.TARGET_REVISION),
    ])

    def check(*args):
        events.append("check")
        return next(plans)

    def execute(statement, *args, **kwargs):
        events.append(str(statement))
        return SimpleNamespace(rowcount=1)

    monkeypatch.setattr(migration, "plan_repair", check)
    connection.execute.side_effect = execute

    result = migration.repair(connection, manifest, apply=True)

    assert result["applied"] is True
    insertion = next(sql for sql in events if sql.startswith("INSERT "))
    assert "ON CONFLICT (record_type, name) DO NOTHING" in insertion
    update_position = next(i for i, sql in enumerate(events) if sql.startswith("UPDATE "))
    assert events[update_position - 1] == events[update_position + 1] == "check"
    assert connection.execute.call_args_list[-1].args[1] == {
        "target": migration.TARGET_REVISION, "base": migration.BASE_REVISION,
    }


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.asyncio
async def test_run_commits_once_and_defaults_to_read_only(monkeypatch, apply):
    engine = Mock()
    engine.dispose = AsyncMock()
    transaction = AsyncMock()
    engine.begin.return_value = transaction
    connection = AsyncMock()
    transaction.__aenter__.return_value = connection
    connection.run_sync.side_effect = lambda callback: callback(Mock())
    expected = {"applied": apply}
    monkeypatch.setattr(migration, "repair", Mock(return_value=expected))

    assert await migration.run(engine, apply=apply) == expected

    engine.begin.assert_called_once_with()
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    engine.dispose.assert_awaited_once_with()
    assert engine.echo is False
    setup = str(connection.execute.call_args_list[0].args[0])
    assert "REPEATABLE READ" in setup
    assert ("READ ONLY" in setup) is (not apply)


@pytest.mark.asyncio
async def test_run_propagates_failure_for_rollback_and_disposes(monkeypatch):
    engine = Mock()
    engine.dispose = AsyncMock()
    transaction = AsyncMock()
    transaction.__aexit__.return_value = False
    engine.begin.return_value = transaction
    connection = AsyncMock()
    transaction.__aenter__.return_value = connection
    connection.run_sync.side_effect = lambda callback: callback(Mock())
    failure = migration.RepairError("postcheck failed")
    monkeypatch.setattr(migration, "repair", Mock(side_effect=failure))

    with pytest.raises(migration.RepairError, match="postcheck failed"):
        await migration.run(engine, apply=True)

    assert transaction.__aexit__.await_args.args[:2] == (migration.RepairError, failure)
    engine.dispose.assert_awaited_once_with()
