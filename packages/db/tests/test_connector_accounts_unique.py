"""Test de integración: unicidad GLOBAL (cruzando tenants) de números de Twilio.

Marcado `@pytest.mark.integration`, mismo criterio de skip que `test_rls.py`
(se salta si no hay un Postgres real alcanzable en `DATABASE_URL`) — la
lógica de fixture/skip se duplica a propósito en vez de importarla de
`test_rls.py`: cada módulo de test de integración de este paquete es
autocontenido (ver también el propio `test_rls.py`), así ninguno depende de
que otro no cambie su forma interna.

Qué verifica (hallazgo de auditoría aislamiento-multi-tenant, ver
`edecan_db.models.ConnectorAccount`): antes de la migración
`0002_twilio_number_global_unique`, `connector_accounts` solo tenía
`UNIQUE(tenant_id, connector_key, external_account_id)` — dos tenants
DISTINTOS podían registrar el mismo número E.164 de Twilio sin que la base
de datos lo impidiera. El índice único parcial nuevo (`connector_key='twilio'`)
lo prohíbe a nivel de base de datos, sin afectar a los demás conectores
(OAuth), que no tienen esa garantía real de unicidad en `external_account_id`.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.integration


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


async def _es_alcanzable(url: str) -> bool:
    import asyncpg

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
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    from alembic.command import upgrade
    from alembic.config import Config

    aquí = Path(__file__).resolve().parents[1]  # packages/db/
    cfg = Config(str(aquí / "alembic.ini"))
    cfg.set_main_option("script_location", str(aquí / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
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


async def _crear_tenant(session, *, slug: str) -> object:
    from edecan_db.models import Tenant

    tenant = Tenant(name=f"Unique test {slug}", slug=slug, plan_key="hosted_pro")
    session.add(tenant)
    await session.flush()  # asigna tenant.id
    return tenant.id


async def test_dos_tenants_no_pueden_reclamar_el_mismo_numero_twilio(db):
    """Núcleo del hallazgo: tenant B ya NO puede "robar" un número que tenant
    A tiene conectado — antes de la migración `0002`, este INSERT pasaba
    silenciosamente y `_resolve_tenant_by_number` (`edecan_premium.twilio_router`)
    resolvía el número contra el registro más reciente (tenant B), dejando la
    línea de tenant A fuera de servicio.
    """
    from edecan_db.models import ConnectorAccount, Tenant
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    numero = "+15550000001"

    async with get_session(None) as session:
        tenant_a_id = await _crear_tenant(session, slug=f"twilio-unique-a-{sufijo}")
        tenant_b_id = await _crear_tenant(session, slug=f"twilio-unique-b-{sufijo}")

    try:
        async with get_session(tenant_a_id) as session:
            session.add(
                ConnectorAccount(
                    tenant_id=tenant_a_id,
                    connector_key="twilio",
                    external_account_id=numero,
                    display_name=numero,
                    scopes=["ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
                )
            )
            await session.flush()

        with pytest.raises(DBAPIError, match="uq_connector_accounts_twilio_external_account_id"):
            async with get_session(tenant_b_id) as session:
                session.add(
                    ConnectorAccount(
                        tenant_id=tenant_b_id,
                        connector_key="twilio",
                        external_account_id=numero,
                        display_name=numero,
                        scopes=["ACyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"],
                    )
                )
                await session.flush()
    finally:
        async with get_session(None) as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_([tenant_a_id, tenant_b_id])))


async def test_dos_tenants_pueden_tener_numeros_twilio_distintos(db):
    """Sanity check negativo: el índice único parcial no debe impedir que dos
    tenants distintos conecten números DIFERENTES."""
    from edecan_db.models import ConnectorAccount, Tenant
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]

    async with get_session(None) as session:
        tenant_a_id = await _crear_tenant(session, slug=f"twilio-distinct-a-{sufijo}")
        tenant_b_id = await _crear_tenant(session, slug=f"twilio-distinct-b-{sufijo}")

    try:
        async with get_session(tenant_a_id) as session:
            session.add(
                ConnectorAccount(
                    tenant_id=tenant_a_id,
                    connector_key="twilio",
                    external_account_id="+15550000002",
                    display_name="+15550000002",
                    scopes=["ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"],
                )
            )
            await session.flush()

        # No debe lanzar: número distinto, tenant distinto.
        async with get_session(tenant_b_id) as session:
            session.add(
                ConnectorAccount(
                    tenant_id=tenant_b_id,
                    connector_key="twilio",
                    external_account_id="+15550000003",
                    display_name="+15550000003",
                    scopes=["ACyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyyy"],
                )
            )
            await session.flush()
    finally:
        async with get_session(None) as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_([tenant_a_id, tenant_b_id])))


async def test_indice_unico_no_aplica_a_conectores_no_twilio(db):
    """Regresión: el índice es PARCIAL (`connector_key='twilio'`) — dos
    tenants distintos deben poder seguir compartiendo el mismo
    `external_account_id` en un conector OAuth (p. ej. si el hash de
    `_bundle_account_hint` coincidiera por casualidad), tal como podían antes
    de esta migración.
    """
    from edecan_db.models import ConnectorAccount, Tenant
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    hint = "coincidenciahash1"

    async with get_session(None) as session:
        tenant_a_id = await _crear_tenant(session, slug=f"oauth-shared-a-{sufijo}")
        tenant_b_id = await _crear_tenant(session, slug=f"oauth-shared-b-{sufijo}")

    try:
        async with get_session(tenant_a_id) as session:
            session.add(
                ConnectorAccount(
                    tenant_id=tenant_a_id,
                    connector_key="google",
                    external_account_id=hint,
                    display_name="Google",
                    scopes=["gmail.readonly"],
                )
            )
            await session.flush()

        # No debe lanzar: `connector_key != 'twilio'`, fuera del alcance del
        # índice único parcial nuevo.
        async with get_session(tenant_b_id) as session:
            session.add(
                ConnectorAccount(
                    tenant_id=tenant_b_id,
                    connector_key="google",
                    external_account_id=hint,
                    display_name="Google",
                    scopes=["gmail.readonly"],
                )
            )
            await session.flush()
    finally:
        async with get_session(None) as session:
            await session.execute(delete(Tenant).where(Tenant.id.in_([tenant_a_id, tenant_b_id])))
