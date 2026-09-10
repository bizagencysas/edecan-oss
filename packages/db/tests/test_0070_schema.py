"""Schema contract for the durable bot-run tables added in migration 0070."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from edecan_db.models import ALL_MODELS, RLS_TABLES, Base, BotRun, JobOutbox, RunEvent
from sqlalchemy import CheckConstraint, UniqueConstraint


def _check_sql(table_name: str, column_name: str) -> str:
    table = Base.metadata.tables[table_name]
    constraint = next(
        item
        for item in table.constraints
        if isinstance(item, CheckConstraint) and column_name in str(item.sqltext)
    )
    return str(constraint.sqltext)


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _foreign_key(table_name: str, column_name: str):
    column = Base.metadata.tables[table_name].columns[column_name]
    return next(iter(column.foreign_keys))


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0070_job_outbox_bot_runs.py"
    spec = importlib.util.spec_from_file_location("job_outbox_bot_runs_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_job_outbox_contract() -> None:
    table = Base.metadata.tables["job_outbox"]
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "job_type",
        "payload",
        "status",
        "attempts",
        "available_at",
        "sent_at",
        "last_error",
        "created_at",
        "updated_at",
    }
    assert str(table.columns["id"].server_default.arg) == "gen_random_uuid()"
    assert str(table.columns["payload"].server_default.arg) == "'{}'::jsonb"
    assert table.columns["status"].server_default.arg == "queued"
    assert str(table.columns["attempts"].server_default.arg) == "0"
    assert table.columns["available_at"].server_default is not None
    assert table.columns["sent_at"].nullable is True
    assert table.columns["last_error"].nullable is True

    status_check = _check_sql("job_outbox", "status")
    for status in ("queued", "sent", "dead"):
        assert status in status_check

    tenant_fk = _foreign_key("job_outbox", "tenant_id")
    assert tenant_fk.target_fullname == "tenants.id"
    assert tenant_fk.ondelete == "CASCADE"

    indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}
    assert indexes["ix_job_outbox_status_available_at"] == ("status", "available_at")
    assert indexes["ix_job_outbox_tenant_id"] == ("tenant_id",)


def test_bot_runs_contract() -> None:
    table = Base.metadata.tables["bot_runs"]
    assert set(table.columns.keys()) == {
        "id",
        "tenant_id",
        "worker_id",
        "conversation_id",
        "origen",
        "run_key",
        "status",
        "claim_generation",
        "lease_expires_at",
        "delivery_message_id",
        "error",
        "created_at",
        "updated_at",
    }
    assert table.columns["origen"].server_default.arg == "chat"
    assert table.columns["status"].server_default.arg == "queued"
    assert str(table.columns["claim_generation"].server_default.arg) == "0"

    status_check = _check_sql("bot_runs", "status")
    for status in ("queued", "running", "succeeded", "failed", "cancelled"):
        assert status in status_check

    tenant_fk = _foreign_key("bot_runs", "tenant_id")
    worker_fk = _foreign_key("bot_runs", "worker_id")
    assert (tenant_fk.target_fullname, tenant_fk.ondelete) == ("tenants.id", "CASCADE")
    assert (worker_fk.target_fullname, worker_fk.ondelete) == (
        "persistent_agents.id",
        "CASCADE",
    )
    assert not table.columns["conversation_id"].foreign_keys
    assert not table.columns["delivery_message_id"].foreign_keys
    assert ("run_key",) in _unique_columns("bot_runs")

    indexes = {index.name: tuple(column.name for column in index.columns) for index in table.indexes}
    assert indexes["ix_bot_runs_tenant_worker_status"] == (
        "tenant_id",
        "worker_id",
        "status",
    )


def test_run_events_contract() -> None:
    table = Base.metadata.tables["run_events"]
    assert set(table.columns.keys()) == {
        "id",
        "run_key",
        "seq",
        "tenant_id",
        "type",
        "payload",
        "created_at",
    }
    assert str(table.columns["payload"].server_default.arg) == "'{}'::jsonb"
    assert table.columns["created_at"].server_default is not None
    assert ("run_key", "seq") in _unique_columns("run_events")
    assert not table.columns["run_key"].foreign_keys

    tenant_fk = _foreign_key("run_events", "tenant_id")
    assert tenant_fk.target_fullname == "tenants.id"
    assert tenant_fk.ondelete == "CASCADE"


def test_models_are_registered_for_rls() -> None:
    assert {JobOutbox, BotRun, RunEvent} <= set(ALL_MODELS)
    assert {"job_outbox", "bot_runs", "run_events"} <= RLS_TABLES


def test_migration_chain_and_rls_policy(monkeypatch) -> None:
    migration = _load_migration()
    assert migration.revision == "0070_job_outbox_bot_runs"
    assert migration.down_revision == "0069_job_types_union_fix"
    assert migration.RLS_TABLES == ("job_outbox", "bot_runs", "run_events")

    created_tables: list[str] = []
    created_indexes: list[tuple[str, str, tuple[str, ...]]] = []
    statements: list[str] = []
    monkeypatch.setattr(
        migration.op,
        "create_table",
        lambda table_name, *args, **kwargs: created_tables.append(table_name),
    )
    monkeypatch.setattr(
        migration.op,
        "create_index",
        lambda name, table_name, columns, **kwargs: created_indexes.append(
            (name, table_name, tuple(columns))
        ),
    )
    monkeypatch.setattr(migration.op, "execute", lambda sql: statements.append(str(sql)))

    migration.upgrade()

    assert created_tables == ["job_outbox", "bot_runs", "run_events"]
    assert (
        "ix_job_outbox_status_available_at",
        "job_outbox",
        ("status", "available_at"),
    ) in created_indexes
    assert (
        "ix_bot_runs_tenant_worker_status",
        "bot_runs",
        ("tenant_id", "worker_id", "status"),
    ) in created_indexes
    for table_name in migration.RLS_TABLES:
        assert f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY" in statements
        assert any(
            statement.startswith(f"CREATE POLICY tenant_isolation ON {table_name} ")
            for statement in statements
        )
