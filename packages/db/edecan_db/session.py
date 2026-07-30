"""Sesión async con aislamiento multi-tenant vía Row-Level Security (§2, §10.3)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from edecan_db.engine import get_sessionmaker

APP_ROLE = "app_user"


@asynccontextmanager
async def get_session(
    tenant_id: UUID | None,
    *,
    sessionmaker: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncIterator[AsyncSession]:
    """Abre una transacción async, opcionalmente restringida a un tenant.

    Si `tenant_id` no es `None`, ejecuta `SET LOCAL ROLE app_user` y fija
    `app.tenant_id` para el resto de la transacción — esto activa las
    políticas `tenant_isolation` (Row-Level Security) de cada tabla
    tenant-scoped (ver la migración `0001_initial`). `app_user` es un rol
    `NOLOGIN` sin `BYPASSRLS`; el rol con el que se conecta la app debe ser
    miembro de `app_user` (la migración `0001_initial` se lo concede a quien
    la ejecute).

    Si `tenant_id` es `None` la sesión se queda con el rol "dueño" (owner)
    de la conexión, que **bypassa RLS** — así es como debe conectarse el
    worker (§2): siempre filtrando manualmente por `tenant_id` del job.

    `app.tenant_id` se fija con `SELECT set_config('app.tenant_id', :tid, true)`
    en lugar de `SET LOCAL app.tenant_id = :tid` porque Postgres no acepta
    parámetros ligados (`$1`) dentro de una sentencia `SET`; `set_config(...,
    is_local=true)` es equivalente a `SET LOCAL` pero sí admite bind
    parameters, evitando tener que interpolar el UUID a mano en el SQL.

    La transacción hace commit al salir normalmente del bloque `async with`
    y rollback si se propaga una excepción.
    """
    maker = sessionmaker or get_sessionmaker()
    async with maker() as session:
        async with session.begin():
            if tenant_id is not None:
                await session.execute(text(f"SET LOCAL ROLE {APP_ROLE}"))
                await session.execute(
                    text("SELECT set_config('app.tenant_id', :tid, true)"),
                    {"tid": str(tenant_id)},
                )
            yield session
