from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0021_conversation_title_sources.py"
    spec = importlib.util.spec_from_file_location("conversation_title_source_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_marks_existing_titles_for_one_time_classification(monkeypatch) -> None:
    migration = _load_migration()
    added: list[tuple] = []
    constraints: list[tuple] = []
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "add_column", lambda *args: added.append(args))
    monkeypatch.setattr(
        migration.op, "create_check_constraint", lambda *args: constraints.append(args)
    )
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert added[0][0] == "conversations"
    column = added[0][1]
    assert column.name == "title_source"
    assert column.server_default.arg == "legacy"
    assert constraints == [
        (
            "ck_conversations_title_source",
            "conversations",
            "title_source IN ('auto_pending', 'auto', 'manual', 'legacy')",
        )
    ]
    assert statements == [
        "UPDATE conversations SET title_source = 'auto_pending' WHERE BTRIM(title) = ''"
    ]
