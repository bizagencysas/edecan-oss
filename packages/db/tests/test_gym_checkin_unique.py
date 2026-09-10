"""C4: `workout_plans` y `gym_checkins` son únicos por (tenant_id, user_id, fecha).

Backstop de base de datos para la carrera del check-in de gym (dos POST
concurrentes del mismo día). Sin abrir Postgres: inspecciona `Base.metadata`
(los `UniqueConstraint` del ORM) y la migración `0071` para que el contrato del
modelo y el de la migración queden atados a la misma garantía.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from edecan_db.models import Base
from sqlalchemy import UniqueConstraint


def _unique_columns(table_name: str) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in Base.metadata.tables[table_name].constraints
        if isinstance(constraint, UniqueConstraint)
    }


def _load_migration():
    path = Path(__file__).parents[1] / "alembic/versions/0071_gym_checkin_unique_dia.py"
    spec = importlib.util.spec_from_file_location("gym_checkin_unique_dia_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def test_workout_plans_unico_por_dia() -> None:
    assert ("tenant_id", "user_id", "fecha") in _unique_columns("workout_plans")


def test_gym_checkins_unico_por_dia() -> None:
    assert ("tenant_id", "user_id", "fecha") in _unique_columns("gym_checkins")


def test_migracion_declara_ambas_constraints_y_cadena() -> None:
    migration = _load_migration()
    assert migration.revision == "0071_gym_checkin_unique_dia"
    assert migration.down_revision == "0070_job_outbox_bot_runs"
    fuente = (Path(__file__).parents[1] / "alembic/versions/0071_gym_checkin_unique_dia.py").read_text(
        encoding="utf-8"
    )
    assert "uq_workout_plans_tenant_id_user_id_fecha" in fuente
    assert "uq_gym_checkins_tenant_id_user_id_fecha" in fuente
    # ON CONFLICT del router solo funciona si la restricción es real en la DB.
    assert "create_unique_constraint" in fuente