"""Tests de la acción `gym_checkin` en el worker: el handler determinista
`run_gym_checkin` (card + push con `category`) y el despacho desde
`run_automation.py` (kind `gym_checkin` NO corre el turno de agente).

`edecan_automations` se stubea vía `sys.modules` (mismo patrón que
`test_automation_handlers.py`) porque `run_automation.handle` lo importa de
forma perezosa al tope, incluso para la rama determinista. El `FakeSession`
solo entiende el SQL de `automations`/`automation_runs`/`tenants`/`personas`
(la rama `gym_checkin` corta ANTES de `deps.llm_router_for`, así que no toca
`connector_accounts`). Para el handler en sí se usa `fakes.FakeRepo` (card en
el chat principal) y se monkeypatchea `push.enviar_push_a_usuario`.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from contextlib import asynccontextmanager
from typing import Any

import edecan_worker.handlers.run_automation as run_automation_module
import edecan_worker.handlers.run_gym_checkin as run_gym_checkin_module
import edecan_worker.push as push_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, install_companion_wake_capture, make_deps
from edecan_core.companion_wake_enqueue import RUN_COMPANION_TURN_JOB

# ---------------------------------------------------------------------------
# FakeSession para el despacho de `run_automation` (ramo gym_checkin)
# ---------------------------------------------------------------------------


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
        self.automations: dict[str, dict[str, Any]] = {}
        self.automation_runs: dict[str, dict[str, Any]] = {}
        self.tenants: dict[str, dict[str, Any]] = {}
        self.personas: dict[str, dict[str, Any]] = {}

    def seed_automation(self, automation_id, tenant_id, **fields) -> None:
        row = {
            "id": str(automation_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "nombre": "Gimnasio",
            "descripcion": "",
            "trigger": json.dumps({"kind": "schedule", "rrule": "FREQ=WEEKLY;BYDAY=MO"}),
            "accion": json.dumps({"kind": "gym_checkin", "objetivo": None}),
            "enabled": True,
            "next_run_at": None,
            "last_run_at": None,
        }
        row.update(fields)
        self.automations[str(automation_id)] = row

    def seed_tenant(self, tenant_id, plan_key: str) -> None:
        self.tenants[str(tenant_id)] = {"plan_key": plan_key}

    def seed_persona(self, user_id, **fields) -> None:
        row = {
            "nombre_asistente": "Edecán",
            "idioma": "es",
            "tono": "cálido y profesional",
            "formalidad": 1,
            "emojis": False,
            "instrucciones": "",
            "rasgos": [],
            "memoria_activada": True,
            "voice_id": None,
        }
        row.update(fields)
        self.personas[str(user_id)] = row

    async def execute(self, clause, params=None):
        sql = str(clause)
        params = dict(params or {})
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM automations" in sql:
            row = self.automations.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                return _FakeResult([row])
            return _FakeResult([])

        if primer_token == "SELECT" and "FROM tenants" in sql:
            row = self.tenants.get(params["id"])
            return _FakeResult([row] if row is not None else [])

        if primer_token == "SELECT" and "FROM personas" in sql:
            row = self.personas.get(params["user_id"])
            return _FakeResult([row] if row is not None else [])

        if primer_token == "INSERT" and "automation_runs" in sql:
            self.automation_runs[params["id"]] = {
                "id": params["id"],
                "tenant_id": params["tenant_id"],
                "automation_id": params["automation_id"],
                "status": "running",
                "detalle": json.loads(params["detalle"]),
            }
            return _FakeResult()

        if primer_token == "UPDATE" and "automation_runs" in sql:
            row = self.automation_runs.get(params["id"])
            if row is not None:
                row["status"] = params["status"]
                row["detalle"] = json.loads(params["detalle"])
            return _FakeResult()

        if primer_token == "UPDATE" and "automations" in sql and "last_run_at" in sql:
            row = self.automations.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                row["last_run_at"] = "touched"
            return _FakeResult()

        raise AssertionError(f"query inesperada en el fake: {sql}")


def _session_factory(session: FakeSession):
    @asynccontextmanager
    async def _factory(tenant_id):
        yield session

    return _factory


def _envelope(automation_id: uuid.UUID, tenant_id: uuid.UUID | None) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_automation",
        payload={"automation_id": str(automation_id)},
    )


@pytest.fixture(autouse=True)
def _fake_edecan_automations(monkeypatch: pytest.MonkeyPatch):
    """Stub de `edecan_automations` (parent + runner + engine) — ver docstring
    del módulo. `run_automation.handle` lo importa al tope, aun para la rama
    determinista."""
    fake_runner_module = types.ModuleType("edecan_automations.runner")
    fake_runner_module.RunnerDeps = type("RunnerDeps", (), {})  # type: ignore[attr-defined]
    runner_calls: list[tuple] = []

    async def _run_automation_turn(automation, deps):
        runner_calls.append((automation, deps))

    fake_runner_module.run_automation = _run_automation_turn  # type: ignore[attr-defined]
    fake_engine_module = types.ModuleType("edecan_automations.engine")
    fake_engine_module.compute_next_run = lambda *a, **k: None  # type: ignore[attr-defined]
    fake_package = types.ModuleType("edecan_automations")
    fake_package.runner = fake_runner_module  # type: ignore[attr-defined]
    fake_package.engine = fake_engine_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.runner", fake_runner_module)
    monkeypatch.setitem(sys.modules, "edecan_automations.engine", fake_engine_module)
    monkeypatch.setattr(run_automation_module, "_build_registry", lambda: object())
    return runner_calls


# ---------------------------------------------------------------------------
# Despacho: kind "gym_checkin" -> run_gym_checkin (no agente)
# ---------------------------------------------------------------------------


async def test_run_automation_gym_checkin_despacha_a_run_gym_checkin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session.seed_automation(
        automation_id,
        tenant_id,
        user_id=str(user_id),
        accion=json.dumps(
            {"kind": "gym_checkin", "objetivo": None, "seed_id": "gym_checkin_diario"}
        ),
    )
    session.seed_tenant(tenant_id, "hosted_pro")
    session.seed_persona(user_id, nombre_asistente="reference implementation")

    llamadas: list[dict[str, Any]] = []

    async def fake_run_gym_checkin(ctx, save_run):
        llamadas.append({"ctx": ctx, "save_run": save_run})
        await save_run("done", {"enviados": 1, "fallidos": 0})

    monkeypatch.setattr(
        run_gym_checkin_module, "run_gym_checkin", fake_run_gym_checkin
    )
    deps = make_deps(session_factory=_session_factory(session))

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    assert len(llamadas) == 1
    ctx = llamadas[0]["ctx"]
    assert ctx.tenant_id == tenant_id
    assert ctx.user_id == user_id
    assert ctx.extras["deps"] is deps

    [run_row] = session.automation_runs.values()
    assert run_row["status"] == "done"
    assert run_row["detalle"] == {"enviados": 1, "fallidos": 0}


async def test_run_automation_gym_checkin_no_corre_el_turno_de_agente(
    monkeypatch: pytest.MonkeyPatch, _fake_edecan_automations
) -> None:
    runner_calls = _fake_edecan_automations
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session.seed_automation(
        automation_id, tenant_id, user_id=str(user_id),
        accion=json.dumps({"kind": "gym_checkin"}),
    )
    session.seed_tenant(tenant_id, "hosted_pro")
    session.seed_persona(user_id)

    async def fake_run_gym_checkin(ctx, save_run):
        await save_run("done", {})

    monkeypatch.setattr(run_gym_checkin_module, "run_gym_checkin", fake_run_gym_checkin)
    deps = make_deps(session_factory=_session_factory(session))

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    assert runner_calls == []


# ---------------------------------------------------------------------------
# Handler `run_gym_checkin`: card + push con category
# ---------------------------------------------------------------------------


async def test_run_gym_checkin_encola_wake_con_card_y_push_en_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_repo = FakeRepo()
    capture = install_companion_wake_capture(monkeypatch)
    deps = make_deps()
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ctx = types.SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, extras={"deps": deps, "flags": {}}
    )
    saved: list[tuple[str, dict[str, Any]]] = []

    async def fake_save_run(status, detalle):
        saved.append((status, detalle))

    monkeypatch.setattr(run_gym_checkin_module, "SqlRepo", lambda session: fake_repo)

    await run_gym_checkin_module.run_gym_checkin(ctx, fake_save_run)

    assert fake_repo.messages == []
    assert len(capture.companion_wakes()) == 1
    wake = capture.companion_wakes()[0]
    assert wake["job_type"] == RUN_COMPANION_TURN_JOB
    payload = wake["payload"]
    assert payload["require_message"] is True
    assert payload["source"] == "gym_checkin"
    assert payload["wake_key"].startswith("gym_checkin:")

    [card] = payload["message_presentation"]
    assert card["type"] == "gym_checkin"
    assert card["titulo"] == "Check-in de gym"
    assert card["botones"] == [
        {"label": "Sí", "accion": "gym_yes"},
        {"label": "No", "accion": "gym_no"},
    ]
    [tool] = payload["message_tool_calls"]
    assert tool["blocks_version"] == 1
    assert tool["blocks"][0]["type"] == "gym_checkin"

    push = payload["push"]
    assert push["title"] == "Edecán"
    assert push["category"] == "GYM_CHECKIN"
    assert push["data"]["route"] == "activity"
    assert "chat_id" in push["data"]

    assert saved == [("done", {"wake_key": payload["wake_key"], "encolado": True})]


async def test_run_gym_checkin_falla_si_no_resuelve_conversacion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deps = make_deps()
    install_companion_wake_capture(monkeypatch)
    tenant_id, user_id = uuid.uuid4(), uuid.uuid4()
    ctx = types.SimpleNamespace(
        tenant_id=tenant_id, user_id=user_id, extras={"deps": deps, "flags": {}}
    )
    saved: list[tuple[str, dict[str, Any]]] = []

    async def fake_save_run(status, detalle):
        saved.append((status, detalle))

    class _RepoRoto:
        async def resolve_main_conversation(self, **kwargs):
            raise RuntimeError("base caída")

    monkeypatch.setattr(run_gym_checkin_module, "SqlRepo", lambda session: _RepoRoto())

    await run_gym_checkin_module.run_gym_checkin(ctx, fake_save_run)

    assert saved == [("error", {"error": "no se pudo encolar gym_checkin"})]