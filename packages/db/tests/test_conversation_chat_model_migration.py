"""`0027_conversation_chat_model` — columnas del selector de modelos del chat.

Se comprueba lo que hace que estrenar el selector NO cambie ninguna
conversación existente: las dos columnas son nullable y sin backfill (NULL =
automático), y solo `chat_effort` lleva CHECK — `chat_model` no, porque el
catálogo es dato que rota sin migración.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0027_conversation_chat_model.py"
    spec = importlib.util.spec_from_file_location("conversation_chat_model_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_la_migracion_va_encadenada_a_la_ultima() -> None:
    migration = _load_migration()

    assert migration.revision == "0027_conversation_chat_model"
    assert migration.down_revision == "0026_conversations_main"


def test_upgrade_agrega_dos_columnas_nullable_sin_backfill(monkeypatch) -> None:
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

    assert [args[0] for args in added] == ["conversations", "conversations"]
    columnas = [args[1] for args in added]
    assert [column.name for column in columnas] == ["chat_model", "chat_effort"]
    # NULL = automático: nullable, sin server_default y sin UPDATE de backfill.
    assert all(column.nullable for column in columnas)
    assert all(column.server_default is None for column in columnas)
    assert statements == []
    # Solo el enum del Esfuerzo lleva CHECK; el id del modelo no, porque el
    # catálogo (`config/modelos.yml` -> `modelos_chat`) cambia sin migración.
    assert constraints == [
        (
            "ck_conversations_chat_effort",
            "conversations",
            "chat_effort IN ('bajo', 'medio', 'alto')",
        )
    ]


def test_downgrade_deshace_el_check_antes_de_las_columnas(monkeypatch) -> None:
    migration = _load_migration()
    orden: list[str] = []
    monkeypatch.setattr(
        migration.op, "drop_constraint", lambda name, table, **kw: orden.append(f"check:{name}")
    )
    monkeypatch.setattr(
        migration.op, "drop_column", lambda table, column: orden.append(f"col:{column}")
    )

    migration.downgrade()

    assert orden == [
        "check:ck_conversations_chat_effort",
        "col:chat_effort",
        "col:chat_model",
    ]


def test_el_modelo_orm_declara_las_mismas_columnas_y_el_mismo_check() -> None:
    from edecan_db.models import Conversation

    assert Conversation.__table__.c.chat_model.nullable is True
    assert Conversation.__table__.c.chat_effort.nullable is True
    checks = {
        str(constraint.sqltext)
        for constraint in Conversation.__table__.constraints
        if hasattr(constraint, "sqltext")
    }
    assert "chat_effort IN ('bajo', 'medio', 'alto')" in checks
    assert not any("chat_model" in text for text in checks)
