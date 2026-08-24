from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = (
        Path(__file__).parents[1]
        / "alembic/versions/0020_redact_historical_chat_secrets.py"
    )
    spec = importlib.util.spec_from_file_location("redact_chat_secrets_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_covers_chat_titles_messages_and_nested_tool_results(monkeypatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    assert len(statements) == 3
    assert "UPDATE messages" in sql
    assert "content::text" in sql
    assert "tool_calls::text" in sql
    assert "UPDATE conversations" in sql
    assert "sk[-_][A-Za-z0-9_-]{8,}" in sql
    assert "[REDACTED]" in sql
    assert "?:" not in sql
    assert ":rk_live" not in sql
    assert ":AKIA" not in sql


def test_downgrade_never_reconstructs_a_secret(monkeypatch) -> None:
    migration = _load_migration()
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.downgrade()

    assert statements == []
