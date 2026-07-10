"""Barrido v7 (WP-V7-06): durabilidad de `agent_missions`/`agent_steps`
(`run_mission.py`), `automation_runs` (`run_automation.py`) y del
`next_run_at` que `automation_scan.py` ya adelantaba correctamente ANTES de
encolar (regresión directa, ver más abajo) — ver
`docs/cumplimiento/barrido-v7-mediastreams-worker.md`.

Mismo patrón de bug que `HOTFIXES_PENDIENTES.md` puntos 8/9 y
`premium/edecan_premium/campaigns.py::handle` (WP-V6-03), aplicado acá a las
transiciones de estado de misiones/automatizaciones: antes de este WP,
`run_mission.py`/`run_automation.py` mantenían TODO (carga, planificación,
Y la ejecución completa del turno/misión, con sus posibles tool calls de
efecto externo real) dentro de UNA sola sesión larga sin comitear nada hasta
el final — un fallo de infraestructura a mitad de camino (el worker matado,
una `asyncio.CancelledError` real; `edecan_automations.runner.run_automation`
docstring ya lo anticipa: "un run que se cuelga o que el worker mata a mitad
de camino") se llevaba puesta en el rollback la evidencia de pasos que YA
habían corrido (con posibles efectos externos reales ya ejecutados), y el
reintento del despachador SQS los repetía desde cero.

`test_automation_handlers.py`/`test_run_mission_handler.py` (existentes, sin
tocar) usan un `FakeSession` compartido SIN semántica transaccional (una
única instancia que muta un dict directo — ver sus propios docstrings) que
no puede demostrar esta clase de bug/fix en absoluto (no hay rollback que
observar). Este archivo, nuevo y autocontenido (mismo criterio que
`premium/tests/test_v6_evidencia.py`/`apps/worker/tests/test_v6_evidencia.py`),
usa un `_FakeDb`/`_TxFakeSession`/`_tx_session_factory` que SÍ simulan
commit-al-salir-limpio (fusión clave-por-clave sobre `_FakeDb`, ver el
comentario junto a `_TxFakeSession`) / descarte-al-propagar-una-excepción —
el único tipo de fake capaz de probar que un checkpoint sobrevive
independientemente de lo que le pase al resto del job.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import edecan_worker.handlers.automation_scan as automation_scan_module
import edecan_worker.handlers.run_automation as run_automation_module
import edecan_worker.handlers.run_mission as run_mission_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps

_LLM_ACCOUNT_ID = uuid.uuid4()


class _FakeLLMVault:
    """`.get()` siempre devuelve un `TokenBundle` válido — mismo criterio que
    `test_run_mission_handler.py`/`test_automation_handlers.py`: este archivo
    no prueba la resolución bring-your-own en sí (ya cubierta en
    `test_llm_por_tenant.py`), solo necesita que no interrumpa el camino
    bajo prueba."""

    async def get(self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID) -> Any:
        if connector_account_id != _LLM_ACCOUNT_ID:
            return None

        class _Bundle:
            access_token = json.dumps({"kind": "anthropic", "api_key": "sk-ant-fake"})

        return _Bundle()


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


# ---------------------------------------------------------------------------
# `_FakeDb` / `_TxFakeSession` / `_tx_session_factory` — ver docstring del
# módulo. Entiende el mismo SQL (por prefijo + subcadena) que los
# `FakeSession` de `test_run_mission_handler.py`/`test_automation_handlers.py`
# para `agent_missions`/`agent_steps`/`automations`/`automation_runs`/
# `tenants`/`personas`/`connector_accounts`, pero con semántica transaccional
# real vía un modelo de "escritura en un buffer local, aplicado SOLO al
# comitear" (nunca "snapshot global + restaurar al fallar" — ESE modelo más
# simple se probó INCORRECTO durante el desarrollo de este archivo: si una
# sesión B abre, escribe y comitea DESPUÉS de que una sesión A ya abrió pero
# ANTES de que A cierre, y A LUEGO hace rollback, "restaurar el snapshot de
# A" pisaría por error el commit YA independiente de B, que en Postgres real
# vive en una transacción totalmente aparte — justo el escenario que
# `_make_save_step` produce: una sesión corta e independiente que abre y
# comitea DENTRO de la ventana de vida de la sesión larga del turno).
#
# Modelo correcto: cada sesión toma su PROPIA copia profunda de `_FakeDb` al
# entrar (ve todo lo comiteado hasta ese instante, nunca lo que otra sesión
# todavía no comiteó); todas sus lecturas/escrituras van contra esa copia
# local; si `__aexit__` sale limpio, la copia se fusiona de vuelta sobre el
# `_FakeDb` COMPARTIDO (commit); si sale con una excepción, la copia se
# descarta sin tocar el compartido (rollback) — nunca puede pisar lo que
# OTRA sesión, ya cerrada, comiteó de forma independiente mientras tanto.
# ---------------------------------------------------------------------------


class _FakeDb:
    def __init__(self) -> None:
        self.missions: dict[str, dict[str, Any]] = {}
        self.steps: dict[tuple[str, int], dict[str, Any]] = {}
        self.automations: dict[str, dict[str, Any]] = {}
        self.automation_runs: dict[str, dict[str, Any]] = {}
        self.tenants: dict[str, dict[str, Any]] = {}
        self.personas: dict[str, dict[str, Any]] = {}

    def seed_mission(self, mission_id: uuid.UUID, tenant_id: uuid.UUID, **fields: Any) -> None:
        row = {
            "id": str(mission_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "objetivo": "Objetivo de prueba",
            "status": "planning",
            "plan": None,
            "resultado": None,
            "presupuesto": {"max_steps": 8},
            "error": None,
        }
        row.update(fields)
        self.missions[str(mission_id)] = row

    def seed_step(
        self, mission_id: uuid.UUID, tenant_id: uuid.UUID, seq: int, **fields: Any
    ) -> None:
        row = {
            "tenant_id": str(tenant_id),
            "mission_id": str(mission_id),
            "seq": seq,
            "agente": "research",
            "instruccion": f"paso {seq}",
            "status": "pending",
            "resultado": None,
            "usage": None,
        }
        row.update(fields)
        self.steps[(str(mission_id), seq)] = row

    def seed_automation(
        self, automation_id: uuid.UUID, tenant_id: uuid.UUID, **fields: Any
    ) -> None:
        row = {
            "id": str(automation_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "nombre": "prueba",
            "descripcion": "",
            "trigger": json.dumps({"kind": "webhook"}),
            "accion": json.dumps({"kind": "agent_instruction", "instruccion": "Haz algo."}),
            "enabled": True,
            "next_run_at": None,
            "last_run_at": None,
        }
        row.update(fields)
        self.automations[str(automation_id)] = row

    def seed_tenant(self, tenant_id: uuid.UUID, plan_key: str) -> None:
        self.tenants[str(tenant_id)] = {"plan_key": plan_key}


class _TxFakeSession:
    """Lee A TRAVÉS de `shared` (prioriza lo que ESTA MISMA sesión ya
    escribió, todavía sin comitear, sobre lo que YA está comiteado en
    `shared`) y escribe a un buffer LOCAL (`_pending`) que solo se aplica a
    `shared`, clave por clave (nunca el dict entero), si `__aexit__` sale
    limpio — ver el comentario junto a `_FakeDb` para el porqué de
    "clave por clave" en vez de "snapshot completo": una sesión ANIDADA que
    abre y comitea DENTRO de la ventana de vida de esta sesión (p. ej. el
    `save_step` independiente de `run_mission.py`) debe sobrevivir intacta
    aunque ESTA sesión también comitee después sin haber vuelto a leer esa
    clave."""

    def __init__(self, shared: _FakeDb) -> None:
        self.shared = shared
        self._pending = _FakeDb()
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def _get(self, attr: str, key: Any) -> dict[str, Any] | None:
        pending = getattr(self._pending, attr)
        if key in pending:
            return pending[key]
        return getattr(self.shared, attr).get(key)

    def _put(self, attr: str, key: Any, row: dict[str, Any]) -> None:
        getattr(self._pending, attr)[key] = row

    def _merged_items(self, attr: str) -> list[tuple[Any, dict[str, Any]]]:
        merged = dict(getattr(self.shared, attr))
        merged.update(getattr(self._pending, attr))
        return list(merged.items())

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM agent_missions" in sql:
            row = self._get("missions", params["id"])
            return _FakeResult([row] if row and row["tenant_id"] == params["tenant_id"] else [])
        if primer_token == "SELECT" and "FROM tenants" in sql:
            row = self._get("tenants", params["id"])
            return _FakeResult([row] if row else [])
        if primer_token == "SELECT" and "FROM connector_accounts" in sql:
            return _FakeResult([{"id": _LLM_ACCOUNT_ID}])
        if primer_token == "SELECT" and "FROM agent_steps" in sql and "seq = :seq" in sql:
            row = self._get("steps", (params["mission_id"], params["seq"]))
            return _FakeResult([row] if row and row["tenant_id"] == params["tenant_id"] else [])
        if primer_token == "SELECT" and "FROM agent_steps" in sql:
            rows = [
                row
                for (mid, _seq), row in sorted(self._merged_items("steps"), key=lambda kv: kv[0][1])
                if mid == params["mission_id"] and row["tenant_id"] == params["tenant_id"]
            ]
            return _FakeResult(rows)
        if primer_token == "INSERT" and "agent_steps" in sql:
            key = (params["mission_id"], params["seq"])
            self._put(
                "steps",
                key,
                {
                    "tenant_id": params["tenant_id"],
                    "mission_id": params["mission_id"],
                    "seq": params["seq"],
                    "agente": params["agente"],
                    "instruccion": params["instruccion"],
                    "status": "pending",
                    "resultado": None,
                    "usage": json.loads(params["usage"]) if params.get("usage") else None,
                },
            )
            return _FakeResult()
        if primer_token == "UPDATE" and "agent_missions" in sql:
            row = self._get("missions", params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                row = dict(row)
                for campo in ("status", "resultado", "error"):
                    if campo in params:
                        row[campo] = params[campo]
                if "plan" in params:
                    row["plan"] = json.loads(params["plan"])
                if "presupuesto" in params:
                    row["presupuesto"] = json.loads(params["presupuesto"])
                self._put("missions", params["id"], row)
            return _FakeResult()
        if primer_token == "UPDATE" and "agent_steps" in sql:
            key = (params["mission_id"], params["seq"])
            row = self._get("steps", key)
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                row = dict(row)
                if "status" in params:
                    row["status"] = params["status"]
                if "resultado" in params:
                    row["resultado"] = params["resultado"]
                if "usage" in params:
                    row["usage"] = json.loads(params["usage"])
                self._put("steps", key, row)
            return _FakeResult()

        if primer_token == "SELECT" and "FROM automations" in sql and "next_run_at <=" in sql:
            # `automation_scan.py::_list_due_schedule_automations` — barrido
            # GLOBAL (sin tenant_id, sin `params["id"]`) — DEBE evaluarse
            # antes que el `SELECT ... FROM automations` por-id de abajo,
            # que sí espera `params["id"]`.
            ahora = params["now"]
            rows = [
                row
                for _id, row in self._merged_items("automations")
                if row["enabled"]
                and row.get("next_run_at") is not None
                and row["next_run_at"] <= ahora
            ]
            rows.sort(key=lambda r: r["next_run_at"])
            return _FakeResult(rows)
        if primer_token == "SELECT" and "FROM automations" in sql:
            row = self._get("automations", params["id"])
            return _FakeResult([row] if row and row["tenant_id"] == params["tenant_id"] else [])
        if primer_token == "SELECT" and "FROM personas" in sql:
            row = self._get("personas", params["user_id"])
            return _FakeResult([row] if row else [])
        if primer_token == "INSERT" and "automation_runs" in sql:
            self._put(
                "automation_runs",
                params["id"],
                {
                    "id": params["id"],
                    "tenant_id": params["tenant_id"],
                    "automation_id": params["automation_id"],
                    "status": "running",
                    "detalle": json.loads(params["detalle"]),
                },
            )
            return _FakeResult()
        if primer_token == "UPDATE" and "automation_runs" in sql:
            row = self._get("automation_runs", params["id"])
            if row is not None:
                row = dict(row)
                row["status"] = params["status"]
                row["detalle"] = json.loads(params["detalle"])
                self._put("automation_runs", params["id"], row)
            return _FakeResult()
        if primer_token == "UPDATE" and "automations" in sql and "last_run_at" in sql:
            row = self._get("automations", params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                row = dict(row)
                row["last_run_at"] = "touched"
                self._put("automations", params["id"], row)
            return _FakeResult()
        if primer_token == "UPDATE" and "automations" in sql and "next_run_at" in sql:
            # `automation_scan.py::_advance_next_run` — sin filtro de
            # tenant_id en el WHERE (barrido global, ver docstring de ese
            # módulo), igual que el SQL real.
            row = self._get("automations", params["id"])
            if row is not None:
                row = dict(row)
                row["next_run_at"] = params["next_run_at"]
                self._put("automations", params["id"], row)
            return _FakeResult()

        raise AssertionError(f"query inesperada en el fake transaccional: {sql}")

    def _commit_into(self, shared: _FakeDb) -> None:
        """Aplica `self._pending` sobre `shared`, CLAVE POR CLAVE (nunca
        `dict.clear()` + `dict.update()` de un dict entero) — así una
        sesión anidada que ya comitió mientras ésta seguía abierta nunca se
        pierde solo porque ésta también comitea después sin haber vuelto a
        leerla."""
        for attr in ("missions", "steps", "automations", "automation_runs", "tenants", "personas"):
            shared_dict = getattr(shared, attr)
            for key, row in getattr(self._pending, attr).items():
                shared_dict[key] = row


def _tx_session_factory(shared: _FakeDb) -> Any:
    @asynccontextmanager
    async def _factory(tenant_id: uuid.UUID | None):
        session = _TxFakeSession(shared)
        try:
            yield session
        except BaseException:
            raise  # se descarta `session._pending` tal cual, `shared` queda intacto.
        else:
            session._commit_into(shared)

    return _factory


def _make_deps(db: _FakeDb) -> Any:
    return make_deps(session_factory=_tx_session_factory(db), vault=lambda s: _FakeLLMVault())


# ---------------------------------------------------------------------------
# run_mission.py — Orchestrator/Mission fakeados vía sys.modules (mismo
# patrón, mínimo, que test_run_mission_handler.py).
# ---------------------------------------------------------------------------


class _FakeOrchestrator:
    plan_calls: list[dict[str, Any]] = []
    run_side_effect: Any = None
    plan_response: list[dict[str, Any]] = []

    def __init__(self, llm_router: Any, registry: Any) -> None:
        pass

    async def plan(
        self, objetivo: str, flags: dict[str, Any], settings: Any
    ) -> list[dict[str, Any]]:
        _FakeOrchestrator.plan_calls.append({"objetivo": objetivo})
        return [dict(p) for p in _FakeOrchestrator.plan_response]

    async def run(self, mission: Any, deps: Any) -> None:
        if _FakeOrchestrator.run_side_effect is not None:
            await _FakeOrchestrator.run_side_effect(mission, deps)


class _FakeMission:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


@pytest.fixture
def fake_orchestrator(monkeypatch: pytest.MonkeyPatch):
    _FakeOrchestrator.plan_calls = []
    _FakeOrchestrator.run_side_effect = None
    _FakeOrchestrator.plan_response = [{"seq": 1, "agente": "research", "instruccion": "x"}]

    fake_module = types.ModuleType("edecan_agents")
    fake_module.Orchestrator = _FakeOrchestrator  # type: ignore[attr-defined]
    fake_module.Mission = _FakeMission  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_agents", fake_module)
    monkeypatch.setattr(run_mission_module, "_build_registry", lambda: object())
    return _FakeOrchestrator


def _mission_envelope(mission_id: uuid.UUID, tenant_id: uuid.UUID, **payload: Any) -> JobEnvelope:
    body = {"mission_id": str(mission_id)}
    body.update(payload)
    return JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="run_mission", payload=body)


async def test_run_mission_paso_ya_terminado_sobrevive_a_un_fallo_posterior_del_turno(
    fake_orchestrator,
) -> None:
    """## Durabilidad por paso (docstring de `run_mission.py`, WP-V7-06):
    el paso 1 termina 'done' (`save_step`) y LUEGO algo catastrófico revienta
    dentro de `orchestrator.run` (aquí simulado: representa un `BaseException`
    genuino escapando de toda la resiliencia interna del `Orchestrator` real
    — worker matado, `CancelledError` real, etc., ver docstring del módulo).
    Antes de este fix, esa excepción se propagaba fuera de la ÚNICA sesión
    larga que compartía TODO `handle()`, y con una sesión real (Postgres) el
    rollback se habría llevado puesto el 'done' del paso 1 -- pese a que, si
    el paso 1 llamó una tool con efecto externo real, ese efecto YA había
    ocurrido de verdad. Con el fix, `save_step` comitea en su PROPIA sesión
    independiente -- el 'done' del paso 1 debe sobrevivir intacto en `db`
    aunque `handle()` termine propagando la excepción."""
    db = _FakeDb()
    mission_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    db.seed_mission(mission_id, tenant_id, presupuesto={"max_steps": 5})
    _FakeOrchestrator.plan_response = [
        {"seq": 1, "agente": "research", "instruccion": "uno"},
        {"seq": 2, "agente": "research", "instruccion": "dos"},
    ]

    async def _side_effect(mission: Any, run_deps: Any) -> None:
        await run_deps.save_step(seq=1, status="done", resultado="listo", usage=None)
        raise RuntimeError("el worker murió a mitad de la ola 2 (simulado)")

    _FakeOrchestrator.run_side_effect = _side_effect
    deps = _make_deps(db)

    with pytest.raises(RuntimeError, match="murió a mitad"):
        await run_mission_module.handle(_mission_envelope(mission_id, tenant_id), deps)

    # el plan inicial Y el paso 1 'done' sobreviven, pese a que `handle()`
    # terminó propagando la excepción -- la prueba directa del fix.
    assert db.steps[(str(mission_id), 1)]["status"] == "done"
    assert db.steps[(str(mission_id), 1)]["resultado"] == "listo"
    assert db.steps[(str(mission_id), 2)]["status"] == "pending"
    assert db.missions[str(mission_id)]["status"] == "running"


async def test_run_mission_reintento_no_replanifica_si_ya_hay_pasos_persistidos(
    fake_orchestrator,
) -> None:
    """Reanudación implícita (docstring de `run_mission.py`): tras el
    escenario del test anterior (el plan ya quedó comiteado), un reintento
    del despachador (mismo `mission_id`, `resume=False` -- el payload
    original, sin `resume`) NO debe volver a llamar `orchestrator.plan()` ni
    duplicar filas de `agent_steps` -- debe reusar el plan existente."""
    db = _FakeDb()
    mission_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    db.seed_mission(mission_id, tenant_id, status="running")
    # simula el estado que deja el fix tras un intento previo interrumpido:
    # el plan quedó comiteado, el paso 1 alcanzó a terminar, el 2 nunca corrió.
    db.seed_step(mission_id, tenant_id, seq=1, status="done", resultado="listo")
    db.seed_step(mission_id, tenant_id, seq=2, status="pending")
    deps = _make_deps(db)

    await run_mission_module.handle(_mission_envelope(mission_id, tenant_id), deps)

    assert _FakeOrchestrator.plan_calls == []  # nunca replanificó
    assert len(db.steps) == 2  # ninguna fila nueva/duplicada
    assert db.steps[(str(mission_id), 1)]["status"] == "done"  # paso ya hecho, intacto


# ---------------------------------------------------------------------------
# run_automation.py — edecan_automations.runner fakeado vía sys.modules
# (mismo patrón, mínimo, que test_automation_handlers.py).
# ---------------------------------------------------------------------------


class _FakeRunnerDeps:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeAutomationRunner:
    side_effect: Any = None

    @staticmethod
    async def run_automation(automation: dict[str, Any], deps: _FakeRunnerDeps) -> None:
        if _FakeAutomationRunner.side_effect is not None:
            await _FakeAutomationRunner.side_effect(automation, deps)


@pytest.fixture
def fake_automation_runner(monkeypatch: pytest.MonkeyPatch):
    _FakeAutomationRunner.side_effect = None

    fake_runner_module = types.ModuleType("edecan_automations.runner")
    fake_runner_module.RunnerDeps = _FakeRunnerDeps  # type: ignore[attr-defined]
    fake_runner_module.run_automation = _FakeAutomationRunner.run_automation  # type: ignore[attr-defined]
    fake_package = types.ModuleType("edecan_automations")
    fake_package.runner = fake_runner_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.runner", fake_runner_module)
    monkeypatch.setattr(run_automation_module, "_build_registry", lambda: object())
    return _FakeAutomationRunner


def _automation_envelope(automation_id: uuid.UUID, tenant_id: uuid.UUID) -> JobEnvelope:
    return JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_automation",
        payload={"automation_id": str(automation_id)},
    )


async def test_run_automation_running_marker_sobrevive_a_un_fallo_posterior_del_turno(
    fake_automation_runner,
) -> None:
    """## Evidencia de que el run arrancó (docstring de `run_automation.py`,
    WP-V7-06): `run_automation_turn` arranca (representa que al menos una
    tool con efecto externo real ya pudo haber corrido) y LUEGO algo de
    infraestructura revienta (aquí simulado). Antes del fix, la fila
    `automation_runs` compartía la sesión larga del turno -- el rollback se
    la habría llevado puesta entera (evidencia de que el intento existió,
    perdida). Con el fix, la fila 'running' ya comiteó en su PROPIA sesión
    ANTES de invocar el turno -- debe sobrevivir intacta."""
    db = _FakeDb()
    automation_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    db.seed_automation(automation_id, tenant_id)

    async def _side_effect(automation: dict[str, Any], run_deps: Any) -> None:
        raise RuntimeError("el worker murió a mitad del turno (simulado)")

    _FakeAutomationRunner.side_effect = _side_effect
    deps = _make_deps(db)

    with pytest.raises(RuntimeError, match="murió a mitad"):
        await run_automation_module.handle(_automation_envelope(automation_id, tenant_id), deps)

    [run_row] = db.automation_runs.values()
    assert run_row["status"] == "running"
    assert run_row["automation_id"] == str(automation_id)


async def test_run_automation_save_run_terminal_es_independiente_de_la_sesion_del_turno(
    fake_automation_runner,
) -> None:
    """El UPDATE terminal (`save_run`) también comitea en su propia sesión —
    confirma que sigue llegando a `automation_runs`/`automations.last_run_at`
    en el camino feliz (sin regresión de comportamiento observable)."""
    db = _FakeDb()
    automation_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    db.seed_automation(automation_id, tenant_id)

    async def _side_effect(automation: dict[str, Any], run_deps: Any) -> None:
        await run_deps.save_run("done", {"resultado": "listo"})

    _FakeAutomationRunner.side_effect = _side_effect
    deps = _make_deps(db)

    await run_automation_module.handle(_automation_envelope(automation_id, tenant_id), deps)

    [run_row] = db.automation_runs.values()
    assert run_row["status"] == "done"
    assert run_row["detalle"] == {"resultado": "listo"}
    assert db.automations[str(automation_id)]["last_run_at"] == "touched"


# ---------------------------------------------------------------------------
# automation_scan.py — regresión directa: `_advance_next_run` YA comiteaba
# en su propia sesión corta ANTES de `enqueue()` (código correcto, sin
# cambios de este WP) — este test confirma con el fake TRANSACCIONAL que un
# `enqueue()` fallido no revierte ese avance (mismo patrón de regresión que
# `premium/tests/test_campaigns.py::
# test_handle_falla_reencolado_no_deshace_los_ya_enviados`, WP-V6-03).
# ---------------------------------------------------------------------------


def _install_fake_edecan_automations_engine(
    monkeypatch: pytest.MonkeyPatch, compute_next_run: Any
) -> None:
    fake_engine_module = types.ModuleType("edecan_automations.engine")
    fake_engine_module.compute_next_run = compute_next_run  # type: ignore[attr-defined]
    fake_package = sys.modules.get("edecan_automations") or types.ModuleType("edecan_automations")
    fake_package.engine = fake_engine_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.engine", fake_engine_module)


def _install_fake_enqueue(monkeypatch: pytest.MonkeyPatch, enqueue_fn: Any) -> None:
    fake_queue_module = types.ModuleType("edecan_core.queue")
    fake_queue_module.enqueue = enqueue_fn  # type: ignore[attr-defined]
    fake_core_module = sys.modules.get("edecan_core") or types.ModuleType("edecan_core")
    fake_core_module.queue = fake_queue_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_core", fake_core_module)
    monkeypatch.setitem(sys.modules, "edecan_core.queue", fake_queue_module)


async def test_automation_scan_falla_el_enqueue_no_revierte_el_next_run_at_ya_avanzado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _FakeDb()
    automation_id, tenant_id = uuid.uuid4(), uuid.uuid4()
    ahora = datetime.now(UTC)
    proxima = ahora + timedelta(days=1)
    db.seed_automation(
        automation_id,
        tenant_id,
        next_run_at=ahora - timedelta(minutes=5),
        trigger=json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY"}),
    )
    _install_fake_edecan_automations_engine(monkeypatch, lambda rrule, after, anchor=None: proxima)

    async def _enqueue_que_falla(settings, job_type, payload, tid):
        raise RuntimeError("blip de red al encolar (simulado)")

    _install_fake_enqueue(monkeypatch, _enqueue_que_falla)
    deps = _make_deps(db)

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    with pytest.raises(RuntimeError, match="blip de red"):
        await automation_scan_module.handle(env, deps)

    # `next_run_at` YA había comiteado en su propia sesión corta (dentro del
    # `async with` que `_advance_next_run` cierra ANTES de `enqueue()`, ver
    # `automation_scan.py`) -- sobrevive intacto pese a que `enqueue()`
    # lanzó justo después, fuera de cualquier sesión abierta.
    assert db.automations[str(automation_id)]["next_run_at"] == proxima
