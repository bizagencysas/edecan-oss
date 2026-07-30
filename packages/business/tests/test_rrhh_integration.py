"""Test de integración de `edecan_business.rrhh` contra Postgres real (WP-V7-01, BARRIDO D).

`docs/rrhh.md` (WP-V5-08) documentaba explícitamente: "No hubo una corrida contra Postgres
real disponible para este work package [...] el SQL parametrizado sigue el esquema pinned al
pie de la letra (columnas verificadas contra la migración real, no de memoria)" — es decir, la
alineación de columnas SIEMPRE fue correcta por lectura cuidadosa, pero nunca se había
verificado empíricamente contra un Postgres real migrado de verdad. Este archivo cierra ese
hueco: mismo patrón EXACTO que `packages/db/tests/test_rls.py`/
`apps/api/tests/test_repo_sql_integration.py` (marcado `@pytest.mark.integration`, se salta
solo si `DATABASE_URL` no está configurada o Postgres no es alcanzable ahí — nunca falla por
ausencia de infraestructura, solo se salta).

Para correrlo: Postgres desechable (`docker run -d --name edecan-v7-rrhh-pg -e
POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan -e POSTGRES_DB=edecan -p 55471:5432
pgvector/pgvector:pg16`), migrar (`DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:
55471/edecan uv run --all-packages alembic -c packages/db/alembic.ini upgrade head`), y correr
con esa misma `DATABASE_URL` exportada. Sin ella, todo este módulo se salta (nunca falla).

**Qué prueba que `FakeSession` (el resto de `test_rrhh.py`) NO puede probar:**

1. Que cada columna que `edecan_business.rrhh` lee/escribe existe de verdad, con el tipo/
   nullability/CHECK exactos de la migración `0007_v5_expansion` — un typo de columna
   (`edecan_api/routers/reuniones.py` en v6, `HOTFIXES_PENDIENTES.md`) revienta acá con
   `UndefinedColumnError`, invisible a cualquier `FakeSession` que mockee filas con el mismo
   esquema equivocado que el código.
2. Row-Level Security REAL: que un tenant nunca ve filas de otro en `employees`/`time_off`/
   `payroll_runs`/`payroll_items` (política `tenant_isolation` de la migración).
3. Las DOS carreras `SELECT ... FOR UPDATE` de este módulo (`_lock_time_off`/
   `_lock_payroll_run`) sí serializan de verdad bajo concurrencia real de Postgres — no algo
   que una `FakeSession` de una sola cola de respuestas programadas pueda ejercitar (una
   `FakeSession` no tiene noción de "dos transacciones concurrentes").
4. Que los tipos `Decimal`/`date`/`jsonb` viajan intactos ida y vuelta por asyncpg (una
   `FakeSession` nunca serializa/deserializa nada de verdad).

**Hallazgo real encontrado y corregido durante esta verificación** (WP-V7-01, fuera de los 4
barridos con nombre del encargo pero dentro del archivo que sí es su alcance,
`packages/business/edecan_business/rrhh.py`): `resolver_ausencia` no tomaba ningún lock de
fila antes de leer `status` (a diferencia de `aprobar_nomina`/`cancelar_nomina`, que sí usan
`_lock_payroll_run`) — dos llamadas concurrentes sobre LA MISMA ausencia (una `aprobar`, otra
`rechazar`) podían AMBAS "tener éxito" sin que ninguna lanzara `EstadoAusenciaError`,
violando el invariante "solo se puede resolver una vez pending". Reproducido 3/3 corridas
contra Postgres real con la función SIN modificar (script standalone, no forma parte del
repo), corregido con `_lock_time_off` (mismo patrón `FOR UPDATE` que `_lock_payroll_run`/
`inventory.registrar_movimiento`), y re-verificado 3/3 corridas limpias tras el fix. Ver
`docs/cumplimiento/barrido-v7-rrhh.md` para el detalle completo. La prueba de concurrencia
correspondiente (`test_resolver_ausencia_concurrente_se_serializa_con_for_update`, más abajo)
deja esa verificación pinneada como test permanente en vez de un script desechable.
"""

from __future__ import annotations

import asyncio
import os
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest

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
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas) — incluye
    `0007_v5_expansion` (`employees`/`time_off`/`payroll_runs`/`payroll_items`)."""
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
    migraciones, y limpia sus cachés `lru_cache` al terminar (mismo patrón EXACTO que
    `packages/db/tests/test_rls.py`/`apps/api/tests/test_repo_sql_integration.py`)."""
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


# ---------------------------------------------------------------------------
# Helpers de setup/cleanup — cada test crea su propio tenant+usuario (sufijo aleatorio) y los
# borra al terminar. `tenants` se borra con `ON DELETE CASCADE` (migración `0007_v5_expansion`,
# `_tenant_id_column()`) y arrastra `employees`/`payroll_runs` (FK directa a `tenant_id`) y, en
# cascada desde ahí, `time_off`/`payroll_items` (FK a `employees`/`payroll_runs`, también
# `ON DELETE CASCADE`); `users` es global y se borra aparte.
# ---------------------------------------------------------------------------


async def _seed_tenant_y_usuario(sufijo: str) -> tuple[UUID, UUID]:
    from edecan_db.models import Tenant, User
    from edecan_db.session import get_session

    async with get_session(None) as session:
        tenant = Tenant(
            name=f"RRHH test {sufijo}", slug=f"rrhh-test-{sufijo}", plan_key="hosted_pro"
        )
        session.add(tenant)
        await session.flush()
        user = User(email=f"rrhh-test-{sufijo}@example.com", password_hash="x" * 20)
        session.add(user)
        await session.flush()
        return tenant.id, user.id


async def _cleanup(tenant_id: UUID, user_id: UUID) -> None:
    from edecan_db.models import Tenant, User
    from edecan_db.session import get_session
    from sqlalchemy import delete

    async with get_session(None) as session:
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
        await session.execute(delete(User).where(User.id == user_id))


# ---------------------------------------------------------------------------
# Ciclo de vida completo: empleado -> ausencia -> nómina -> aprobación.
# ---------------------------------------------------------------------------


async def test_ciclo_completo_empleado_ausencia_nomina_contra_postgres_real(db) -> None:
    """Ejercita, en orden, TODAS las funciones públicas de `edecan_business.rrhh` contra
    Postgres real migrado con `0007_v5_expansion` — si alguna columna del SQL parametrizado no
    coincidiera con la migración real (nombre, tipo, o un `CHECK` más estricto de lo que el
    código asume), esta prueba revienta con `UndefinedColumnError`/`CheckViolationError` de
    Postgres real, no con un mock que calca el mismo esquema equivocado."""
    from edecan_business.rrhh import (
        aprobar_nomina,
        calcular_nomina,
        cancelar_nomina,
        crear_empleado,
        editar_empleado,
        listar_ausencias,
        listar_empleados,
        listar_nominas,
        obtener_empleado,
        obtener_empleado_por_nombre,
        obtener_nomina,
        registrar_ausencia,
        resolver_ausencia,
    )
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        # 1) crear_empleado — INSERT con TODAS las columnas del contrato pinned.
        async with get_session(tenant_id) as session:
            empleado = await crear_empleado(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                nombre="Ana Pérez",
                email="ana@empresa.com",
                puesto="Gerente de Ventas",
                salario_mensual=Decimal("2500.00"),
                moneda="usd",
                fecha_ingreso="2026-01-15",
            )
        assert empleado["nombre"] == "Ana Pérez"
        assert empleado["salario_mensual"] == Decimal("2500.00")
        assert empleado["moneda"] == "USD"
        assert empleado["fecha_ingreso"] == date(2026, 1, 15)
        assert empleado["status"] == "active"
        assert empleado["meta"] == {}  # jsonb default '{}' — confirma el tipo real
        employee_id = empleado["id"]

        # 2) editar_empleado — UPDATE parcial dinámico.
        async with get_session(tenant_id) as session:
            actualizado = await editar_empleado(
                session, tenant_id=tenant_id, employee_id=employee_id, puesto="Directora Comercial"
            )
        assert actualizado["puesto"] == "Directora Comercial"
        assert actualizado["nombre"] == "Ana Pérez"  # no tocado

        # 3) listar_empleados / obtener_empleado / obtener_empleado_por_nombre.
        async with get_session(tenant_id) as session:
            listado = await listar_empleados(session, tenant_id=tenant_id)
        assert [e["id"] for e in listado] == [employee_id]

        async with get_session(tenant_id) as session:
            leido = await obtener_empleado(session, tenant_id=tenant_id, employee_id=employee_id)
        assert leido["id"] == employee_id

        async with get_session(tenant_id) as session:
            por_nombre = await obtener_empleado_por_nombre(
                session, tenant_id=tenant_id, nombre="ana pérez"
            )
        assert por_nombre["id"] == employee_id

        # 4) registrar_ausencia — `time_off` SIN user_id/approved_at (contrato pinned).
        async with get_session(tenant_id) as session:
            ausencia = await registrar_ausencia(
                session,
                tenant_id=tenant_id,
                employee_id=employee_id,
                kind="vacaciones",
                desde="2026-08-01",
                hasta="2026-08-05",
                notas="primera semana de agosto",
            )
        assert ausencia["status"] == "pending"
        assert "user_id" not in ausencia
        assert "approved_at" not in ausencia
        time_off_id = ausencia["id"]

        # 5) listar_ausencias.
        async with get_session(tenant_id) as session:
            ausencias = await listar_ausencias(session, tenant_id=tenant_id)
        assert [a["id"] for a in ausencias] == [time_off_id]

        # 6) resolver_ausencia (aprobar) — ahora con el lock `FOR UPDATE` (WP-V7-01).
        async with get_session(tenant_id) as session:
            resuelta = await resolver_ausencia(
                session, tenant_id=tenant_id, time_off_id=time_off_id, aprobar=True
            )
        assert resuelta["status"] == "approved"

        # 7) calcular_nomina — `payroll_runs.total` es el NETO; sin columna `deducciones_pct`
        #    propia (contrato pinned) — `total_bruto`/`total_deducciones` son CALCULADOS.
        async with get_session(tenant_id) as session:
            nomina = await calcular_nomina(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                periodo="2026-07",
                deducciones_pct=Decimal("10"),
                notas="primera corrida",
            )
        assert nomina["status"] == "draft"
        assert len(nomina["items"]) == 1
        assert nomina["items"][0]["empleado_nombre"] == "Ana Pérez"
        assert nomina["items"][0]["bruto"] == Decimal("2500.00")
        assert nomina["items"][0]["deducciones"] == Decimal("250.00")
        assert nomina["items"][0]["neto"] == Decimal("2250.00")
        assert nomina["total"] == Decimal("2250.00")  # única columna persistida: el NETO
        assert nomina["total_bruto"] == Decimal("2500.00")  # calculado, no persistido
        assert nomina["total_deducciones"] == Decimal("250.00")  # calculado, no persistido
        assert "deducciones_pct" not in nomina  # esa columna no existe en payroll_runs
        payroll_run_id = nomina["id"]

        # 8) listar_nominas / obtener_nomina.
        async with get_session(tenant_id) as session:
            nominas = await listar_nominas(session, tenant_id=tenant_id)
        assert [n["id"] for n in nominas] == [payroll_run_id]

        async with get_session(tenant_id) as session:
            detalle = await obtener_nomina(
                session, tenant_id=tenant_id, payroll_run_id=payroll_run_id
            )
        assert detalle["items"][0]["empleado_nombre"] == "Ana Pérez"

        # 9) aprobar_nomina — draft -> approved, NUNCA mueve dinero (solo status/approved_at).
        async with get_session(tenant_id) as session:
            aprobada = await aprobar_nomina(
                session, tenant_id=tenant_id, payroll_run_id=payroll_run_id
            )
        assert aprobada["status"] == "approved"
        assert aprobada["approved_at"] is not None

        # 10) cancelar_nomina sobre una YA aprobada -> EstadoNominaError (409 en el router).
        from edecan_business.rrhh import EstadoNominaError

        async with get_session(tenant_id) as session:
            with pytest.raises(EstadoNominaError):
                await cancelar_nomina(
                    session, tenant_id=tenant_id, payroll_run_id=payroll_run_id
                )
    finally:
        await _cleanup(tenant_id, user_id)


async def test_registrar_ausencia_solapada_con_aprobada_rechaza_contra_postgres_real(db) -> None:
    from edecan_business.rrhh import (
        AusenciaSolapadaError,
        crear_empleado,
        registrar_ausencia,
        resolver_ausencia,
    )
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            empleado = await crear_empleado(
                session, tenant_id=tenant_id, user_id=user_id, nombre="Beto"
            )
        async with get_session(tenant_id) as session:
            primera = await registrar_ausencia(
                session,
                tenant_id=tenant_id,
                employee_id=empleado["id"],
                kind="vacaciones",
                desde="2026-09-01",
                hasta="2026-09-10",
            )
        async with get_session(tenant_id) as session:
            await resolver_ausencia(
                session, tenant_id=tenant_id, time_off_id=primera["id"], aprobar=True
            )

        async with get_session(tenant_id) as session:
            with pytest.raises(AusenciaSolapadaError):
                await registrar_ausencia(
                    session,
                    tenant_id=tenant_id,
                    employee_id=empleado["id"],
                    kind="permiso",
                    desde="2026-09-05",
                    hasta="2026-09-06",
                )
    finally:
        await _cleanup(tenant_id, user_id)


async def test_calcular_nomina_excluye_inactivos_y_sin_salario_contra_postgres_real(db) -> None:
    """`calcular_nomina` solo toma empleados `active` CON `salario_mensual` asignado (ver el
    docstring del módulo) — verificado contra el filtro SQL real (`status = 'active' AND
    salario_mensual IS NOT NULL`), no contra una `FakeSession` que podría devolver cualquier
    fila programada sin que el filtro real la hubiera excluido de verdad."""
    from edecan_business.rrhh import calcular_nomina, crear_empleado
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            await crear_empleado(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                nombre="Con salario activo",
                salario_mensual=Decimal("1000.00"),
            )
        async with get_session(tenant_id) as session:
            await crear_empleado(
                session, tenant_id=tenant_id, user_id=user_id, nombre="Sin salario todavía"
            )
        async with get_session(tenant_id) as session:
            inactivo = await crear_empleado(
                session,
                tenant_id=tenant_id,
                user_id=user_id,
                nombre="Inactivo con salario",
                salario_mensual=Decimal("5000.00"),
            )
        async with get_session(tenant_id) as session:
            from edecan_business.rrhh import editar_empleado

            await editar_empleado(
                session, tenant_id=tenant_id, employee_id=inactivo["id"], status="inactive"
            )

        async with get_session(tenant_id) as session:
            nomina = await calcular_nomina(
                session, tenant_id=tenant_id, user_id=user_id, periodo="2026-10"
            )
        assert len(nomina["items"]) == 1
        assert nomina["items"][0]["empleado_nombre"] == "Con salario activo"
        assert nomina["total"] == Decimal("1000.00")
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# Concurrencia real: los DOS locks `FOR UPDATE` de este módulo.
# ---------------------------------------------------------------------------


async def test_resolver_ausencia_concurrente_se_serializa_con_for_update(db) -> None:
    """Regresión permanente del hallazgo de WP-V7-01 (ver el docstring del módulo): dos
    `resolver_ausencia` concurrentes sobre LA MISMA ausencia `pending` deben serializarse
    (`_lock_time_off`, `FOR UPDATE`) — la segunda SIEMPRE debe encontrar la ausencia ya
    resuelta y lanzar `EstadoAusenciaError`, nunca "tener éxito" también. Antes del fix de
    WP-V7-01 esta prueba fallaba (las dos "tenían éxito") en 3/3 corridas reproducidas."""
    from edecan_business.rrhh import (
        EstadoAusenciaError,
        crear_empleado,
        registrar_ausencia,
        resolver_ausencia,
    )
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            empleado = await crear_empleado(
                session, tenant_id=tenant_id, user_id=user_id, nombre="Carla"
            )
        async with get_session(tenant_id) as session:
            ausencia = await registrar_ausencia(
                session,
                tenant_id=tenant_id,
                employee_id=empleado["id"],
                kind="vacaciones",
                desde="2026-11-01",
                hasta="2026-11-05",
            )
        time_off_id = ausencia["id"]

        barrera = asyncio.Event()
        resultados: dict[str, tuple[str, str]] = {}

        async def _resolver(nombre: str, aprobar: bool) -> None:
            async with get_session(tenant_id) as session:
                await barrera.wait()
                try:
                    r = await resolver_ausencia(
                        session, tenant_id=tenant_id, time_off_id=time_off_id, aprobar=aprobar
                    )
                    resultados[nombre] = ("ok", r["status"])
                except EstadoAusenciaError:
                    resultados[nombre] = ("error", "EstadoAusenciaError")

        t1 = asyncio.create_task(_resolver("aprobar", True))
        t2 = asyncio.create_task(_resolver("rechazar", False))
        await asyncio.sleep(0.05)  # deja que ambas tareas lleguen a `barrera.wait()`
        barrera.set()
        await asyncio.gather(t1, t2)

        exitos = [v for v in resultados.values() if v[0] == "ok"]
        errores = [v for v in resultados.values() if v[0] == "error"]
        assert len(exitos) == 1, f"debía haber EXACTAMENTE un éxito, hubo {resultados}"
        assert len(errores) == 1, (
            f"debía haber EXACTAMENTE un EstadoAusenciaError, hubo {resultados}"
        )

        async with get_session(tenant_id) as session:
            from sqlalchemy import text

            fila_final = (
                await session.execute(
                    text("SELECT status FROM time_off WHERE id = :id ::uuid"),
                    {"id": str(time_off_id)},
                )
            ).mappings().first()
        # El status final coincide con el ÚNICO resultado exitoso -- nunca quedó pisado por
        # el segundo intento (que ahora falla ANTES de llegar al UPDATE).
        assert fila_final["status"] == exitos[0][1]
    finally:
        await _cleanup(tenant_id, user_id)


async def test_aprobar_nomina_concurrente_se_serializa_con_for_update(db) -> None:
    """Mismo tipo de prueba que la anterior, pero para `_lock_payroll_run` (usado por
    `aprobar_nomina`/`cancelar_nomina`, ya existía antes de WP-V7-01 pero NUNCA se había
    verificado contra concurrencia real de Postgres — `docs/rrhh.md` documentaba
    explícitamente que no hubo corrida contra Postgres real disponible). Dos llamadas
    concurrentes (una `aprobar_nomina`, otra `cancelar_nomina`) sobre la MISMA corrida
    `draft` deben serializarse: exactamente una tiene éxito, la otra encuentra la corrida ya
    resuelta y lanza `EstadoNominaError`."""
    from edecan_business.rrhh import (
        EstadoNominaError,
        aprobar_nomina,
        calcular_nomina,
        cancelar_nomina,
    )
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    try:
        async with get_session(tenant_id) as session:
            nomina = await calcular_nomina(
                session, tenant_id=tenant_id, user_id=user_id, periodo="2026-12"
            )
        payroll_run_id = nomina["id"]

        barrera = asyncio.Event()
        resultados: dict[str, tuple[str, str]] = {}

        async def _aprobar() -> None:
            async with get_session(tenant_id) as session:
                await barrera.wait()
                try:
                    r = await aprobar_nomina(
                        session, tenant_id=tenant_id, payroll_run_id=payroll_run_id
                    )
                    resultados["aprobar"] = ("ok", r["status"])
                except EstadoNominaError:
                    resultados["aprobar"] = ("error", "EstadoNominaError")

        async def _cancelar() -> None:
            async with get_session(tenant_id) as session:
                await barrera.wait()
                try:
                    r = await cancelar_nomina(
                        session, tenant_id=tenant_id, payroll_run_id=payroll_run_id
                    )
                    resultados["cancelar"] = ("ok", r["status"])
                except EstadoNominaError:
                    resultados["cancelar"] = ("error", "EstadoNominaError")

        t1 = asyncio.create_task(_aprobar())
        t2 = asyncio.create_task(_cancelar())
        await asyncio.sleep(0.05)
        barrera.set()
        await asyncio.gather(t1, t2)

        exitos = [v for v in resultados.values() if v[0] == "ok"]
        errores = [v for v in resultados.values() if v[0] == "error"]
        assert len(exitos) == 1, f"debía haber EXACTAMENTE un éxito, hubo {resultados}"
        assert len(errores) == 1, f"debía haber EXACTAMENTE un EstadoNominaError, hubo {resultados}"
    finally:
        await _cleanup(tenant_id, user_id)


# ---------------------------------------------------------------------------
# Row-Level Security real sobre las 4 tablas nuevas de `0007_v5_expansion`.
# ---------------------------------------------------------------------------


async def test_rls_aisla_tablas_rrhh_entre_tenants(db) -> None:
    """`packages/db/tests/test_rls.py` ya verifica RLS en general (`usage_events`, tabla de
    v1) — este test extiende esa misma garantía a las 4 tablas NUEVAS de `0007_v5_expansion`
    (`employees`/`time_off`/`payroll_runs`/`payroll_items`), que ese archivo no cubre."""
    from edecan_business.rrhh import calcular_nomina, crear_empleado, registrar_ausencia
    from edecan_db.session import get_session

    sufijo = uuid4().hex[:8]
    tenant_a_id, user_a_id = await _seed_tenant_y_usuario(f"a-{sufijo}")
    tenant_b_id, user_b_id = await _seed_tenant_y_usuario(f"b-{sufijo}")
    try:
        async with get_session(tenant_a_id) as session:
            empleado_a = await crear_empleado(
                session,
                tenant_id=tenant_a_id,
                user_id=user_a_id,
                nombre="Solo de A",
                salario_mensual=Decimal("1200.00"),
            )
        async with get_session(tenant_a_id) as session:
            await registrar_ausencia(
                session,
                tenant_id=tenant_a_id,
                employee_id=empleado_a["id"],
                kind="vacaciones",
                desde="2027-01-01",
                hasta="2027-01-02",
            )
        async with get_session(tenant_a_id) as session:
            await calcular_nomina(
                session, tenant_id=tenant_a_id, user_id=user_a_id, periodo="2027-01"
            )

        # Una sesión SCOPEADA al tenant B (RLS activo, `SET LOCAL ROLE app_user`) no debe ver
        # NADA de lo que insertó el tenant A, aunque las funciones no reciban ningún filtro
        # extra más allá de `tenant_id` en el SQL -- la política de la base de datos es la
        # última línea de defensa.
        from edecan_business.rrhh import listar_ausencias, listar_empleados, listar_nominas

        async with get_session(tenant_b_id) as session:
            assert await listar_empleados(session, tenant_id=tenant_b_id) == []
            assert await listar_ausencias(session, tenant_id=tenant_b_id) == []
            assert await listar_nominas(session, tenant_id=tenant_b_id) == []

        # Incluso pasando el `tenant_id` de A a mano (bug hipotético del código llamador): la
        # sesión sigue scopeada a B por `app.tenant_id`, RLS gana.
        async with get_session(tenant_b_id) as session:
            assert await listar_empleados(session, tenant_id=tenant_a_id) == []

        # La sesión de A sigue viendo lo suyo con normalidad.
        async with get_session(tenant_a_id) as session:
            propios = await listar_empleados(session, tenant_id=tenant_a_id)
        assert [e["id"] for e in propios] == [empleado_a["id"]]
    finally:
        await _cleanup(tenant_a_id, user_a_id)
        await _cleanup(tenant_b_id, user_b_id)
