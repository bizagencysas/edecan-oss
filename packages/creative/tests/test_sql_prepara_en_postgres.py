"""Le pide a un Postgres REAL que prepare cada sentencia de `edecan_creative.social`.

Por qué existe: el resto de los tests de este paquete sustituye la sesión por una falsa que
guarda el SQL en una lista y nunca lo ejecuta. Eso valida los parámetros, pero NO valida que
Postgres sepa tipar la sentencia -- y ese hueco dejó pasar a producción un
`jsonb_build_object(:key, ...)` sin `CAST`: al ser variádica sobre `"any"`, Postgres no puede
inferir el tipo del parámetro y asyncpg (que prepara los statements antes de ejecutarlos)
falla con `ProgrammingError: could not determine data type of parameter $1`. Peor aún, ese
error deja la transacción del turno envenenada, así que TODA herramienta que corriera después
moría con `InFailedSQLTransactionError` -- un solo bug, una cascada de errores en pantalla.

`PREPARE` valida tipos y nombres de columna sin escribir una sola fila, así que este test no
necesita tenants, usuarios ni limpieza posterior.

Marcado `integration`: se salta solo si no hay `DATABASE_URL` o no hay un Postgres alcanzable
ahí, igual que `packages/db/tests/test_rls.py`.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from edecan_creative.social import (
    SQL_GET_EDITORIAL_PROFILE,
    SQL_LISTAR_DESTINOS,
    SQL_SAVE_AGENDA_STATE,
    SQL_SAVE_EDITORIAL_PROFILE,
)

pytestmark = pytest.mark.integration

SENTENCIAS = {
    "get_editorial_profile": SQL_GET_EDITORIAL_PROFILE,
    "destinos_configurados": SQL_LISTAR_DESTINOS,
    "save_editorial_profile": SQL_SAVE_EDITORIAL_PROFILE,
    "save_agenda_state": SQL_SAVE_AGENDA_STATE,
}


def _dsn() -> str | None:
    url = os.environ.get("DATABASE_URL")
    # asyncpg no entiende el sufijo `+asyncpg` que usa SQLAlchemy en la URL.
    return url.replace("postgresql+asyncpg://", "postgresql://", 1) if url else None


async def _alcanzable(dsn: str) -> bool:
    import asyncpg

    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


def _skip_reason() -> str | None:
    dsn = _dsn()
    if not dsn:
        return "DATABASE_URL no está configurada"
    if not asyncio.run(_alcanzable(dsn)):
        return "no hay un Postgres alcanzable en DATABASE_URL"
    return None


def _a_parametros_posicionales(sql: str) -> str:
    """`:nombre` (estilo SQLAlchemy) -> `$1..$n` (estilo asyncpg/PREPARE).

    Un mismo `:nombre` repetido reusa su número, igual que hace SQLAlchemy al compilar.
    """
    numeros: dict[str, int] = {}

    def _sustituir(match: re.Match[str]) -> str:
        nombre = match.group(1)
        if nombre not in numeros:
            numeros[nombre] = len(numeros) + 1
        return f"${numeros[nombre]}"

    # `\b:nombre` y no `::tipo`: el cast de Postgres no debe confundirse con un parámetro.
    return re.sub(r"(?<!:):([a-z_][a-z0-9_]*)", _sustituir, sql)


@pytest.mark.skipif(_skip_reason() is not None, reason=_skip_reason() or "")
@pytest.mark.parametrize("nombre", sorted(SENTENCIAS))
def test_postgres_puede_preparar_la_sentencia(nombre: str) -> None:
    import asyncpg

    sql = _a_parametros_posicionales(SENTENCIAS[nombre])

    async def _preparar() -> None:
        conn = await asyncpg.connect(_dsn())
        try:
            # `prepare` NO ejecuta: solo le pide a Postgres que resuelva tipos y columnas.
            await conn.prepare(sql)
        finally:
            await conn.close()

    try:
        asyncio.run(_preparar())
    except asyncpg.PostgresError as exc:  # pragma: no cover - solo si alguien rompe el SQL
        pytest.fail(
            f"Postgres no pudo preparar el SQL de `{nombre}`: {exc}\n\n"
            "Si dice 'could not determine data type of parameter', a algún parámetro dentro "
            "de una función variádica (`jsonb_build_object`, `concat`, ...) le falta un CAST "
            "explícito.\n\n"
            f"{sql}"
        )
