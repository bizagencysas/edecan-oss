"""`0031_conv_context_cleared` — columna del comando local `/clear`.

(La revisión se llama así, en corto; el archivo que la contiene conserva el
nombre largo `0031_conversation_context_cleared.py`.)

Se comprueba lo que hace que estrenar la columna no cambie ninguna
conversación existente (nullable, sin backfill, `NULL` = nunca se limpió) y
que la migración quede encadenada a la última (`0030_social_drafts_verification`).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0031_conversation_context_cleared.py"
    spec = importlib.util.spec_from_file_location("conversation_context_cleared_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_la_migracion_va_encadenada_a_la_ultima() -> None:
    migration = _load_migration()

    # El id es MÁS CORTO que el nombre del archivo a propósito: `version_num` es
    # varchar(32) y el nombre completo (33 chars) reventaba el arranque entero de la
    # app instalada (02-ago-2026). Ver el comentario en la propia migración.
    assert migration.revision == "0031_conv_context_cleared"
    assert migration.down_revision == "0030_social_drafts_verification"


def test_upgrade_agrega_una_columna_nullable_sin_backfill(monkeypatch) -> None:
    migration = _load_migration()
    added: list[tuple] = []
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "add_column", lambda *args: added.append(args))
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    assert [args[0] for args in added] == ["conversations"]
    column = added[0][1]
    assert column.name == "context_cleared_at"
    # NULL = nunca se limpió: nullable, sin server_default y sin UPDATE de
    # backfill -- estrenar la columna no toca ninguna conversación existente.
    assert column.nullable is True
    assert column.server_default is None
    assert statements == []


def test_downgrade_quita_la_columna(monkeypatch) -> None:
    migration = _load_migration()
    dropped: list[tuple] = []
    monkeypatch.setattr(
        migration.op, "drop_column", lambda table, column: dropped.append((table, column))
    )

    migration.downgrade()

    assert dropped == [("conversations", "context_cleared_at")]


def test_el_modelo_orm_declara_la_misma_columna_nullable() -> None:
    from edecan_db.models import Conversation

    assert Conversation.__table__.c.context_cleared_at.nullable is True
    assert Conversation.__table__.c.context_cleared_at.server_default is None
