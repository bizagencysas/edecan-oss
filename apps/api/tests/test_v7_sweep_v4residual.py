"""Barrido v7 — residual v4/v5 (WP-V7-07): `devices`/push (APNs/FCM), ERP inventario, ads,
mensajes. Ver `docs/cumplimiento/barrido-v7-v4residual.md` para el informe completo del
paquete de trabajo (qué se revisó, qué se encontró, qué se corrigió).

Este archivo complementa —no repite— la cobertura ya exhaustiva con `FakeSession`/`FakeVault`
de `test_erp_router.py`/`test_ads_router.py`/`test_devices_router.py`/`test_devices_push.py`/
`test_mensajes_router.py`: aquellos prueban la orquestación HTTP con SQL MOCKEADO (rápidos,
deterministas, corren siempre). Los tests de este módulo se dividen en dos grupos:

1. **Empíricos, marcados `@pytest.mark.integration`** (se saltan solos si `DATABASE_URL` no
   apunta a un Postgres real alcanzable, mismo patrón — duplicado a propósito,
   `ARCHITECTURE.md` §10.1: "cada módulo de test de integración es autocontenido" — que
   `apps/api/tests/test_repo_sql_integration.py`/`packages/db/tests/test_rls.py`): llaman
   DIRECTAMENTE a las funciones de los routers (`erp.create_producto`, `devices.create_device`,
   `ads.confirmar_borrador`, etc. — sin pasar por FastAPI/ASGI, pero es el MISMO código de
   producción) con una `AsyncSession` real contra una base migrada a `head`
   (`0006_v4_expansion`/`0007_v5_expansion`). Objetivo explícito: que un desajuste entre el SQL
   crudo de un router y la migración real sea IMPOSIBLE de pasar por alto — la misma clase de
   bug crítico que v6 encontró en `apps/api/edecan_api/routers/reuniones.py` (`HOTFIXES_
   PENDIENTES.md`: escribía contra columnas que no existían, invisible a los tests porque su
   `FakeSession` asumía el mismo esquema equivocado que el código). `test_registrar_movimiento_
   for_update_evita_perdida_de_actualizaciones_concurrentes` en particular es la verificación
   EMPÍRICA (no solo textual) de que el fix crítico v4 del `SELECT ... FOR UPDATE`
   (`DIRECCION_ACTUAL.md`, "v4 completado") sigue intacto: lanza movimientos de stock
   CONCURRENTES de verdad (sesiones/conexiones Postgres distintas) sobre el mismo producto.

2. **Estructurales, sin marcar** (corren siempre, no necesitan Postgres): centinelas baratos
   que verifican por INSPECCIÓN (no por request HTTP) las dos garantías que este paquete de
   trabajo debía re-confirmar: (a) `edecan_worker.push` sigue siendo bring-your-own puro —
   `Settings` del worker no declara ningún campo con forma de credencial de push de plataforma,
   y `cargar_credenciales_push` ni siquiera ACEPTA un parámetro `settings`; (b) paridad de
   flags de plan entre cada router (`erp.py`/`ads.py`/`mensajes.py`) y las tools de agente
   correspondientes (`GestionarInventarioTool`/`EstadoInventarioTool`,
   `AdsResumenTool`/`AdsPrepararCampanaTool`, `EnviarMensajeTool`/`LeerMensajesTool`) — mismo
   propósito que `apps/api/tests/test_v6_sweep_flags.py` para otras superficies, pero acotado a
   las tres de este paquete de trabajo.

Para correr los tests empíricos: un Postgres desechable (p. ej. `docker run -d --name
edecan-v7-resid-pg -e POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan -e POSTGRES_DB=edecan
-p 55476:5432 pgvector/pgvector:pg16`) + `DATABASE_URL=postgresql+asyncpg://edecan:edecan@
localhost:55476/edecan` exportada, luego `uv run --all-packages pytest
apps/api/tests/test_v7_sweep_v4residual.py`. Sin `DATABASE_URL`, solo corren los
estructurales (los empíricos se saltan con un `skip` explícito, nunca fallan).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException, Response
from sqlalchemy import text

from edecan_api.config import Settings
from edecan_api.deps import CurrentUser, TenantCtx, flags_for_plan
from edecan_api.repo import SqlRepo
from edecan_api.routers import ads, devices, erp

# `hosted_pro` trae `erp.inventory`/`tools.ads`/`notifications.push`/`connectors.messaging`
# en `True` a la vez (`edecan_schemas.plans.PLANES`) — un solo plan_key alcanza para las 3
# superficies de este paquete de trabajo.
_PLAN = "hosted_pro"


def _current_user(
    tenant_id: uuid.UUID, user_id: uuid.UUID, *, plan_key: str = _PLAN
) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        tenant=TenantCtx(tenant_id=tenant_id, plan_key=plan_key, flags=flags_for_plan(plan_key)),
    )


# ---------------------------------------------------------------------------
# Setup de integración — duplicado a propósito de `test_repo_sql_integration.py`
# (`ARCHITECTURE.md` §10.1: cada módulo de test de integración es autocontenido).
# ---------------------------------------------------------------------------


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
# Aplicado manualmente (`@pytest.mark.integration` + `@pytest.mark.skipif(...)`) a cada test
# empírico del GRUPO 1 más abajo — a propósito NO como `pytestmark` de módulo (a diferencia de
# `test_repo_sql_integration.py`/`test_rls.py`, que son 100% de integración): este archivo
# mezcla los empíricos con los estructurales del GRUPO 2, que deben poder correr SIEMPRE, sin
# Postgres.


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    from alembic.command import upgrade
    from alembic.config import Config

    db_dir = Path(__file__).resolve().parents[3] / "packages" / "db"
    cfg = Config(str(db_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # `command.upgrade` es sync pero `env.py` llama `asyncio.run(...)` por dentro -- correrlo
    # en un hilo aparte evita pisar el loop de pytest-asyncio de este mismo test.
    await asyncio.to_thread(upgrade, cfg, "head")


@pytest.fixture
async def db(monkeypatch: pytest.MonkeyPatch):
    """Prepara `edecan_db.settings`/`engine` para apuntar a `DATABASE_URL`, aplica
    migraciones, y limpia sus cachés `lru_cache` al terminar."""
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


async def _seed_tenant_y_usuario(
    sufijo: str, *, plan_key: str = _PLAN
) -> tuple[uuid.UUID, uuid.UUID]:
    from edecan_db.session import get_session

    async with get_session(None) as session:
        repo = SqlRepo(session)
        tenant = await repo.create_tenant(
            name=f"v7 sweep {sufijo}", slug=f"v7-sweep-{sufijo}", plan_key=plan_key
        )
        user = await repo.create_user(
            email=f"v7-sweep-{sufijo}@example.com", password_hash="x" * 20
        )
        await repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
    return tenant["id"], user["id"]


async def _cleanup(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from edecan_db.session import get_session

    # El tenant primero: `ON DELETE CASCADE` (0001_initial) se lleva todas las tablas
    # tenant-scoped que el test haya poblado (products, stock_moves, ad_drafts, devices,
    # connector_accounts, oauth_tokens, tenant_keys, audit_log...).
    async with get_session(None) as session:
        await session.execute(text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)})
    async with get_session(None) as session:
        await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})


# ===========================================================================
# GRUPO 1 — Empíricos contra Postgres real (Barrido D: esquema).
# ===========================================================================


# ---------------------------------------------------------------------------
# ERP / inventario (`erp.py` + `packages/business/edecan_business/inventory.py`)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_erp_inventory_lifecycle_contra_postgres_real(db) -> None:
    """`products`/`stock_moves` (`0006_v4_expansion`) ejercitadas de punta a punta con el
    código REAL del router (`erp.create_producto`/`list_productos`/`update_producto`/
    `create_movimiento`/`get_resumen`) contra Postgres real — si alguna columna del SQL
    crudo de `edecan_business.inventory` no coincidiera con la migración real, esto revienta
    con un `UndefinedColumnError` en vez de pasar en silencio como con un `FakeSession`."""
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    try:
        async with get_session(tenant_id) as session:
            creado = await erp.create_producto(
                erp.ProductoCreateIn(
                    sku=f"sku-{sufijo}",
                    nombre="Widget de prueba",
                    precio=Decimal("9.99"),
                    costo=Decimal("4.00"),
                    stock_minimo=Decimal("2"),
                ),
                current_user,
                session,
            )
            assert creado["sku"] == f"sku-{sufijo}".upper()  # `_normalizar_sku`
            assert Decimal(str(creado["stock"])) == Decimal("0")  # nace SIEMPRE en 0
            product_id = creado["id"]

            # sku duplicado -> 409 real (edecan_business.inventory.SkuDuplicadoError).
            with pytest.raises(erp.SkuDuplicadoError):
                await erp.crear_producto(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    sku=f"sku-{sufijo}",
                    nombre="Otro con el mismo sku",
                )

            listado = await erp.list_productos(None, None, current_user, session)
            assert any(p["id"] == product_id for p in listado)

            actualizado = await erp.update_producto(
                product_id, erp.ProductoUpdateIn(precio=Decimal("12.50")), current_user, session
            )
            assert Decimal(str(actualizado["precio"])) == Decimal("12.50")

            entrada = await erp.create_movimiento(
                product_id,
                erp.MovimientoIn(delta=Decimal("10"), motivo="compra"),
                current_user,
                session,
            )
            assert Decimal(str(entrada["producto"]["stock"])) == Decimal("10")

            salida = await erp.create_movimiento(
                product_id,
                erp.MovimientoIn(delta=Decimal("-3"), motivo="venta"),
                current_user,
                session,
            )
            assert Decimal(str(salida["producto"]["stock"])) == Decimal("7")

            # Dejaría stock negativo con motivo != 'ajuste' -> rechazado, contra Postgres real.
            # `erp.create_movimiento` es el ENDPOINT (atrapa `StockInsuficienteError` y la
            # traduce a `HTTPException(400)`, ver el router) — a diferencia de la llamada de
            # arriba a `erp.crear_producto` (la función de negocio pura, que sí deja escapar
            # `SkuDuplicadoError` tal cual).
            with pytest.raises(HTTPException) as exc_info:
                await erp.create_movimiento(
                    product_id,
                    erp.MovimientoIn(delta=Decimal("-100"), motivo="venta"),
                    current_user,
                    session,
                )
            assert exc_info.value.status_code == 400
            # ... pero SÍ se permite con motivo='ajuste' (corrección administrativa explícita).
            ajuste = await erp.create_movimiento(
                product_id,
                erp.MovimientoIn(delta=Decimal("-100"), motivo="ajuste"),
                current_user,
                session,
            )
            assert Decimal(str(ajuste["producto"]["stock"])) == Decimal("-93")

            desactivado = await erp.update_producto(
                product_id, erp.ProductoUpdateIn(activo=False), current_user, session
            )
            assert desactivado["activo"] is False

            resumen = await erp.get_resumen(current_user, session)
            # El producto quedó desactivado -> no cuenta en el resumen de inventario "vivo".
            assert resumen["total_skus"] == 0
    finally:
        await _cleanup(tenant_id, user_id)


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_registrar_movimiento_for_update_evita_perdida_de_actualizaciones_concurrentes(
    db,
) -> None:
    """Verificación EMPÍRICA (no solo el texto `"FOR UPDATE"` en el SQL, que ya cubre
    `packages/business/tests/test_inventory.py::test_registrar_movimiento_select_inicial_usa_
    for_update` con un `FakeSession`) del fix crítico v4 documentado en `DIRECCION_ACTUAL.md`
    ("v4 completado", hallazgo #2): sin el `SELECT ... FOR UPDATE`, dos `registrar_movimiento`
    concurrentes sobre el MISMO producto pueden perder una actualización (ambos leen el mismo
    `stock` viejo antes de que el otro confirme, el segundo `UPDATE` pisa el resultado del
    primero en vez de sumarse). Lanza N tareas concurrentes DE VERDAD —cada una con su propia
    sesión/conexión de Postgres, vía `asyncio.gather`— y confirma que el `stock` final es la
    suma EXACTA de todos los deltas: si el lock de fila no sirviera, este test fallaría de
    forma intermitente con un stock menor al esperado (movimientos "perdidos")."""
    from edecan_business.inventory import crear_producto, registrar_movimiento
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            producto = await crear_producto(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                sku=f"race-{sufijo}",
                nombre="Producto de carrera",
            )
            product_id = producto["id"]

        n_movimientos = 12

        async def _un_ajuste() -> None:
            async with get_session(tenant_id) as session:
                await registrar_movimiento(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    product_id=product_id,
                    delta=Decimal("1"),
                    motivo="ajuste",
                )

        await asyncio.gather(*[_un_ajuste() for _ in range(n_movimientos)])

        async with get_session(tenant_id) as session:
            fila = (
                (
                    await session.execute(
                        text("SELECT stock FROM products WHERE id = :id"), {"id": str(product_id)}
                    )
                )
                .mappings()
                .first()
            )
            assert fila is not None
            assert Decimal(str(fila["stock"])) == Decimal(n_movimientos)

            movimientos = (
                (
                    await session.execute(
                        text("SELECT COUNT(*) AS n FROM stock_moves WHERE product_id = :id"),
                        {"id": str(product_id)},
                    )
                )
                .mappings()
                .first()
            )
            assert movimientos is not None
            assert int(movimientos["n"]) == n_movimientos  # ninguno se perdió ni se duplicó
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# Devices + push (`devices.py`, columnas `push_token`/`push_platform` de `0007_v5_expansion`)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_devices_lifecycle_contra_postgres_real(db) -> None:
    """`devices` (`0003_v2_expansion` + columnas `push_token`/`push_platform` de
    `0007_v5_expansion`) ejercitada con el código REAL del router (`create_device`/
    `heartbeat`/`set_push_token`/`delete_push_token`/`revoke_device`)."""
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    try:
        async with get_session(tenant_id) as session:
            creado = await devices.create_device(
                devices.DeviceIn(
                    nombre="iPhone de prueba",
                    plataforma="ios",
                    kind="mobile",
                    fingerprint=f"fp-{sufijo}",
                ),
                Response(),
                current_user,
                session,
            )
            device_id = creado["id"]
            assert creado["status"] == "active"
            assert creado["push_token"] is None
            assert creado["push_platform"] is None

            await devices.heartbeat(device_id, current_user, session)

            await devices.set_push_token(
                device_id,
                devices.PushTokenIn(push_token="tok-real-pg", push_platform="apns"),
                current_user,
                session,
            )

        async with get_session(tenant_id) as session:
            fila = (
                (
                    await session.execute(
                        text("SELECT push_token, push_platform FROM devices WHERE id = :id"),
                        {"id": str(device_id)},
                    )
                )
                .mappings()
                .first()
            )
            assert fila is not None
            assert fila["push_token"] == "tok-real-pg"
            assert fila["push_platform"] == "apns"

        async with get_session(tenant_id) as session:
            await devices.delete_push_token(device_id, current_user, session)

        async with get_session(tenant_id) as session:
            fila = (
                (
                    await session.execute(
                        text("SELECT push_token, push_platform FROM devices WHERE id = :id"),
                        {"id": str(device_id)},
                    )
                )
                .mappings()
                .first()
            )
            assert fila is not None
            assert fila["push_token"] is None
            assert fila["push_platform"] is None

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            revocado = await devices.revoke_device(device_id, current_user, session, repo)
            assert revocado["status"] == "revoked"
    finally:
        await _cleanup(tenant_id, user_id)


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_devices_push_credentials_roundtrip_contra_postgres_real(db) -> None:
    """`PUT`/`GET`/`DELETE /v1/devices/push/credentials` con un `TokenVault` REAL
    (`LocalKeyProvider` + AES-256-GCM) contra `tenant_keys`/`oauth_tokens`/
    `connector_accounts` reales — cierra el hueco de que `test_devices_push.py` (offline)
    solo prueba la orquestación con un `FakeVault`/`FakeSession` en memoria, nunca el cifrado
    envolvente real ni el esquema real de esas 3 tablas."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from edecan_db.session import get_session
    from edecan_db.vault import LocalKeyProvider, TokenVault

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    master_key = Fernet.generate_key().decode("ascii")

    clave_ec = ec.generate_private_key(ec.SECP256R1())
    p8_pem = clave_ec.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("utf-8")

    try:
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            vault = TokenVault(session, LocalKeyProvider(master_key))
            await devices.put_push_credentials(
                devices.PushCredentialsIn(
                    apns=devices.ApnsCredentialsIn(
                        team_id="TEAMID1234",
                        key_id="KEYID5678",
                        bundle_id="com.acme.app",
                        p8_key=p8_pem,
                    )
                ),
                current_user,
                repo,
                vault,
            )

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            vault = TokenVault(session, LocalKeyProvider(master_key))
            estado = await devices.get_push_status(current_user, repo, vault, session)
            assert estado.apns is True
            assert estado.fcm is False
            assert estado.devices_con_token == 0

        # Descifrar con la master key CORRECTA debe devolver la p8 tal cual se guardó.
        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            vault = TokenVault(session, LocalKeyProvider(master_key))
            config = await devices._cargar_config_push_existente(repo, vault, tenant_id)
            assert config["apns"]["p8_key"] == p8_pem.strip()

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            await devices.delete_push_credentials(current_user, repo)

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            vault = TokenVault(session, LocalKeyProvider(master_key))
            estado = await devices.get_push_status(current_user, repo, vault, session)
            assert estado.apns is False
            assert estado.fcm is False
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# Ads (`ads.py` + `ad_drafts` de `0006_v4_expansion`)
# ---------------------------------------------------------------------------


class _VaultQueNuncaDebeLlamarse:
    """Doble de `TokenVault` que revienta si `.get()` llega a invocarse — usado para
    demostrar (no solo asumir) que `get_tenant_ads_provider` resuelve a `StubAdsProvider`
    ANTES de tocar el vault cuando el tenant no conectó ninguna cuenta de Meta (el `SELECT`
    contra `connector_accounts` no encuentra fila y corta ahí, ver
    `edecan_ads.providers.get_tenant_ads_provider`)."""

    async def get(self, *args: Any, **kwargs: Any) -> None:
        raise AssertionError(
            "get_tenant_ads_provider no debería llamar a vault.get() sin connector_account."
        )


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_ads_borradores_lifecycle_contra_postgres_real(db) -> None:
    """`ad_drafts` (`0006_v4_expansion`) ejercitada con el código REAL del router
    (`list_borradores`/`confirmar_borrador`/`cancelar_borrador`) contra Postgres real. El
    `INSERT` inicial calca exactamente `edecan_ads.tools._crear_ad_draft` (duplicado a
    propósito — paquete hermano, `ARCHITECTURE.md` §10.1) para probar el mismo esquema que
    usa `ads_preparar_campana`. Sin credenciales de Meta conectadas, `confirmar_borrador`
    resuelve a `StubAdsProvider` (cero red real) y aun así deja el borrador `pushed` y
    SIEMPRE en pausa — el guardrail de dinero (`ARCHITECTURE.md` §13.b/§13.e) no depende de
    que haya una cuenta de Meta real conectada."""
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    settings = Settings(LOCAL_MASTER_KEY=Fernet.generate_key().decode("ascii"))

    try:
        async with get_session(tenant_id) as session:
            row = (
                (
                    await session.execute(
                        text(
                            "INSERT INTO ad_drafts "
                            "(tenant_id, user_id, provider, nombre, objetivo, presupuesto_diario, "
                            "moneda, payload, status) "
                            "VALUES (:tenant_id ::uuid, :user_id ::uuid, 'meta', :nombre, "
                            ":objetivo, :presupuesto_diario, :moneda, CAST(:payload AS jsonb), "
                            "'draft') RETURNING id"
                        ),
                        {
                            "tenant_id": str(tenant_id),
                            "user_id": str(user_id),
                            "nombre": "Campaña de prueba",
                            "objetivo": "OUTCOME_TRAFFIC",
                            "presupuesto_diario": Decimal("25.00"),
                            "moneda": "USD",
                            "payload": json.dumps({}),
                        },
                    )
                )
                .mappings()
                .first()
            )
            assert row is not None
            draft_id = row["id"]

        async with get_session(tenant_id) as session:
            listado = await ads.list_borradores(current_user, session)
            assert len(listado) == 1
            assert listado[0]["id"] == draft_id
            assert listado[0]["status"] == "draft"
            assert listado[0]["payload"] == {}  # JSONB decodificado, no un string crudo

        async with get_session(tenant_id) as session:
            resultado = await ads.confirmar_borrador(
                draft_id, current_user, session, _VaultQueNuncaDebeLlamarse(), settings
            )
            assert resultado["borrador"]["status"] == "pushed"
            assert resultado["borrador"]["external_id"].startswith("stub-campaign-")
            assert resultado["borrador"]["pushed_at"] is not None
            assert "SIEMPRE en pausa" in resultado["mensaje"] or "pausa" in resultado["mensaje"]

        async with get_session(tenant_id) as session:
            # Ya 'pushed' -> no cancelable (`_ESTADOS_CANCELABLES` no lo incluye).
            with pytest.raises(HTTPException) as exc_info:
                await ads.cancelar_borrador(draft_id, current_user, session)
            assert exc_info.value.status_code == 409
    finally:
        await _cleanup(tenant_id, user_id)


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_ad_drafts_check_constraint_status_contra_postgres_real(db) -> None:
    """Defensa en profundidad del guardrail de dinero: el `CHECK` de `ad_drafts.status`
    (`0006_v4_expansion`) debe rechazar, a nivel de BASE DE DATOS, cualquier valor fuera del
    vocabulario que asume el código Python (`draft|confirmed|pushed|error|cancelled`) — para
    que un futuro bug de aplicación que intente colar un status arbitrario (p. ej. algo que
    implique gasto activo sin pasar por el flujo real) no pueda hacerlo ni siquiera con SQL
    directo."""
    from edecan_db.session import get_session
    from sqlalchemy.exc import DBAPIError

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        with pytest.raises(DBAPIError, match="ck_ad_drafts_status|check constraint"):
            async with get_session(tenant_id) as session:
                await session.execute(
                    text(
                        "INSERT INTO ad_drafts (tenant_id, user_id, nombre, objetivo, status) "
                        "VALUES (:tenant_id ::uuid, :user_id ::uuid, 'x', 'y', 'activa_de_verdad')"
                    ),
                    {"tenant_id": str(tenant_id), "user_id": str(user_id)},
                )
    finally:
        await _cleanup(tenant_id, user_id)


# ===========================================================================
# GRUPO 2 — Estructurales (Barrido A: BYO push; Barrido B: paridad de flags).
# Corren siempre, no necesitan Postgres.
# ===========================================================================


def test_worker_settings_no_declara_ningun_campo_de_credencial_push() -> None:
    """Barrido A (BYO): `edecan_worker.push` debe ser ESTRUCTURALMENTE incapaz de leer una
    credencial de push de PLATAFORMA (ver el docstring del propio módulo). Si algún día
    alguien agrega un campo tipo `APNS_*`/`FCM_*`/`PUSH_*` a `Settings` "para simplificar",
    este centinela lo marca de inmediato — hoy no existe ninguno, a propósito."""
    from edecan_worker.config import Settings as WorkerSettings

    campos = set(WorkerSettings.model_fields)
    sospechosos = {c for c in campos if c.upper().startswith(("APNS_", "FCM_", "PUSH_"))}
    assert sospechosos == set()


def test_cargar_credenciales_push_no_acepta_ni_puede_leer_settings() -> None:
    """Barrido A (BYO): prueba estructural (firma de la función, no comportamiento) de que
    `cargar_credenciales_push` NI SIQUIERA PUEDE recibir un `Settings` de plataforma — a
    diferencia del bug histórico más serio del repo (`packages/llm/edecan_llm/router.py::
    _build_provider_from_config` leyendo `self._settings` como fallback silencioso,
    `DIRECCION_ACTUAL.md` "v4 completado"), esta función no tiene ningún parámetro por el que
    una credencial de plataforma pudiera colarse."""
    from edecan_worker.push import cargar_credenciales_push

    parametros = list(inspect.signature(cargar_credenciales_push).parameters)
    assert parametros == ["session", "vault", "tenant_id"]


def test_enviar_push_a_usuario_nunca_lee_deps_settings_para_credenciales() -> None:
    """Complementa el centinela anterior: `enviar_push_a_usuario` SÍ recibe `deps` (que trae
    `deps.settings`, usado por otros handlers para timeouts/URLs), pero jamás debe pasarle
    `deps.settings`/`deps` completo a `cargar_credenciales_push` — solo `session`/`vault`/
    `tenant_id`. Verificado leyendo el código fuente real del módulo (no solo confiando en la
    firma ya probada arriba) para cazar un futuro `cargar_credenciales_push(session, vault,
    tenant_id, settings=deps.settings)` que técnicamente no rompería la firma actual si
    alguien le agregara un parámetro opcional."""
    import inspect as _inspect

    from edecan_worker import push as push_module

    fuente = _inspect.getsource(push_module.enviar_push_a_usuario)
    assert "deps.settings" not in fuente
    assert "self._settings" not in fuente


def test_paridad_de_flags_erp_router_vs_tools_de_agente() -> None:
    """Barrido B (plan-flag): `_require_erp_inventory` (`erp.py`) y las dos tools de
    `edecan_business.tools` deben exigir EXACTAMENTE el mismo flag fino — sin esta paridad,
    un tenant podría alcanzar por el chat una capacidad que el router HTTP le niega (o
    viceversa)."""
    from edecan_business.tools import EstadoInventarioTool, GestionarInventarioTool
    from edecan_schemas.plans import FLAG_ERP_INVENTORY

    assert GestionarInventarioTool.requires_flags == frozenset({FLAG_ERP_INVENTORY})
    assert EstadoInventarioTool.requires_flags == frozenset({FLAG_ERP_INVENTORY})


def test_paridad_de_flags_ads_router_vs_tools_de_agente() -> None:
    """Barrido B (plan-flag): `_require_tools_ads` (`ads.py`) y las dos tools de
    `edecan_ads.tools` deben exigir el mismo flag — `edecan_ads` usa el string local
    `"tools.ads"` (no importa `edecan_schemas`, ver el docstring de ese paquete), así que
    esta prueba ancla el VALOR exacto en vez de comparar símbolos importados."""
    from edecan_ads.tools import AdsPrepararCampanaTool, AdsResumenTool
    from edecan_schemas.plans import FLAG_TOOLS_ADS

    assert FLAG_TOOLS_ADS == "tools.ads"
    assert AdsResumenTool.requires_flags == frozenset({FLAG_TOOLS_ADS})
    assert AdsPrepararCampanaTool.requires_flags == frozenset({FLAG_TOOLS_ADS})
    assert AdsPrepararCampanaTool.dangerous is True  # doble gate, ver docs/ads.md
    assert AdsResumenTool.dangerous is False


def test_paridad_de_flags_mensajes_router_vs_tools_de_agente() -> None:
    """Barrido B (plan-flag): `_require_messaging` (`mensajes.py`) y las dos tools de
    `edecan_messaging.tools` deben exigir el mismo flag."""
    from edecan_messaging.tools import EnviarMensajeTool, LeerMensajesTool
    from edecan_schemas.plans import FLAG_CONNECTORS_MESSAGING

    assert EnviarMensajeTool.requires_flags == frozenset({FLAG_CONNECTORS_MESSAGING})
    assert LeerMensajesTool.requires_flags == frozenset({FLAG_CONNECTORS_MESSAGING})


def test_devices_push_endpoints_exigen_flag_notifications_push() -> None:
    """Barrido B (plan-flag): los 5 endpoints de push de `devices.py` dependen de
    `_require_notifications_push`, que compara contra `FLAG_NOTIFICATIONS_PUSH` — ancla el
    símbolo exacto (no una subcadena de prosa) para que este test falle si algún día alguien
    cambia el flag sin querer. `heartbeat`/`revoke`/`GET`/`POST ""` (device CRUD base)
    deliberadamente NO llevan este gate — `companion` (que sí exigen) ya es `True` en los 4
    planes (`ARCHITECTURE.md` §13.f)."""
    from edecan_schemas.plans import FLAG_NOTIFICATIONS_PUSH

    fuente = inspect.getsource(devices._require_notifications_push)
    assert "FLAG_NOTIFICATIONS_PUSH" in fuente
    assert FLAG_NOTIFICATIONS_PUSH == "notifications.push"
