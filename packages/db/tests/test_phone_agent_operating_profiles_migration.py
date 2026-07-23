from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0024_phone_agent_operating_profiles.py"
    spec = importlib.util.spec_from_file_location("phone_agent_operating_profiles_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_migration_adds_independent_agent_profiles_and_call_snapshot(monkeypatch) -> None:
    migration = _load_migration()
    assert migration.revision == "0024_phone_agent_profiles"
    assert len(migration.revision) <= 32
    columns: list[tuple[str, str, bool]] = []
    indexes: list[tuple[str, str, tuple[str, ...], bool, str]] = []
    statements: list[str] = []

    monkeypatch.setattr(
        migration.op,
        "add_column",
        lambda table, column: columns.append((table, column.name, column.nullable)),
    )

    def capture_index(name, table, fields, *, unique, postgresql_where):
        indexes.append((name, table, tuple(fields), unique, str(postgresql_where)))

    monkeypatch.setattr(migration.op, "create_index", capture_index)
    monkeypatch.setattr(migration.op, "execute", lambda sql: statements.append(str(sql)))

    migration.upgrade()

    assert columns == [
        ("phone_agent_templates", "operating_profile", False),
        ("phone_agent_templates", "handles_inbound", False),
        ("phone_agent_templates", "handles_outbound", False),
        ("phone_agent_templates", "is_inbound_default", False),
        ("phone_calls", "recipient_name", True),
        ("phone_calls", "agent_operating_profile", True),
    ]
    assert indexes == [
        (
            "uq_phone_agent_templates_inbound_default",
            "phone_agent_templates",
            ("tenant_id", "user_id"),
            True,
            "is_inbound_default",
        )
    ]
    assert "SET is_inbound_default = is_default" in statements[0]
