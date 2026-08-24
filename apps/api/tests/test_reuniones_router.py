"""Tests de `edecan_api.routers.reuniones` — offline: `FakeSession`/`FakeRepo`
en memoria (mismo patrón que `test_voz_avanzada.py`/`test_viajes_router.py`),
`enqueue` monkeypatcheado (cero red/SQS real). Al final del archivo hay,
además, una sección de integración contra Postgres real (BARRIDO D,
WP-V7-04, gateada por `DATABASE_URL`).

`reuniones.router` SÍ está en `V6_ROUTER_NAMES` (`edecan_api.main`, v6 se
completó — corrección 2026-07-09, WP-V7-04: esta nota decía "todavía no
aterrizó" desde que WP-V6-05 escribió el router en paralelo con el linchpin
de v6, quedó desactualizada) — este archivo igual lo monta manualmente sobre
la `app` de `conftest.py`, mismo patrón defensivo/redundante que
`test_voz_avanzada.py`/`test_erp_router.py` usan incluso para routers ya
montados en producción.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import auth_headers
from httpx import ASGITransport, AsyncClient

from edecan_api import deps as edecan_deps
from edecan_api.routers import reuniones


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)


@dataclass
class FakeSession:
    respuestas: list[list[dict[str, Any]]] = field(default_factory=list)
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    commits: int = 0
    rowcount_por_llamada: list[int] = field(default_factory=list)

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> Any:
        self.llamadas.append((str(stmt), dict(params or {})))
        filas = self.respuestas.pop(0) if self.respuestas else []
        resultado = FakeResult(filas)
        if self.rowcount_por_llamada:
            resultado.rowcount = self.rowcount_por_llamada.pop(0)  # type: ignore[attr-defined]
        else:
            resultado.rowcount = len(filas)  # type: ignore[attr-defined]
        return resultado

    async def commit(self) -> None:
        self.commits += 1


@pytest.fixture
def fake_session() -> FakeSession:
    return FakeSession()


@pytest.fixture
def enqueue_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Any, str, dict, Any]]:
    llamadas: list[tuple[Any, str, dict, Any]] = []

    async def fake_enqueue(
        settings: Any, job_type: str, payload: dict, tenant_id: Any
    ) -> uuid.UUID:
        llamadas.append((settings, job_type, payload, tenant_id))
        return uuid.uuid4()

    monkeypatch.setattr(reuniones, "enqueue", fake_enqueue)
    return llamadas


@pytest.fixture
def _mounted_app(app, fake_session: FakeSession):
    """`app` (de `conftest.py`) + `reuniones.router` montado + `get_tenant_session`
    reemplazado por `fake_session` — mismo patrón que `test_voz_avanzada.py`."""
    app.include_router(reuniones.router)
    app.dependency_overrides[edecan_deps.get_tenant_session] = lambda: fake_session
    return app


@pytest.fixture
async def client(_mounted_app) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=_mounted_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _headers_con_meetings(
    app, *, tenant_id: uuid.UUID, user_id: uuid.UUID | None = None
) -> dict[str, str]:
    """`tools.meetings` SÍ está pinned en `edecan_schemas.plans` (v6 se
    completó — corrección 2026-07-09, WP-V7-04: `hosted_pro` ya lo trae
    `True` de verdad), pero este archivo lo sigue fijando explícito acá en
    vez de depender de la matriz real de planes — mismo criterio que
    `test_voz_avanzada.py::_headers_con_cloning`: sobreescribe
    `get_current_user` directo con un `CurrentUser` que trae el flag,
    independiente de si `PLANES` cambia de forma en el futuro."""
    user_id = user_id or uuid.uuid4()
    tenant = edecan_deps.TenantCtx(
        tenant_id=tenant_id, plan_key="hosted_pro", flags={"tools.meetings": True}
    )
    current_user = edecan_deps.CurrentUser(user_id=user_id, tenant=tenant)
    app.dependency_overrides[edecan_deps.get_current_user] = lambda: current_user
    return auth_headers(user_id=user_id, tenant_id=tenant_id, plan_key="hosted_pro")


def _reunion_row(**overrides: Any) -> dict[str, Any]:
    """Fila cruda `meetings` — mismas columnas EXACTAS del esquema real
    (`ARCHITECTURE.md` §15.b / `0008_v6_expansion.py` / `edecan_db.models.
    Meeting`): `source_file_id` (no `file_id`) y un único blob `minutos`
    JSONB (no columnas `decisiones`/`acciones`/`temas` propias) — ver el
    docstring de `reuniones.py`, sección "Tabla `meetings`"."""
    now = datetime.now(UTC)
    base = {
        "id": uuid.uuid4(),
        "tenant_id": uuid.uuid4(),
        "user_id": uuid.uuid4(),
        "source_file_id": uuid.uuid4(),
        "titulo": "Standup",
        "status": "pending",
        "transcript_file_id": None,
        "resumen": None,
        "minutos": None,
        "duracion_segundos": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------


async def test_crear_reunion_requires_authentication(client) -> None:
    response = await client.post("/v1/reuniones", json={"file_id": str(uuid.uuid4())})
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# POST /v1/reuniones
# ---------------------------------------------------------------------------


async def test_crear_reunion_archivo_inexistente_returns_404(
    client, fake_repo, fake_session, app, enqueue_calls
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)

    response = await client.post(
        "/v1/reuniones", json={"file_id": str(uuid.uuid4())}, headers=headers
    )

    assert response.status_code == 404
    assert fake_session.llamadas == []
    assert enqueue_calls == []


async def test_crear_reunion_archivo_no_es_audio_ni_video_returns_400(
    client, fake_repo, fake_session, app, enqueue_calls
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "filename": "contrato.pdf",
        "mime": "application/pdf",
        "s3_key": "x",
        "size_bytes": 10,
        "status": "ready",
    }

    response = await client.post("/v1/reuniones", json={"file_id": str(file_id)}, headers=headers)

    assert response.status_code == 400
    assert enqueue_calls == []


async def test_crear_reunion_de_otro_tenant_returns_404(
    client, fake_repo, fake_session, app, enqueue_calls
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": uuid.uuid4(),  # OTRO tenant
        "filename": "reunion.mp3",
        "mime": "audio/mpeg",
        "s3_key": "x",
        "size_bytes": 10,
        "status": "ready",
    }

    response = await client.post("/v1/reuniones", json={"file_id": str(file_id)}, headers=headers)

    assert response.status_code == 404


@pytest.mark.parametrize("mime", ["audio/mpeg", "audio/wav", "video/mp4", "video/quicktime"])
async def test_crear_reunion_camino_feliz_inserta_y_encola(
    client, fake_repo, fake_session, app, enqueue_calls, mime: str
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id, user_id=user_id)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "filename": "reunion.dat",
        "mime": mime,
        "s3_key": "x",
        "size_bytes": 10,
        "status": "ready",
    }
    meeting_id = uuid.uuid4()
    fila_insertada = _reunion_row(
        id=meeting_id,
        tenant_id=tenant_id,
        user_id=user_id,
        source_file_id=file_id,
        titulo="Kickoff",
    )
    fake_session.respuestas = [[fila_insertada]]

    response = await client.post(
        "/v1/reuniones",
        json={"file_id": str(file_id), "titulo": "Kickoff"},
        headers=headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["id"] == str(meeting_id)
    assert body["titulo"] == "Kickoff"
    assert body["status"] == "pending"
    assert body["file_id"] == str(file_id)

    sql_insert, params = fake_session.llamadas[0]
    assert "INSERT INTO meetings" in sql_insert
    # Columna real (`ARCHITECTURE.md` §15.b): `source_file_id`, no `file_id`
    # — y `status` inicial `'pending'`, único valor que acepta el CHECK real.
    # Ver `reuniones.py`, docstring, sección "Tabla `meetings`".
    assert "source_file_id" in sql_insert
    assert "'pending'" in sql_insert
    assert params["tenant_id"] == str(tenant_id)
    assert params["user_id"] == str(user_id)
    assert params["file_id"] == str(file_id)
    assert params["titulo"] == "Kickoff"

    assert len(enqueue_calls) == 1
    _settings, job_type, payload, enq_tenant_id = enqueue_calls[0]
    assert job_type == "process_meeting"
    assert payload == {"meeting_id": str(meeting_id)}
    assert enq_tenant_id == tenant_id

    assert len(fake_repo.audit_log) == 1
    assert fake_repo.audit_log[0]["action"] == "reuniones.created"


async def test_crear_reunion_sin_titulo_usa_filename(
    client, fake_repo, fake_session, app, enqueue_calls
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    file_id = uuid.uuid4()
    fake_repo.files[file_id] = {
        "id": file_id,
        "tenant_id": tenant_id,
        "filename": "standup-lunes.m4a",
        "mime": "audio/mp4",
        "s3_key": "x",
        "size_bytes": 10,
        "status": "ready",
    }
    fake_session.respuestas = [
        [_reunion_row(tenant_id=tenant_id, source_file_id=file_id, titulo="standup-lunes.m4a")]
    ]

    response = await client.post("/v1/reuniones", json={"file_id": str(file_id)}, headers=headers)

    assert response.status_code == 202
    _sql, params = fake_session.llamadas[0]
    assert params["titulo"] == "standup-lunes.m4a"


# ---------------------------------------------------------------------------
# GET /v1/reuniones, GET /v1/reuniones/{id}
# ---------------------------------------------------------------------------


async def test_listar_reuniones(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    filas = [
        _reunion_row(tenant_id=tenant_id, titulo="Reunión 1"),
        _reunion_row(tenant_id=tenant_id, titulo="Reunión 2", status="done", resumen="R"),
    ]
    fake_session.respuestas = [filas]

    response = await client.get("/v1/reuniones", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert body[1]["resumen"] == "R"


async def test_obtener_reunion_con_minutas_completas(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    meeting_id = uuid.uuid4()
    transcript_id = uuid.uuid4()
    fila = _reunion_row(
        id=meeting_id,
        tenant_id=tenant_id,
        status="done",
        resumen="Se decidió lanzar en marzo.",
        minutos={
            "decisiones": ["Lanzar en marzo"],
            "acciones": [{"tarea": "Escribir el plan", "responsable": "Ana"}],
            "temas": ["roadmap"],
        },
        transcript_file_id=transcript_id,
        duracion_segundos=125.5,
    )
    fake_session.respuestas = [[fila]]

    response = await client.get(f"/v1/reuniones/{meeting_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["resumen"] == "Se decidió lanzar en marzo."
    assert body["decisiones"] == ["Lanzar en marzo"]
    assert body["acciones"] == [{"tarea": "Escribir el plan", "responsable": "Ana"}]
    assert body["temas"] == ["roadmap"]
    assert body["transcript_file_id"] == str(transcript_id)
    assert body["duracion_segundos"] == 125.5


async def test_obtener_reunion_decisiones_como_texto_json_crudo_se_parsea(
    client, fake_repo, fake_session, app
) -> None:
    """Defensivo (`_dict_desde_jsonb`/`_lista_desde_jsonb`): si el driver
    devuelve `minutos` (el único blob jsonb real) como texto crudo en vez de
    ya decodificado, el router igual lo parsea — mismo criterio que
    `routers/ads.py::_from_jsonb`."""
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    meeting_id = uuid.uuid4()
    fila = _reunion_row(
        id=meeting_id,
        tenant_id=tenant_id,
        minutos='{"decisiones": ["Lanzar en marzo"], '
        '"acciones": [{"tarea": "X", "responsable": null}], "temas": []}',
    )
    fake_session.respuestas = [[fila]]

    response = await client.get(f"/v1/reuniones/{meeting_id}", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["decisiones"] == ["Lanzar en marzo"]
    assert body["acciones"] == [{"tarea": "X", "responsable": None}]


async def test_obtener_reunion_inexistente_returns_404(
    client, fake_repo, fake_session, app
) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    fake_session.respuestas = [[]]

    response = await client.get(f"/v1/reuniones/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /v1/reuniones/{id}
# ---------------------------------------------------------------------------


async def test_borrar_reunion_camino_feliz(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    meeting_id = uuid.uuid4()
    fake_session.rowcount_por_llamada = [1]

    response = await client.delete(f"/v1/reuniones/{meeting_id}", headers=headers)

    assert response.status_code == 204
    sql_delete, params = fake_session.llamadas[0]
    assert "DELETE FROM meetings" in sql_delete
    assert params["id"] == str(meeting_id)
    assert params["tenant_id"] == str(tenant_id)
    assert len(fake_repo.audit_log) == 1
    assert fake_repo.audit_log[0]["action"] == "reuniones.deleted"


async def test_borrar_reunion_inexistente_returns_404(client, fake_repo, fake_session, app) -> None:
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    fake_session.rowcount_por_llamada = [0]

    response = await client.delete(f"/v1/reuniones/{uuid.uuid4()}", headers=headers)

    assert response.status_code == 404
    assert fake_repo.audit_log == []


async def test_borrar_reunion_no_borra_los_archivos(client, fake_repo, fake_session, app) -> None:
    """Ver docstring del router: DELETE solo borra la fila `meetings`, nunca
    toca `files` — este test documenta esa garantía asegurando que el único
    SQL ejecutado es el DELETE sobre `meetings`."""
    tenant_id = uuid.uuid4()
    headers = _headers_con_meetings(app, tenant_id=tenant_id)
    meeting_id = uuid.uuid4()
    fake_session.rowcount_por_llamada = [1]

    await client.delete(f"/v1/reuniones/{meeting_id}", headers=headers)

    assert len(fake_session.llamadas) == 1
    sql, _params = fake_session.llamadas[0]
    assert "files" not in sql.lower()


# ---------------------------------------------------------------------------
# Integración: contra Postgres real (BARRIDO D, WP-V7-04)
# ---------------------------------------------------------------------------
#
# El fix de esquema de v6 (`meetings.source_file_id`/`minutos` JSONB/status
# `pending`, ver el docstring del router) nunca había tenido una ronda de
# re-verificación contra Postgres real — solo contra `FakeSession`, que
# mockea filas con el MISMO esquema que asume el código (invisible a un
# `UndefinedColumnError` real). Esta sección lo corrige: Docker desechable +
# `alembic upgrade head`, gateada por `DATABASE_URL` (se salta sola si no
# está configurada/alcanzable, JAMÁS falla por eso) — mismo patrón
# autocontenido que `apps/api/tests/test_repo_sql_integration.py` (cada
# módulo de test de integración duplica su propia lógica de skip/fixture a
# propósito, ver el docstring de ese archivo).
#
# Llama `reuniones.crear_reunion`/`obtener_reunion` DIRECTO como funciones
# Python normales (los `Depends(...)` de FastAPI son solo valores default;
# pasar los argumentos explícitos los pisa sin necesitar montar la app ASGI
# completa) con un `AsyncSession`/`SqlRepo` REALES — exactamente el mismo
# código que corre `POST /v1/reuniones` en producción, incluida la sentencia
# `INSERT INTO meetings` real. `enqueue` se monkeypatchea (cero SQS/red real,
# no es parte de lo que esta sección verifica).


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


def _skip_reason_integracion() -> str | None:
    import asyncio

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


_SKIP_REASON_INTEGRACION = _skip_reason_integracion()


async def _aplicar_migraciones(database_url: str) -> None:
    """Aplica hasta `head` (idempotente: no-op si ya están aplicadas)."""
    import asyncio
    from pathlib import Path

    from alembic.command import upgrade
    from alembic.config import Config

    db_dir = Path(__file__).resolve().parents[3] / "packages" / "db"
    cfg = Config(str(db_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(db_dir / "alembic"))
    cfg.set_main_option("sqlalchemy.url", database_url)
    await asyncio.to_thread(upgrade, cfg, "head")


@pytest.fixture
async def db_real(monkeypatch: pytest.MonkeyPatch):
    """Prepara `edecan_db.settings`/`engine` para apuntar a `DATABASE_URL`,
    aplica migraciones, y limpia sus cachés `lru_cache` al terminar — mismo
    patrón que `test_repo_sql_integration.py::db`."""
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


async def _seed_tenant_y_archivo(sufijo: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Tenant + user + una fila `files` (el audio de origen) directo con el
    ORM de `edecan_db` contra Postgres real — `meetings.tenant_id`/`user_id`/
    `files.tenant_id`/`user_id` llevan FK reales, así que hacen falta filas
    padre de verdad (a diferencia de `FakeSession`, que no las exige)."""
    from edecan_db.models import File, Tenant, User
    from edecan_db.session import get_session

    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    file_id = uuid.uuid4()
    async with get_session(None) as session:
        session.add(
            Tenant(
                id=tenant_id,
                name=f"v7 reuniones {sufijo}",
                slug=f"v7-reuniones-{sufijo}",
                plan_key="hosted_pro",
            )
        )
        session.add(
            User(id=user_id, email=f"v7-reuniones-{sufijo}@example.com", password_hash="x" * 20)
        )
        await session.flush()
        session.add(
            File(
                id=file_id,
                tenant_id=tenant_id,
                user_id=user_id,
                s3_key=f"tenants/{tenant_id}/files/{file_id}/reunion.mp3",
                filename="reunion.mp3",
                mime="audio/mpeg",
                size_bytes=10,
                status="ready",
            )
        )
    return tenant_id, user_id, file_id


async def _cleanup_tenant(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    from edecan_db.models import Tenant, User
    from edecan_db.session import get_session
    from sqlalchemy import delete

    async with get_session(None) as session:
        # El tenant primero: `ON DELETE CASCADE` se lleva `files`/`meetings`.
        await session.execute(delete(Tenant).where(Tenant.id == tenant_id))
    async with get_session(None) as session:
        await session.execute(delete(User).where(User.id == user_id))


@pytest.mark.integration
@pytest.mark.skipif(_SKIP_REASON_INTEGRACION is not None, reason=_SKIP_REASON_INTEGRACION or "")
async def test_integracion_crear_y_obtener_reunion_contra_postgres_real(
    db_real, monkeypatch: pytest.MonkeyPatch
) -> None:
    from edecan_db.session import get_session

    from edecan_api.config import Settings as ApiSettings
    from edecan_api.deps import CurrentUser, TenantCtx, flags_for_plan
    from edecan_api.repo import SqlRepo

    sufijo = uuid.uuid4().hex[:8]
    tenant_id, user_id, file_id = await _seed_tenant_y_archivo(sufijo)

    llamadas_enqueue: list[tuple[str, dict]] = []

    async def _fake_enqueue(settings: Any, job_type: str, payload: dict, tenant_id_arg: Any):
        llamadas_enqueue.append((job_type, payload))
        return uuid.uuid4()

    monkeypatch.setattr(reuniones, "enqueue", _fake_enqueue)

    try:
        # `flags_for_plan` es la MISMA función que usa `get_current_user` real
        # (`edecan_api.deps`) — a diferencia de `_headers_con_meetings` (que
        # fija el flag a mano), acá se deja que la matriz REAL de
        # `edecan_schemas.plans` decida, para confirmar que `hosted_pro`
        # trae `tools.meetings=True` de verdad (BARRIDO B).
        current_user = CurrentUser(
            user_id=user_id,
            tenant=TenantCtx(
                tenant_id=tenant_id, plan_key="hosted_pro", flags=flags_for_plan("hosted_pro")
            ),
        )
        assert current_user.tenant.flags.get("tools.meetings") is True

        async with get_session(tenant_id) as session:
            repo = SqlRepo(session)
            creada = await reuniones.crear_reunion(
                payload=reuniones.ReunionIn(file_id=file_id, titulo="Integración v7"),
                current_user=current_user,
                repo=repo,
                session=session,
                settings=ApiSettings(),
            )

        # Columna real `status='pending'` (nunca `'queued'`) — el CHECK de
        # `0008_v6_expansion.py` habría rechazado `'queued'` con un
        # `CheckViolationError` real, algo que `FakeSession` jamás detecta.
        assert creada.status == "pending"
        assert creada.titulo == "Integración v7"
        assert creada.file_id == file_id
        assert llamadas_enqueue == [("process_meeting", {"meeting_id": str(creada.id)})]

        # `GET /v1/reuniones/{id}` (la función real) sobre la MISMA fila,
        # confirmando que el mapeo `source_file_id -> file_id` sobrevive un
        # viaje real de ida y vuelta por Postgres.
        async with get_session(tenant_id) as session:
            obtenida = await reuniones.obtener_reunion(
                reunion_id=creada.id, current_user=current_user, session=session
            )
        assert obtenida.id == creada.id
        assert obtenida.file_id == file_id
        assert obtenida.status == "pending"

        # Verificación cruda adicional: las columnas reales existen y tienen
        # los valores esperados (más allá de lo que ya validó `ReunionOut`).
        async with get_session(None) as session:
            from sqlalchemy import text as sql_text_real

            fila = (
                (
                    await session.execute(
                        sql_text_real("SELECT * FROM meetings WHERE id = :id"), {"id": creada.id}
                    )
                )
                .mappings()
                .first()
            )
        assert fila is not None
        assert fila["source_file_id"] == file_id
        assert fila["tenant_id"] == tenant_id
        assert fila["minutos"] is None  # todavía no procesada por el worker
    finally:
        await _cleanup_tenant(tenant_id, user_id)
