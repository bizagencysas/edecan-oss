"""Tests del job `daily_brief` (PHASE2.md §56-57): composición del brief,
entrega en el chat principal + push, idempotencia por día y degradación
best-effort del calendario (tabla todavía no pinneada).

`SqlRepo` se sustituye por `fakes.FakeRepo` (mensaje en el chat principal) y
`push.enviar_push_a_usuario` se monkeypatchea, mismo patrón que
`test_gym_checkin.py`. El `FakeSession` de este módulo solo entiende el SQL
que `daily_brief` emite (conteos de `automation_runs`/`reminders`, nombres de
automatizaciones fallidas, `audit_log` para la marca de entrega y el fallo
simulado de `calendar_events`).
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import edecan_worker.handlers.daily_brief as daily_brief_module
import edecan_worker.push as push_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, make_deps


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class FakeSession:
    def __init__(self) -> None:
        self.runs: list[dict[str, Any]] = []
        self.reminders: list[dict[str, Any]] = []
        self.failed_names: list[str] = []
        self.calendar_events: list[dict[str, Any]] = []
        self.calendar_raises = True
        self.brief_deliveries: set[str] = set()

    def seed_run(self, status: str, *, started_at: datetime | None = None) -> None:
        self.runs.append({"status": status, "started_at": started_at})

    def seed_reminder(self, user_id: uuid.UUID, status: str) -> None:
        self.reminders.append({"user_id": str(user_id), "status": status})

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})

        if "pg_advisory_xact_lock" in sql:
            return _FakeResult([])
        if "INSERT INTO audit_log" in sql:
            self.brief_deliveries.add(params["target"])
            return _FakeResult([])
        if "FROM audit_log" in sql:
            existe = params["target"] in self.brief_deliveries
            return _FakeResult([{"id": "x"}] if existe else [])
        if "FROM automation_runs" in sql and "COUNT" in sql:
            status = params["status"]
            desde = params.get("desde")
            n = sum(
                1
                for r in self.runs
                if r["status"] == status and (desde is None or r["started_at"] >= desde)
            )
            return _FakeResult([{"n": n}])
        if "a.nombre" in sql:
            return _FakeResult([{"nombre": nombre} for nombre in self.failed_names])
        if "FROM reminders" in sql:
            n = sum(
                1
                for r in self.reminders
                if r["status"] == "pending" and r["user_id"] == params["user_id"]
            )
            return _FakeResult([{"n": n}])
        if "calendar_events" in sql:
            if self.calendar_raises:
                raise RuntimeError('relation "calendar_events" does not exist')
            return _FakeResult(
                [{"titulo": e["titulo"], "inicio_at": e["inicio_at"]} for e in self.calendar_events]
            )
        raise AssertionError(f"SQL no reconocido en el fake: {sql}")


def _session_factory(session: FakeSession):
    @asynccontextmanager
    async def _factory(tenant_id):
        yield session

    return _factory


def _envelope(tenant_id: uuid.UUID | None, user_id: uuid.UUID | None = None) -> JobEnvelope:
    # `model_construct` salta la validación de `type`: "daily_brief" todavía no
    # está en `edecan_schemas.queue.JOB_TYPES` (fuera del alcance de este WP).
    # El handler solo lee `tenant_id`/`payload`, así que basta con armarlo a mano.
    payload = {"user_id": str(user_id)} if user_id is not None else {}
    return JobEnvelope.model_construct(
        job_id=uuid.uuid4(), tenant_id=tenant_id, type="daily_brief", payload=payload
    )


# ---------------------------------------------------------------------------
# smart_resume (PHASE2 §57)
# ---------------------------------------------------------------------------


async def test_smart_resume_resume_en_espanol() -> None:
    session = FakeSession()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))
    session.seed_run("done", started_at=ahora - timedelta(hours=2))
    session.seed_run("error", started_at=ahora - timedelta(hours=3))
    session.seed_run("running", started_at=ahora - timedelta(hours=4))

    texto = await daily_brief_module.smart_resume(tenant_id, user_id, session)

    assert texto == (
        "Desde la última vez:\n"
        "- se completaron 2 automatizaciones\n"
        "- falló 1\n"
        "- sigue pendiente 1"
    )


async def test_smart_resume_singular_cuando_hay_uno() -> None:
    session = FakeSession()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))
    session.seed_run("error", started_at=ahora - timedelta(hours=2))
    session.seed_run("running")

    texto = await daily_brief_module.smart_resume(uuid.uuid4(), uuid.uuid4(), session)

    assert "se completó 1 automatización" in texto
    assert "falló 1" in texto
    assert "sigue pendiente 1" in texto


# ---------------------------------------------------------------------------
# handle: composición, entrega y push
# ---------------------------------------------------------------------------


async def test_handle_compone_brief_y_entrega_chat_mas_push(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    fake_repo = FakeRepo()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))
    session.seed_run("done", started_at=ahora - timedelta(hours=2))
    session.seed_run("error", started_at=ahora - timedelta(hours=3))
    session.failed_names = ["Publicar post"]
    session.seed_reminder(user_id, "pending")
    session.seed_reminder(user_id, "pending")

    pushes: list[dict[str, Any]] = []

    async def fake_push(deps, *, tenant_id, user_id, titulo, cuerpo, data=None, category=None):
        pushes.append({"titulo": titulo, "cuerpo": cuerpo, "data": data})
        return push_module.ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(daily_brief_module, "SqlRepo", lambda s: fake_repo)
    monkeypatch.setattr(daily_brief_module.push, "enviar_push_a_usuario", fake_push)
    deps = make_deps(session_factory=_session_factory(session))

    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)

    assert len(fake_repo.messages) == 1
    msg = fake_repo.messages[0]
    assert msg["role"] == "assistant"
    texto = msg["content"]["text"]
    assert "Resumen de hoy" in texto
    assert "corrieron bien 2 automatizaciones" in texto
    assert "falló 1 (Publicar post)" in texto
    assert "2 recordatorios pendientes" in texto

    assert len(pushes) == 1
    assert pushes[0]["titulo"] == "Edecán"
    assert pushes[0]["cuerpo"].startswith("Hoy: ")
    assert pushes[0]["data"]["chat_id"] == str(msg["conversation_id"])
    assert "falló" in pushes[0]["cuerpo"]

    assert len(session.brief_deliveries) == 1


async def test_handle_segunda_vez_el_mismo_dia_no_duplica(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    fake_repo = FakeRepo()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))

    pushes: list[dict[str, Any]] = []

    async def fake_push(deps, **kwargs):
        pushes.append(kwargs)
        return push_module.ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(daily_brief_module, "SqlRepo", lambda s: fake_repo)
    monkeypatch.setattr(daily_brief_module.push, "enviar_push_a_usuario", fake_push)
    deps = make_deps(session_factory=_session_factory(session))

    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)
    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)

    assert len(fake_repo.messages) == 1
    assert len(pushes) == 1


async def test_handle_sin_contenido_no_envia_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    fake_repo = FakeRepo()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    pushes: list[dict[str, Any]] = []

    monkeypatch.setattr(daily_brief_module, "SqlRepo", lambda s: fake_repo)
    monkeypatch.setattr(
        daily_brief_module.push,
        "enviar_push_a_usuario",
        lambda *a, **k: pushes.append(k) or push_module.ResultadoEnvioPush(0, 0),
    )
    deps = make_deps(session_factory=_session_factory(session))

    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)

    assert fake_repo.messages == []
    assert pushes == []
    assert session.brief_deliveries == set()


async def test_handle_calendario_inaccesible_degrada_con_gracia(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.calendar_raises = True  # la tabla no existe todavía
    fake_repo = FakeRepo()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))

    pushes: list[dict[str, Any]] = []

    async def fake_push(deps, **kwargs):
        pushes.append(kwargs)
        return push_module.ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(daily_brief_module, "SqlRepo", lambda s: fake_repo)
    monkeypatch.setattr(daily_brief_module.push, "enviar_push_a_usuario", fake_push)
    deps = make_deps(session_factory=_session_factory(session))

    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)

    assert len(fake_repo.messages) == 1
    assert len(pushes) == 1


async def test_handle_calendario_disponible_incluye_eventos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    session.calendar_raises = False
    session.calendar_events = [
        {"titulo": "Reunión con cliente", "inicio_at": datetime.now(UTC) + timedelta(hours=2)}
    ]
    fake_repo = FakeRepo()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ahora = datetime.now(UTC)
    session.seed_run("done", started_at=ahora - timedelta(hours=1))

    async def fake_push(deps, **kwargs):
        return push_module.ResultadoEnvioPush(1, 0)

    monkeypatch.setattr(daily_brief_module, "SqlRepo", lambda s: fake_repo)
    monkeypatch.setattr(daily_brief_module.push, "enviar_push_a_usuario", fake_push)
    deps = make_deps(session_factory=_session_factory(session))

    await daily_brief_module.handle(_envelope(tenant_id, user_id), deps)

    [msg] = fake_repo.messages
    assert "Reunión con cliente" in msg["content"]["text"]


# ---------------------------------------------------------------------------
# Validación de contrato
# ---------------------------------------------------------------------------


async def test_handle_sin_tenant_id_levanta(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps()
    env = _envelope(None, uuid.uuid4())
    with pytest.raises(ValueError, match="tenant_id"):
        await daily_brief_module.handle(env, deps)


async def test_handle_sin_user_id_levanta(monkeypatch: pytest.MonkeyPatch) -> None:
    deps = make_deps()
    env = _envelope(uuid.uuid4(), None)
    with pytest.raises(ValueError, match="user_id"):
        await daily_brief_module.handle(env, deps)