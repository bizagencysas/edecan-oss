"""Test de integración de Row-Level Security contra Postgres real (§2, §10.3).

Marcado `@pytest.mark.integration`. Se salta automáticamente si `DATABASE_URL`
no está configurada o si no hay un Postgres alcanzable ahí (por ejemplo, en
una máquina sin `docker compose up -d postgres` corriendo) -- ver
`_skip_reason()` más abajo.

Qué verifica: crea dos tenants nuevos, escribe una fila de `usage_events` para
cada uno, y confirma que una sesión con `app.tenant_id` fijado al tenant A
(`edecan_db.session.get_session(tenant_a.id)`) NO ve la fila del tenant B (ni
al revés), que la sesión "dueño" (`get_session(None)`, sin RLS) sí ve ambas,
y que un intento de insertar una fila falsificando el `tenant_id` del otro
tenant desde una sesión ajena es rechazado por la base de datos.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def _es_alcanzable(url: str) -> bool:
    import asyncpg

    # asyncpg no entiende el sufijo `+asyncpg` que usa SQLAlchemy en la URL.
    dsn = url.replace("postgresql+asyncpg://", "postgresql://", 1)
    try:
        conn = await asyncpg.connect(dsn, timeout=2)
    except Exception:
        return False
    await conn.close()
    return True


def _skip_reason() -> str | None:
    url = _database_url()
    if not url:
        return "DATABASE_URL no está configurada"
    try:
        alcanzable = asyncio.run(_es_alcanzable(url))
    except Exception as exc:  # pragma: no cover - solo diagnóstico del skip
        return f"No se pudo probar la conexión a DATABASE_URL: {exc}"
    if not alcanzable:
        return f"Postgres no está alcanzable en DATABASE_URL={url!r}"
    return None


_SKIP_REASON = _skip_reason()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or ""),
]


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica `0001_initial` (idempotente: no-op si ya está aplicada)."""
    from alembic.command import upgrade
    from alembic.config import Config

    aquí = Path(__file__).resolve().parents[1]  # packages/db/
    cfg = Config(str(aquí / "alembic.ini"))
    cfg.set_main_option("script_location", str(aquí / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # `command.upgrade` es sync pero `env.py` llama `asyncio.run(...)` por
    # dentro -- correrlo en un hilo aparte evita pisar el loop de
    # pytest-asyncio de este mismo test.
    await asyncio.to_thread(upgrade, cfg, "head")


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch):
    """Prepara `edecan_db.settings`/`engine` para apuntar a `DATABASE_URL`,
    aplica migraciones, y limpia sus cachés `lru_cache` al terminar."""
    from edecan_db import engine as engine_module
    from edecan_db import settings as settings_module

    database_url = _database_url()
    assert database_url  # el skipif del módulo ya garantizó que hay una

    monkeypatch.setenv("DATABASE_URL", database_url)
    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()

    await _aplicar_migraciones(database_url)

    yield

    settings_module.get_settings.cache_clear()
    engine_module.get_engine.cache_clear()


async def _crear_tenant_con_usage_event(session, *, slug: str, quantity: int) -> UUID:
    from edecan_db.models import Tenant, UsageEvent

    tenant = Tenant(name=f"RLS test {slug}", slug=slug, plan_key="free_selfhost")
    session.add(tenant)
    await session.flush()  # asigna tenant.id

    session.add(UsageEvent(tenant_id=tenant.id, kind="llm_tokens", quantity=quantity))
    await session.flush()
    return tenant.id


async def test_tenant_a_no_ve_filas_de_tenant_b(db):
    from edecan_db.models import Tenant, UsageEvent
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]

    async with get_session(None) as session:
        tenant_a_id = await _crear_tenant_con_usage_event(
            session, slug=f"rls-test-a-{sufijo}", quantity=100
        )
        tenant_b_id = await _crear_tenant_con_usage_event(
            session, slug=f"rls-test-b-{sufijo}", quantity=200
        )

    try:
        # La sesión de tenant A solo debe ver su propia fila.
        async with get_session(tenant_a_id) as session:
            rows = (await session.execute(select(UsageEvent))).scalars().all()
            assert [r.tenant_id for r in rows] == [tenant_a_id]
            assert rows[0].quantity == 100

        # La sesión de tenant B solo debe ver la suya.
        async with get_session(tenant_b_id) as session:
            rows = (await session.execute(select(UsageEvent))).scalars().all()
            assert [r.tenant_id for r in rows] == [tenant_b_id]
            assert rows[0].quantity == 200

        # El dueño (sin tenant_id, `get_session(None)`) bypassa RLS -- sin
        # FORCE ROW LEVEL SECURITY -- y ve las dos (§2).
        async with get_session(None) as session:
            rows = (
                (
                    await session.execute(
                        select(UsageEvent).where(
                            UsageEvent.tenant_id.in_([tenant_a_id, tenant_b_id])
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert {r.tenant_id for r in rows} == {tenant_a_id, tenant_b_id}

        # Falsificar una fila de tenant B desde la sesión de tenant A debe
        # ser rechazado por la política (misma expresión sirve de WITH CHECK).
        with pytest.raises(DBAPIError, match="row-level security"):
            async with get_session(tenant_a_id) as session:
                session.add(UsageEvent(tenant_id=tenant_b_id, kind="llm_tokens", quantity=999))
                await session.flush()
    finally:
        async with get_session(None) as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_([tenant_a_id, tenant_b_id])))
