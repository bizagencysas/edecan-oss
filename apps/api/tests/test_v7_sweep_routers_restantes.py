"""Barrido v7 — routers restantes (WP-V7-08): `missions`, `automations`, `ide`,
`smarthome`, `skills`, `setup`, `voice`, `companion`, `memory`, `files`, `contacts`,
`finance`, `reminders`, `me`, `persona`, `admin`, `usage` (los 17 routers que ningún
barrido de evidencia anterior recorrió — `docs/cumplimiento/barrido-evidencia-v6.md`
cubrió otro conjunto), más `packages/automations`. Ver
`docs/cumplimiento/barrido-v7-routers-restantes.md` para el informe completo (tabla de
los 17, qué se encontró, qué se corrigió).

Este archivo complementa — NO repite — la cobertura ya exhaustiva con
`FakeSession`/`FakeRepo`/`FakeVault` de `test_missions_router.py`/
`test_automations_router.py`/`test_ide_router.py`/`test_smarthome_router.py`/
`test_skills_router.py`/`test_setup_router.py`/`test_voice.py`/`test_voice_byo.py`/
`test_companion.py`/`test_memory.py`/`test_files.py`/`test_contacts.py`/
`test_finance.py`/`test_reminders.py`/`test_me.py`/`test_persona.py`/`test_admin.py`/
`test_usage.py`. Mismo criterio que su hermano de esta misma ola,
`test_v7_sweep_v4residual.py` (WP-V7-07): dos grupos.

1. **Empíricos, marcados `@pytest.mark.integration`** (se saltan solos si
   `DATABASE_URL` no apunta a un Postgres real alcanzable — duplicado a propósito de
   `test_repo_sql_integration.py`/`test_v7_sweep_v4residual.py`, `ARCHITECTURE.md`
   §10.1: "cada módulo de test de integración es autocontenido"): ejercitan
   DIRECTAMENTE las funciones de `missions.py`/`automations.py` (los únicos dos de
   los 17 routers de este WP que hablan SQL parametrizado directo contra tablas
   propias — el resto usa `Repo`/`edecan_skills.store`/el companion, ver el informe)
   contra una base migrada a `head` (`0003_v2_expansion`). Objetivo explícito: que un
   desajuste entre el SQL crudo de estos routers y la migración real sea IMPOSIBLE de
   pasar por alto — la misma clase de bug crítico que v6 encontró en
   `apps/api/edecan_api/routers/reuniones.py` (`HOTFIXES_PENDIENTES.md`).

2. **Estructurales, sin marcar** (corren siempre, no necesitan Postgres):
   a. **Regresión de los dos fail-open corregidos por este WP**:
      `files.py::_check_storage_quota` y `voice.py::_check_voice_quota` defaulteaban
      a `UNLIMITED` (`tenant.flags.get(LIMIT_..., UNLIMITED)`) en vez de `0` cuando
      `tenant.flags` no trae la clave — exactamente lo que pasa con un `plan_key`
      huérfano (`edecan_api.deps.flags_for_plan` devuelve `{}`, ver su docstring:
      "p. ej. quedó huérfano tras un cambio de catálogo de planes"). A diferencia de
      `voice.py` (que sí tiene un flag booleano `voice.web` fail-closed ANTES, ver
      `test_voice.py::test_transcribe_without_voice_web_flag_returns_403`, que ya
      prueba `plan_key="plan_no_existe"` y confirma 403 por ESE gate), `files.py` no
      tiene ningún flag booleano previo — así que un tenant con `plan_key` huérfano
      podía subir archivos con almacenamiento SIN NINGÚN límite. Alineado con el
      criterio fail-closed que ya usa `missions.py::_check_missions_quota`
      (`tenant.flags.get(LIMIT_MISSIONS_PER_DAY, 0)`) y que documenta explícitamente
      `ide.py::_require_companion_ide` ("fail-closed, no fail-open").
   b. **Paridad "sin flag de plan por diseño"** para `smarthome.py`/`skills.py` (así
      lo documentan, con instrucción explícita del work package dueño original, sus
      propios paquetes hermanos `edecan_smarthome`/`edecan_skills`).
   c. **Barrido D (esquema) sin Postgres**: compara las columnas literales que usa el
      SQL crudo de `missions.py`/`automations.py` contra `edecan_db.models` por
      introspección de `Table.columns` — corre siempre (a diferencia del Grupo 1), así
      que detecta un desajuste incluso en una corrida sin `DATABASE_URL`.

Para correr los tests empíricos: un Postgres desechable (p. ej. `docker run -d --name
edecan-v7-routers-pg -e POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan -e
POSTGRES_DB=edecan -p 55480:5432 pgvector/pgvector:pg16`) + `DATABASE_URL=postgresql+
asyncpg://edecan:edecan@localhost:55480/edecan` exportada, luego `uv run --all-packages
pytest apps/api/tests/test_v7_sweep_routers_restantes.py`. Sin `DATABASE_URL`, solo
corren los estructurales (los empíricos se saltan con un `skip` explícito, nunca
fallan).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from conftest import auth_headers

from edecan_api.deps import CurrentUser, TenantCtx, flags_for_plan

# `hosted_pro` trae `agents.missions`/`automations.rules`/`voice.web` en `True` a la
# vez (`edecan_schemas.plans.PLANES`) — un solo plan_key alcanza para el Grupo 1.
_PLAN = "hosted_pro"


def _current_user(
    tenant_id: uuid.UUID, user_id: uuid.UUID, *, plan_key: str = _PLAN
) -> CurrentUser:
    return CurrentUser(
        user_id=user_id,
        tenant=TenantCtx(tenant_id=tenant_id, plan_key=plan_key, flags=flags_for_plan(plan_key)),
    )


# ===========================================================================
# GRUPO 1 — Empíricos contra Postgres real (Barrido D: esquema).
# Boilerplate duplicado a propósito de `test_v7_sweep_v4residual.py`/
# `test_repo_sql_integration.py` (ARCHITECTURE.md §10.1).
# ===========================================================================


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


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    from alembic.command import upgrade
    from alembic.config import Config

    db_dir = Path(__file__).resolve().parents[3] / "packages" / "db"
    cfg = Config(str(db_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
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

    from edecan_api.repo import SqlRepo

    async with get_session(None) as session:
        repo = SqlRepo(session)
        tenant = await repo.create_tenant(
            name=f"v7 sweep routers {sufijo}", slug=f"v7-sweep-routers-{sufijo}", plan_key=plan_key
        )
        user = await repo.create_user(
            email=f"v7-sweep-routers-{sufijo}@example.com", password_hash="x" * 20
        )
        await repo.create_membership(user_id=user["id"], tenant_id=tenant["id"], role="owner")
    return tenant["id"], user["id"]


async def _cleanup(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from edecan_db.session import get_session
    from sqlalchemy import text as _text

    # El tenant primero: `ON DELETE CASCADE` (0001_initial) se lleva
    # agent_missions/agent_steps/automations/automation_runs.
    async with get_session(None) as session:
        await session.execute(_text("DELETE FROM tenants WHERE id = :id"), {"id": str(tenant_id)})
    async with get_session(None) as session:
        await session.execute(_text("DELETE FROM users WHERE id = :id"), {"id": str(user_id)})


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_missions_lifecycle_contra_postgres_real(db) -> None:
    """`agent_missions`/`agent_steps` (`0003_v2_expansion`) ejercitadas de punta a
    punta con el código REAL de `missions.py` (`create_mission`/`list_missions`/
    `get_mission`/`get_mission_detalle`/`confirm_mission`/`cancel_mission`) contra
    Postgres real — si `_MISSION_COLUMNS`/`_STEP_COLUMNS` no coincidieran con la
    migración real, esto revienta con `UndefinedColumnError` en vez de pasar en
    silencio como con un `FakeSession` (mismo objetivo que ya cubrió el hallazgo
    crítico de `reuniones.py` en v6)."""
    from edecan_db.session import get_session
    from sqlalchemy import text as _text

    from edecan_api.config import Settings
    from edecan_api.routers import missions

    settings = Settings()
    enqueued: list[dict] = []

    async def _fake_enqueue(_settings, job_type, payload, tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid4()

    import edecan_api.routers.missions as missions_module

    original_enqueue = missions_module.enqueue
    missions_module.enqueue = _fake_enqueue  # type: ignore[assignment]

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    try:
        async with get_session(tenant_id) as session:
            creada = await missions.create_mission(
                missions.MissionCreateIn(objetivo="Investigar competidores"),
                current_user,
                session,
                settings,
            )
            assert creada["status"] == "planning"
            mission_id = creada["id"]
            assert len(enqueued) == 1
            assert enqueued[0]["job_type"] == "run_mission"

        async with get_session(tenant_id) as session:
            listado = await missions.list_missions(current_user, session)
            assert any(m["id"] == mission_id for m in listado)

        async with get_session(tenant_id) as session:
            detalle = await missions.get_mission_detalle(mission_id, current_user, session)
            assert detalle["mission"]["id"] == mission_id
            assert detalle["steps"] == []
            assert detalle["agregados"]["pasos_por_status"]["pending"] == 0

        # Simula que el worker dejó la misión esperando confirmación con un paso
        # pendiente — inserta directo (mismas columnas que `run_mission.py` usaría).
        async with get_session(tenant_id) as session:
            await session.execute(
                _text("UPDATE agent_missions SET status = 'waiting_confirmation' WHERE id = :id"),
                {"id": str(mission_id)},
            )
            await session.execute(
                _text(
                    "INSERT INTO agent_steps "
                    "(id, tenant_id, mission_id, seq, agente, instruccion, status) "
                    "VALUES (gen_random_uuid(), :tenant_id, :mission_id, 1, 'investigador', "
                    "'buscar precios', 'waiting_confirmation')"
                ),
                {"tenant_id": str(tenant_id), "mission_id": str(mission_id)},
            )

        async with get_session(tenant_id) as session:
            confirmada = await missions.confirm_mission(
                mission_id,
                missions.MissionConfirmIn(approved=True),
                current_user,
                session,
                settings,
            )
            assert confirmada["status"] == "running"
            assert len(enqueued) == 2
            assert enqueued[1]["payload"]["resume"] is True

        # Segunda misión, para el camino de cancelación directa.
        async with get_session(tenant_id) as session:
            otra = await missions.create_mission(
                missions.MissionCreateIn(objetivo="Otra misión"), current_user, session, settings
            )
            otra_id = otra["id"]

        async with get_session(tenant_id) as session:
            cancelada = await missions.cancel_mission(otra_id, current_user, session)
            assert cancelada["status"] == "cancelled"
    finally:
        missions_module.enqueue = original_enqueue  # type: ignore[assignment]
        await _cleanup(tenant_id, user_id)


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON is not None, reason=_SKIP_REASON or "")
async def test_automations_lifecycle_contra_postgres_real(db) -> None:
    """`automations`/`automation_runs` (`0003_v2_expansion`) ejercitadas de punta a
    punta con el código REAL de `automations.py` (`create_automation`/
    `list_automations`/`get_automation`/`update_automation`/`probar_automation`/
    `list_automation_runs`/`delete_automation`) contra Postgres real."""
    from edecan_db.session import get_session
    from sqlalchemy import text as _text

    from edecan_api.config import Settings
    from edecan_api.routers import automations

    settings = Settings()

    import edecan_api.routers.automations as automations_module

    enqueued: list[dict] = []

    async def _fake_enqueue(_settings, job_type, payload, tenant_id):
        enqueued.append({"job_type": job_type, "payload": payload, "tenant_id": tenant_id})
        return uuid4()

    original_enqueue = automations_module.enqueue
    automations_module.enqueue = _fake_enqueue  # type: ignore[assignment]

    sufijo = uuid4().hex[:8]
    tenant_id, user_id = await _seed_tenant_y_usuario(sufijo)
    current_user = _current_user(tenant_id, user_id)
    try:
        async with get_session(tenant_id) as session:
            creada = await automations.create_automation(
                automations.AutomationIn(
                    nombre="Reporte diario",
                    trigger={"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"},
                    accion={"instruccion": "Manda el reporte de ventas."},
                ),
                current_user,
                session,
                settings,
            )
            automation_id = creada["id"]
            assert creada["trigger"]["kind"] == "schedule"
            assert creada["next_run_at"] is not None

        async with get_session(tenant_id) as session:
            listado = await automations.list_automations(current_user, session, settings)
            assert any(a["id"] == automation_id for a in listado)

        async with get_session(tenant_id) as session:
            actualizada = await automations.update_automation(
                automation_id,
                automations.AutomationPatch(descripcion="Actualizada por el test"),
                current_user,
                session,
                settings,
            )
            assert actualizada["descripcion"] == "Actualizada por el test"

        async with get_session(tenant_id) as session:
            probada = await automations.probar_automation(
                automation_id, current_user, session, settings
            )
            assert probada == {"queued": True}
            assert len(enqueued) == 1
            assert enqueued[0]["job_type"] == "run_automation"

        # `automation_runs`: el worker (fuera de este WP) sería quien inserta filas
        # reales — se inserta una directo para probar el SELECT de `list_automation_runs`
        # contra el esquema real.
        async with get_session(tenant_id) as session:
            await session.execute(
                _text(
                    "INSERT INTO automation_runs "
                    "(id, tenant_id, automation_id, status, detalle) "
                    "VALUES (gen_random_uuid(), :tenant_id, :automation_id, 'done', "
                    "CAST(:detalle AS jsonb))"
                ),
                {
                    "tenant_id": str(tenant_id),
                    "automation_id": str(automation_id),
                    "detalle": json.dumps({"resultado": "ok"}),
                },
            )

        async with get_session(tenant_id) as session:
            runs = await automations.list_automation_runs(automation_id, current_user, session)
            assert len(runs) == 1
            assert runs[0]["status"] == "done"
            assert runs[0]["detalle"] == {"resultado": "ok"}

        async with get_session(tenant_id) as session:
            await automations.delete_automation(automation_id, current_user, session)

        async with get_session(tenant_id) as session:
            with pytest.raises(Exception):  # noqa: B017 - HTTPException 404, cualquier subclase sirve
                await automations.get_automation(automation_id, current_user, session, settings)
    finally:
        automations_module.enqueue = original_enqueue  # type: ignore[assignment]
        await _cleanup(tenant_id, user_id)


# ===========================================================================
# GRUPO 2 — Estructurales (siempre corren, no necesitan Postgres).
# ===========================================================================


# ---------------------------------------------------------------------------
# Barrido D: esquema real (`edecan_db.models`) vs SQL crudo, SIN Postgres.
# ---------------------------------------------------------------------------


def test_missions_columnas_sql_coinciden_con_el_modelo_agent_missions() -> None:
    """`missions.py::_MISSION_COLUMNS` (usada en cada SELECT/INSERT...RETURNING)
    debe ser exactamente el conjunto de columnas reales de `agent_missions`
    (`0003_v2_expansion`/`edecan_db.models.AgentMission`) — mismo espíritu que
    `test_v7_sweep_v4residual.py::test_erp_inventory_lifecycle_contra_postgres_real`
    pero sin necesitar Postgres: un desajuste literal en el string de columnas se
    detecta incluso sin `DATABASE_URL`."""
    from edecan_db.models import AgentMission

    from edecan_api.routers.missions import _MISSION_COLUMNS

    columnas_router = {c.strip() for c in _MISSION_COLUMNS.split(",")}
    columnas_modelo = set(AgentMission.__table__.columns.keys())
    assert columnas_router == columnas_modelo


def test_missions_columnas_sql_coinciden_con_el_modelo_agent_steps() -> None:
    from edecan_db.models import AgentStep

    from edecan_api.routers.missions import _STEP_COLUMNS

    columnas_router = {c.strip() for c in _STEP_COLUMNS.split(",")}
    columnas_modelo = set(AgentStep.__table__.columns.keys())
    assert columnas_router == columnas_modelo


def test_automations_insert_solo_usa_columnas_reales_de_automations() -> None:
    """`automations.py::create_automation` inserta un subconjunto explícito de
    columnas (no las 12 -- `last_run_at` nace `NULL` por default) -- este test
    verifica que ESE subconjunto sea un `subset` real de `Automation.__table__`,
    para cazar un typo de columna que `FakeSession`/`_FakeResult` no puede detectar
    (aceptan cualquier nombre de columna sin validar)."""
    from edecan_db.models import Automation

    columnas_insert = {
        "id",
        "tenant_id",
        "user_id",
        "nombre",
        "descripcion",
        "trigger",
        "accion",
        "enabled",
        "next_run_at",
    }
    columnas_modelo = set(Automation.__table__.columns.keys())
    assert columnas_insert <= columnas_modelo


def test_automations_runs_select_solo_usa_columnas_reales() -> None:
    from edecan_db.models import AutomationRun

    columnas_usadas_por_list_automation_runs = {
        "id",
        "status",
        "detalle",
        "started_at",
        "finished_at",
    }
    columnas_modelo = set(AutomationRun.__table__.columns.keys())
    assert columnas_usadas_por_list_automation_runs <= columnas_modelo


# ---------------------------------------------------------------------------
# Regresión del fix: fail-closed en vez de fail-open en `files.py`/`voice.py`.
# ---------------------------------------------------------------------------


async def test_check_storage_quota_plan_huerfano_deniega_en_vez_de_ilimitado(fake_repo) -> None:
    """Regresión directa del hallazgo (WP-V7-08, barrido v7): antes de este fix,
    `_check_storage_quota` defaulteaba a `UNLIMITED` cuando `tenant.flags` no traía
    `LIMIT_STORAGE_MB` -- exactamente lo que pasa con un `plan_key` huérfano
    (`edecan_api.deps.flags_for_plan` devuelve `{}`). Con el fix, el mismo tenant
    (0 MB de límite -> fail-closed) es rechazado al primer byte."""
    from fastapi import HTTPException

    from edecan_api.deps import TenantCtx
    from edecan_api.routers.files import _check_storage_quota

    tenant = TenantCtx(tenant_id=uuid.uuid4(), plan_key="plan_no_existe", flags={})

    with pytest.raises(HTTPException) as exc_info:
        await _check_storage_quota(fake_repo, tenant, incoming_bytes=1024)
    assert exc_info.value.status_code == 429


async def test_check_storage_quota_plan_conocido_conserva_su_limite_real(fake_repo) -> None:
    """Contraparte: un plan REAL (`hosted_basic`, `LIMIT_STORAGE_MB=1024` explícito
    en `edecan_schemas.plans.PLANES`) nunca alcanza el nuevo default -- este test
    falla si el fix accidentalmente rompiera el camino feliz de un plan real."""
    from edecan_api.deps import TenantCtx, flags_for_plan
    from edecan_api.routers.files import _check_storage_quota

    tenant = TenantCtx(
        tenant_id=uuid.uuid4(), plan_key="hosted_basic", flags=flags_for_plan("hosted_basic")
    )

    # Un archivo chico, muy por debajo de los 1024 MB reales del plan: nunca lanza.
    await _check_storage_quota(fake_repo, tenant, incoming_bytes=1024)


async def test_upload_file_plan_huerfano_devuelve_429_no_ilimitado(client, monkeypatch) -> None:
    """Regresión a nivel HTTP: `POST /v1/files` con un `plan_key` que no existe en
    `edecan_schemas.plans.PLANES` debía (antes del fix) aceptar el archivo sin
    ningún límite -- `files.py` no tiene ningún flag booleano previo que bloquee
    ese caso (a diferencia de `voice.py`, ver `test_voice.py::
    test_transcribe_without_voice_web_flag_returns_403`). Con el fix, 429."""
    import edecan_api.routers.files as files_module

    s3_calls: list[dict] = []

    class _FakeS3Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return None

        async def put_object(self, **kwargs):
            s3_calls.append(kwargs)

    class _FakeAioboto3Session:
        def client(self, service_name: str, **kwargs):
            assert service_name == "s3"
            return _FakeS3Client()

    monkeypatch.setattr(files_module.aioboto3, "Session", lambda: _FakeAioboto3Session())

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe")

    response = await client.post(
        "/v1/files",
        files={"file": ("archivo.pdf", b"contenido-de-prueba", "application/pdf")},
        headers=headers,
    )

    assert response.status_code == 429
    assert s3_calls == []  # el chequeo de cuota corta antes de tocar S3 -- nunca se sube nada


async def test_check_voice_quota_plan_huerfano_deniega_en_vez_de_ilimitado(fake_repo) -> None:
    """Misma regresión que `_check_storage_quota`, para `_check_voice_quota`
    (`voice.py`). A nivel HTTP este caso YA estaba cubierto por
    `_require_voice_web` (403 antes de llegar acá, ver
    `test_voice.py::test_transcribe_without_voice_web_flag_returns_403`) -- este
    test unitario prueba la función DIRECTAMENTE (sin pasar por ese primer gate)
    para confirmar que el segundo candado también es fail-closed, defensa en
    profundidad si algún día se reordenan los chequeos."""
    from fastapi import HTTPException

    from edecan_api.deps import TenantCtx
    from edecan_api.routers.voice import _check_voice_quota

    tenant = TenantCtx(tenant_id=uuid.uuid4(), plan_key="plan_no_existe", flags={})

    with pytest.raises(HTTPException) as exc_info:
        await _check_voice_quota(fake_repo, tenant, extra_seconds=5.0)
    assert exc_info.value.status_code == 429


async def test_check_voice_quota_plan_conocido_conserva_su_limite_real(fake_repo) -> None:
    from edecan_api.deps import TenantCtx, flags_for_plan
    from edecan_api.routers.voice import _check_voice_quota

    tenant = TenantCtx(
        tenant_id=uuid.uuid4(), plan_key="hosted_basic", flags=flags_for_plan("hosted_basic")
    )

    # hosted_basic trae 60 min = 3600s; 5s no se acerca al límite real -> nunca lanza.
    await _check_voice_quota(fake_repo, tenant, extra_seconds=5.0)


# ---------------------------------------------------------------------------
# Barrido B: `smarthome.py`/`skills.py` no exigen ningún flag de plan, por diseño
# (`packages/smarthome/edecan_smarthome/tools.py`/`packages/skills/edecan_skills/
# tools.py` lo documentan explícitamente: "instrucción explícita del work package,
# no toques edecan_schemas" / "sin requires_flags"). Un `plan_key` huérfano (flags
# vacíos) NO debe bloquear estas dos superficies -- a diferencia de `voice.web`/
# `agents.missions`/`automations.rules`/`companion.ide`, que sí las bloquean.
# ---------------------------------------------------------------------------


async def test_smarthome_status_no_exige_ningun_flag_de_plan(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe")

    response = await client.get("/v1/smarthome/status", headers=headers)

    assert response.status_code == 200
    assert response.json()["configured"] is False


async def test_skills_list_no_exige_ningun_flag_de_plan(app, client) -> None:
    """`conftest.app` deja `get_tenant_session` en `None` por defecto (ver docstring
    de `test_skills_router.py`) -- `GET /v1/skills` sí necesita un doble que entienda
    el `SELECT` de `edecan_skills.store.list_skills`, así que este test le asigna uno
    mínimo (siempre vacío, alcanza para probar que el 200 llega sin exigir ningún
    flag de plan)."""
    import edecan_api.deps as edecan_deps

    class _EmptyResult:
        def mappings(self) -> _EmptyResult:
            return self

        def all(self) -> list[dict]:
            return []

    class _EmptySession:
        async def execute(self, clause, params=None) -> _EmptyResult:
            return _EmptyResult()

    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: _EmptySession()

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe")

    response = await client.get("/v1/skills", headers=headers)

    assert response.status_code == 200
    assert response.json() == {"skills": []}


# ---------------------------------------------------------------------------
# Barrido C: `missions.py::confirm_mission` -- el `UPDATE ... status='running'`
# seguido de `enqueue(...)` es atómico (no es el patrón write-then-raise de
# `hooks.py`: acá NO hay evidencia legal/de cumplimiento involucrada, así que la
# reversión conjunta ante un fallo de `enqueue` es el comportamiento CORRECTO --
# nunca deja la misión en 'running' sin que el job de verdad se haya encolado).
# ---------------------------------------------------------------------------


async def test_confirm_mission_si_enqueue_falla_la_sesion_revierte_el_status() -> None:
    """No usa Postgres real: construye un `FakeSession` mínimo que registra si
    `execute()`/`rollback()` fueron invocados, y confirma que `confirm_mission`
    deja escapar la excepción de `enqueue` tal cual (no la traga) -- el llamador
    real (`get_tenant_session`, `ARCHITECTURE.md` §2/§10.3) es quien decide el
    rollback de la transacción completa al verla escapar; este test documenta que
    `missions.py` no hace nada que le impida hacerlo (p. ej. no comitea a medio
    camino, no atrapa la excepción)."""
    import edecan_api.routers.missions as missions_module
    from edecan_api.routers.missions import MissionConfirmIn

    class _FakeResult:
        def __init__(self, rows=None, scalar_value=None):
            self._rows = rows or []
            self._scalar_value = scalar_value

        def mappings(self):
            return self

        def first(self):
            return dict(self._rows[0]) if self._rows else None

        def all(self):
            return [dict(r) for r in self._rows]

        def scalar(self):
            return self._scalar_value

    class _FakeSession:
        def __init__(self, mission_row, step_seq):
            self._mission_row = dict(mission_row)
            self._step_seq = step_seq
            self.commits = 0

        async def execute(self, clause, params=None):
            sql = str(clause)
            if sql.startswith("SELECT") and "agent_missions" in sql:
                return _FakeResult(rows=[self._mission_row])
            if sql.startswith("SELECT seq FROM agent_steps"):
                return _FakeResult(rows=[{"seq": self._step_seq}])
            if sql.startswith("UPDATE agent_missions"):
                self._mission_row["status"] = params["status"]
                return _FakeResult()
            return _FakeResult()

        async def commit(self):
            self.commits += 1

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    mission_row = {
        "id": str(mission_id),
        "tenant_id": str(tenant_id),
        "user_id": str(user_id),
        "objetivo": "x",
        "status": "waiting_confirmation",
        "plan": None,
        "resultado": None,
        "presupuesto": {},
        "error": None,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    session = _FakeSession(mission_row, step_seq=1)
    current_user = _current_user(tenant_id, user_id)

    async def _enqueue_que_falla(*args, **kwargs):
        raise RuntimeError("SQS caído (simulado)")

    original_enqueue = missions_module.enqueue
    missions_module.enqueue = _enqueue_que_falla  # type: ignore[assignment]
    try:
        with pytest.raises(RuntimeError):
            await missions_module.confirm_mission(
                mission_id,
                MissionConfirmIn(approved=True),
                current_user,
                session,  # type: ignore[arg-type]
                object(),  # settings: no se usa antes de que enqueue lance
            )
        # La sesión NUNCA comiteó nada por su cuenta -- el rollback (si el
        # llamador real lo hace, como `get_tenant_session`) queda enteramente en
        # manos de quien abrió la sesión, nunca de este router.
        assert session.commits == 0
    finally:
        missions_module.enqueue = original_enqueue  # type: ignore[assignment]
