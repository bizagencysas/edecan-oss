"""Criterio de `edecan-migracion-usage-costo-usd`.

`usage_events` guarda `quantity` (tokens, segundos, bytes) pero no el dinero.
Reconstruir el costo después exige saber qué precio regía ese día, y los
precios cambian: la única forma honesta es escribirlo en el momento. Este
criterio comprueba la migración nueva y el modelo ORM sin abrir ninguna
conexión — `ScriptDirectory` solo importa el `.py` de la revisión.

Falla hoy: la columna no existe y la cabeza de migraciones sigue siendo 0025.
"""

from __future__ import annotations

import sys
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config
from alembic.script import ScriptDirectory
from edecan_db.models import UsageEvent

_RAIZ = Path(__file__).resolve().parents[4]
_ALEMBIC_INI = _RAIZ / "packages/db/alembic.ini"
_COLUMNA = "costo_usd"


def _script_directory() -> ScriptDirectory:
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option("script_location", str(_RAIZ / "packages/db/alembic"))
    return ScriptDirectory.from_config(config)


def _revisar_modelo() -> str:
    columnas = UsageEvent.__table__.columns
    if _COLUMNA not in columnas:
        return f"el modelo UsageEvent no tiene la columna {_COLUMNA!r}"
    columna = columnas[_COLUMNA]
    if not isinstance(columna.type, sa.Numeric):
        return f"{_COLUMNA} debería ser NUMERIC, es {columna.type!r}"
    if columna.nullable:
        return f"{_COLUMNA} debe ser NOT NULL (un evento sin costo conocido vale 0)"
    if columna.server_default is None:
        return f"{_COLUMNA} necesita server_default para que las filas viejas queden en 0"
    return ""


def _revisar_migracion() -> str:
    directorio = _script_directory()
    cabezas = list(directorio.get_heads())
    if len(cabezas) != 1:
        return f"hay {len(cabezas)} cabezas de migración: {cabezas}"
    cabeza = directorio.get_revision(cabezas[0])
    if cabeza.revision == "0025_social_editorial":
        return "la cabeza sigue siendo 0025: no se agregó ninguna migración"
    if cabeza.down_revision != "0025_social_editorial":
        return (
            f"la migración nueva desciende de {cabeza.down_revision!r}; "
            "debe encadenarse justo después de 0025_social_editorial"
        )

    fuente = Path(cabeza.module.__file__ or "").read_text(encoding="utf-8")
    if "usage_events" not in fuente or _COLUMNA not in fuente:
        return "la migración nueva no menciona usage_events y costo_usd"
    if "add_column" not in fuente:
        return "la migración nueva no agrega la columna con op.add_column"
    if "drop_column" not in fuente:
        return "la migración nueva no revierte la columna en downgrade()"
    for nombre in ("upgrade", "downgrade"):
        if not callable(getattr(cabeza.module, nombre, None)):
            return f"la migración nueva no define {nombre}()"
    return ""


def main() -> int:
    for problema in (_revisar_modelo(), _revisar_migracion()):
        if problema:
            print(problema)
            return 1
    print("ok: usage_events.costo_usd está en el modelo y en una migración encadenada a 0025")
    return 0


if __name__ == "__main__":
    sys.exit(main())
