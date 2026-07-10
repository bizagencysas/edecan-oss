"""Job `run_mission` (`ROADMAP_V2.md` §7.3, §7.4, §7.6, §7.9).

`edecan_agents` se fakea vía monkeypatch de import (inyectando un módulo
falso en `sys.modules`, mismo patrón que `test_run_campaign_step.py` con
`edecan_premium`): el `Orchestrator`/`Mission` reales ya tienen su propia
suite exhaustiva en `packages/agents/tests/`, así que aquí solo importa
verificar que el HANDLER (a) filtra siempre por `tenant_id`, (b) carga/
persiste `agent_missions`/`agent_steps` con el SQL correcto, (c) arma
`Mission`/`RunDeps` con los datos correctos, y (d) el flujo de reanudación
resetea el paso pendiente y aprueba solo su `tool_call_id` guardado.

`FakeSession` es un almacén en memoria de `agent_missions`/`agent_steps`/
`tenants` que entiende (por prefijo + subcadena) el SQL que emite
`run_mission.py` — igual de espíritu que `_FakeSession` en
`premium/tests/test_campaigns.py`, pero con más forma de tabla porque este
handler hace más operaciones distintas.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from contextlib import asynccontextmanager
from typing import Any

import edecan_worker.handlers.run_mission as run_mission_module
import pytest
from edecan_schemas import JobEnvelope
from fakes import make_deps

# `Deps.llm_router_for` (bring-your-own, WP-V3-02) ahora LANZA
# `TenantLLMNotConnectedError` en vez de degradar a la plataforma cuando no
# puede resolver un proveedor propio del tenant (ver
# `apps/worker/tests/test_llm_por_tenant.py`, que cubre esa resolución en
# detalle). Este archivo no prueba esa resolución en sí — solo la
# orquestación de misiones (`Orchestrator` está fakeado por completo, ver
# `FakeOrchestrator` abajo) — así que `FakeSession`/`_FakeLLMVault` simulan
# un tenant que YA conectó un proveedor LLM válido, para que las pruebas de
# "camino feliz" lleguen a ejercitar la orquestación sin que la resolución
# bring-your-own se interponga. Un UUID fijo (no `uuid.uuid4()` por prueba)
# porque `execute()` no varía la respuesta por tenant.
_LLM_ACCOUNT_ID = uuid.uuid4()


class _FakeLLMVault:
    """`.get()` devuelve siempre un `TokenBundle` válido para
    `_LLM_ACCOUNT_ID` — ver el comentario de arriba. `FakeOrchestrator` (más
    abajo) está fakeado por completo, así que el `LLMRouter` real que
    termine construyendo `Deps.llm_router_for` con esta config nunca se usa
    para una llamada de verdad; solo importa que la resolución no lance."""

    async def get(self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID) -> Any:
        if connector_account_id != _LLM_ACCOUNT_ID:
            return None

        class _Bundle:
            access_token = json.dumps({"kind": "anthropic", "api_key": "sk-ant-fake-de-prueba"})

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


class FakeSession:
    def __init__(self) -> None:
        self.missions: dict[str, dict[str, Any]] = {}
        self.steps: dict[tuple[str, int], dict[str, Any]] = {}
        self.tenants: dict[str, dict[str, Any]] = {}
        self.executed: list[tuple[str, dict[str, Any]]] = []

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

    def seed_tenant(self, tenant_id: uuid.UUID, plan_key: str) -> None:
        self.tenants[str(tenant_id)] = {"plan_key": plan_key}

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM agent_missions" in sql:
            row = self.missions.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                return _FakeResult(rows=[row])
            return _FakeResult(rows=[])

        if primer_token == "SELECT" and "FROM tenants" in sql:
            row = self.tenants.get(params["id"])
            return _FakeResult(rows=[row] if row is not None else [])

        if (
            primer_token == "SELECT"
            and "FROM connector_accounts" in sql
            and "external_account_id" in sql
        ):
            # `Deps._build_mcp_tools` (connector_key="mcp") -- distinto de la
            # rama de abajo (resolución LLM, que solo selecciona `id`). Este
            # archivo no simula tenants con conectores MCP: lista vacía, así
            # `mcp_tools_para` sigue su camino normal de "sin tools" sin
            # pasar por el `except Exception` de `_build_mcp_tools` (que
            # generaría un `KeyError: 'external_account_id'` con la rama de
            # abajo, ya que esa fila solo trae `id`).
            return _FakeResult(rows=[])

        if primer_token == "SELECT" and "FROM connector_accounts" in sql:
            # Ver el comentario junto a `_LLM_ACCOUNT_ID`: este archivo
            # simula un tenant que ya conectó su LLM propio, siempre.
            return _FakeResult(rows=[{"id": _LLM_ACCOUNT_ID}])

        if primer_token == "SELECT" and "FROM agent_steps" in sql and "seq = :seq" in sql:
            row = self.steps.get((params["mission_id"], params["seq"]))
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                return _FakeResult(rows=[row])
            return _FakeResult(rows=[])

        if primer_token == "SELECT" and "FROM agent_steps" in sql:
            rows = [
                row
                for (mission_id, _seq), row in sorted(self.steps.items(), key=lambda kv: kv[0][1])
                if mission_id == params["mission_id"] and row["tenant_id"] == params["tenant_id"]
            ]
            return _FakeResult(rows=rows)

        if primer_token == "INSERT" and "agent_steps" in sql:
            key = (params["mission_id"], params["seq"])
            self.steps[key] = {
                "tenant_id": params["tenant_id"],
                "mission_id": params["mission_id"],
                "seq": params["seq"],
                "agente": params["agente"],
                "instruccion": params["instruccion"],
                "status": "pending",
                "resultado": None,
                # WP-V5-05: `_insert_steps` esconde `depende_de` dentro de
                # `usage` (`agent_steps` no tiene columna propia, ver
                # `run_mission.py::_paso_con_depende_de`) — el fake replica
                # el mismo `json.loads(params["usage"])` que ya hace el
                # UPDATE de más abajo.
                "usage": json.loads(params["usage"]) if params.get("usage") else None,
            }
            return _FakeResult()

        if primer_token == "UPDATE" and "agent_missions" in sql:
            row = self.missions.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                if "status" in params:
                    row["status"] = params["status"]
                if "plan" in params:
                    row["plan"] = json.loads(params["plan"])
                if "resultado" in params:
                    row["resultado"] = params["resultado"]
                if "error" in params:
                    row["error"] = params["error"]
                if "presupuesto" in params:
                    row["presupuesto"] = json.loads(params["presupuesto"])
            return _FakeResult()

        if primer_token == "UPDATE" and "agent_steps" in sql:
            key = (params["mission_id"], params["seq"])
            row = self.steps.get(key)
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                if "status" in params:
                    row["status"] = params["status"]
                if "resultado" in params:
                    row["resultado"] = params["resultado"]
                if "usage" in params:
                    row["usage"] = json.loads(params["usage"])
            return _FakeResult()

        raise AssertionError(f"query inesperada en el fake: {sql}")


def _session_factory(session: FakeSession):
    @asynccontextmanager
    async def _factory(tenant_id):
        yield session

    return _factory


class FakeOrchestrator:
    """Instalado en el `edecan_agents` falso — cada instancia registra sus
    llamadas en las listas de clase compartidas `plan_calls`/`run_calls` (se
    resetean por test vía la fixture `fake_orchestrator`)."""

    plan_calls: list[dict[str, Any]] = []
    run_calls: list[dict[str, Any]] = []
    instances: list[FakeOrchestrator] = []
    plan_response: list[dict[str, Any]] = []
    run_side_effect: Any = None  # callable(mission, deps) opcional

    def __init__(self, llm_router: Any, registry: Any) -> None:
        self.llm_router = llm_router
        self.registry = registry
        FakeOrchestrator.instances.append(self)

    async def plan(
        self, objetivo: str, flags: dict[str, Any], settings: Any
    ) -> list[dict[str, Any]]:
        FakeOrchestrator.plan_calls.append(
            {"objetivo": objetivo, "flags": flags, "settings": settings}
        )
        return [dict(p) for p in FakeOrchestrator.plan_response]

    async def run(self, mission: Any, deps: Any) -> None:
        FakeOrchestrator.run_calls.append({"mission": mission, "deps": deps})
        if FakeOrchestrator.run_side_effect is not None:
            await FakeOrchestrator.run_side_effect(mission, deps)


class FakeMission:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


REGISTRY_SENTINEL = object()
"""Devuelto por el `_build_registry` monkeypatcheado (ver `fake_orchestrator`
abajo): `run_mission.py` construye un `edecan_core.tools.ToolRegistry` REAL
vía `load_entry_points(group="edecan.tools")`, que resolvería el entry point
`agents = "edecan_agents:get_all_tools"` (`packages/agents/pyproject.toml`)
contra el `edecan_agents` FALSO ya instalado en `sys.modules` — que no
define `get_all_tools` — y reventaría con `AttributeError` antes de llegar a
ejercitar nada de este handler. Monkeypatchear `_build_registry` aísla el
test de esa maquinaria real (entry points instalados en el venv actual) por
completo, además de más rápido/determinista."""


@pytest.fixture(autouse=True)
def fake_orchestrator(monkeypatch: pytest.MonkeyPatch):
    FakeOrchestrator.plan_calls = []
    FakeOrchestrator.run_calls = []
    FakeOrchestrator.instances = []
    FakeOrchestrator.plan_response = [{"seq": 1, "agente": "research", "instruccion": "Investiga"}]
    FakeOrchestrator.run_side_effect = None

    fake_module = types.ModuleType("edecan_agents")
    fake_module.Orchestrator = FakeOrchestrator  # type: ignore[attr-defined]
    fake_module.Mission = FakeMission  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_agents", fake_module)
    monkeypatch.setattr(run_mission_module, "_build_registry", lambda: REGISTRY_SENTINEL)
    return FakeOrchestrator


def _envelope(mission_id: uuid.UUID, tenant_id: uuid.UUID | None, **payload: Any) -> JobEnvelope:
    body = {"mission_id": str(mission_id)}
    body.update(payload)
    return JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="run_mission", payload=body)


# ---------------------------------------------------------------------------
# Casos borde
# ---------------------------------------------------------------------------


async def test_sin_tenant_id_lanza_value_error():
    session = FakeSession()
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())
    with pytest.raises(ValueError):
        await run_mission_module.handle(_envelope(uuid.uuid4(), None), deps)


async def test_mision_no_encontrada_no_hace_nada():
    session = FakeSession()
    tenant_id = uuid.uuid4()
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(uuid.uuid4(), tenant_id), deps)

    assert FakeOrchestrator.plan_calls == []
    assert FakeOrchestrator.run_calls == []


async def test_mision_de_otro_tenant_se_trata_como_no_encontrada():
    # Filtrado por tenant_id (ARCHITECTURE.md §2): el worker es "dueño" y
    # bypassa RLS, así que esta comprobación manual es la única barrera.
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_dueno = uuid.uuid4()
    tenant_atacante = uuid.uuid4()
    session.seed_mission(mission_id, tenant_dueno)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_atacante), deps)

    assert FakeOrchestrator.plan_calls == []
    assert session.missions[str(mission_id)]["status"] == "planning"  # sin tocar


@pytest.mark.parametrize("status_terminal", ["done", "error", "cancelled"])
async def test_mision_en_estado_terminal_se_ignora(status_terminal: str):
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status=status_terminal)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    assert FakeOrchestrator.plan_calls == []
    assert FakeOrchestrator.run_calls == []


# ---------------------------------------------------------------------------
# Misión nueva: planifica, persiste pasos, ejecuta
# ---------------------------------------------------------------------------


async def test_mision_nueva_planifica_persiste_pasos_y_ejecuta():
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(
        mission_id, tenant_id, objetivo="Investiga el mercado", presupuesto={"max_steps": 5}
    )
    session.seed_tenant(tenant_id, "hosted_pro")
    FakeOrchestrator.plan_response = [
        {"seq": 1, "agente": "research", "instruccion": "Busca datos"},
        {"seq": 2, "agente": "data_analyst", "instruccion": "Analiza los datos"},
    ]
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    # Orchestrator(llm_router, registry) se construyó con el llm_router
    # bring-your-own del tenant (resuelto vía `deps.llm_router_for`, NO
    # `deps.llm_router` de plataforma — este tenant sí tiene una config
    # propia válida, ver `_FakeLLMVault`/`_LLM_ACCOUNT_ID`) y el registry que
    # arma `_build_registry` (aquí monkeypatcheado a REGISTRY_SENTINEL, ver
    # docstring de esa constante).
    assert len(FakeOrchestrator.instances) == 1
    assert FakeOrchestrator.instances[0].llm_router is not None
    assert FakeOrchestrator.instances[0].llm_router is not deps.llm_router
    assert FakeOrchestrator.instances[0].registry is REGISTRY_SENTINEL

    # plan() se llamó con el objetivo real y los flags de hosted_pro (agents.missions=True).
    assert len(FakeOrchestrator.plan_calls) == 1
    plan_call = FakeOrchestrator.plan_calls[0]
    assert plan_call["objetivo"] == "Investiga el mercado"
    assert plan_call["flags"]["agents.missions"] is True

    # los 2 pasos quedaron persistidos en agent_steps.
    assert session.steps[(str(mission_id), 1)]["agente"] == "research"
    assert session.steps[(str(mission_id), 1)]["instruccion"] == "Busca datos"
    assert session.steps[(str(mission_id), 2)]["agente"] == "data_analyst"

    # la misión pasó a running con el plan guardado.
    assert session.missions[str(mission_id)]["status"] == "running"
    assert session.missions[str(mission_id)]["plan"] == FakeOrchestrator.plan_response

    # run() se llamó una vez, con una Mission armada desde la fila + pasos recién insertados.
    assert len(FakeOrchestrator.run_calls) == 1
    mission = FakeOrchestrator.run_calls[0]["mission"]
    assert mission.id == mission_id
    assert mission.tenant_id == tenant_id
    assert mission.objetivo == "Investiga el mercado"
    assert mission.presupuesto == {"max_steps": 5}
    assert mission.resume_step_seq is None
    assert mission.approved_tool_call_id is None
    assert [p["seq"] for p in mission.plan] == [1, 2]
    assert all(p["status"] == "pending" for p in mission.plan)


async def test_mision_nueva_sin_tenant_en_bd_usa_plan_free_selfhost():
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id)
    # nota: NO se llama session.seed_tenant(...) -> _load_tenant devuelve None.
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    plan_call = FakeOrchestrator.plan_calls[0]
    assert plan_call["flags"]["agents.missions"] is True  # free_selfhost también lo trae en True


async def test_run_deps_expone_session_settings_vault_y_flags(monkeypatch: pytest.MonkeyPatch):
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id)
    session.seed_tenant(tenant_id, "hosted_basic")

    # `_FakeLLMVault` (no un `object()` plano): `deps.vault(session)` es la
    # MISMA factory que usa `Deps.llm_router_for` para resolver el LLM
    # bring-your-own del tenant (ver comentario junto a `_LLM_ACCOUNT_ID`) Y
    # la que recibe `_RunDeps` — así que el objeto que se identity-checkea
    # abajo (`run_deps.vault is vault_sentinel`) también necesita un
    # `.get()` que funcione, o `handle()` lanzaría `TenantLLMNotConnectedError`
    # antes de llegar a construir `_RunDeps`.
    vault_sentinel = _FakeLLMVault()
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: vault_sentinel)

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    run_deps = FakeOrchestrator.run_calls[0]["deps"]
    assert run_deps.session is session
    assert run_deps.settings is deps.settings
    assert run_deps.vault is vault_sentinel
    assert run_deps.flags["agents.missions"] is True  # hosted_basic también lo trae en True


async def test_run_deps_save_step_y_save_mission_persisten_via_sql():
    """El `Orchestrator` (fake) invoca `deps.save_step`/`deps.save_mission`
    como lo haría el real — verifica que el handler los conecta a SQL de
    verdad, no a un stub que no persiste nada."""
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id)
    FakeOrchestrator.plan_response = [{"seq": 1, "agente": "research", "instruccion": "x"}]

    async def _side_effect(mission, deps):
        await deps.save_step(seq=1, status="done", resultado="listo", usage={"input_tokens": 3})
        await deps.save_mission(status="done", resultado="síntesis final", error=None)

    FakeOrchestrator.run_side_effect = _side_effect
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    assert session.steps[(str(mission_id), 1)]["status"] == "done"
    assert session.steps[(str(mission_id), 1)]["resultado"] == "listo"
    assert session.steps[(str(mission_id), 1)]["usage"] == {"input_tokens": 3}
    assert session.missions[str(mission_id)]["status"] == "done"
    assert session.missions[str(mission_id)]["resultado"] == "síntesis final"


async def test_run_deps_save_mission_persiste_presupuesto():
    """WP-V5-05: `RunDeps.save_mission(presupuesto=...)` (el `Orchestrator`
    real lo usa para el contador `replans_usados`) llega hasta
    `agent_missions.presupuesto` vía SQL real."""
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, presupuesto={"max_steps": 8})
    FakeOrchestrator.plan_response = [{"seq": 1, "agente": "research", "instruccion": "x"}]

    async def _side_effect(mission, deps):
        await deps.save_mission(presupuesto={"max_steps": 8, "replans_usados": 1})

    FakeOrchestrator.run_side_effect = _side_effect
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    assert session.missions[str(mission_id)]["presupuesto"] == {
        "max_steps": 8,
        "replans_usados": 1,
    }
    # `status` no se tocó (no se pasó -> "no lo toques", misma convención de
    # `_update_mission` de siempre).
    assert session.missions[str(mission_id)]["status"] == "running"


async def test_run_deps_insert_steps_persiste_filas_nuevas_via_sql():
    """WP-V5-05: `RunDeps.insert_steps` (el `Orchestrator` real lo usa tras
    un replan) crea filas `agent_steps` NUEVAS — a diferencia de
    `save_step`, que solo actualiza una fila EXISTENTE."""
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id)
    FakeOrchestrator.plan_response = [{"seq": 1, "agente": "research", "instruccion": "x"}]

    async def _side_effect(mission, deps):
        await deps.insert_steps(
            [
                {
                    "seq": 2,
                    "agente": "research",
                    "instruccion": "paso de reemplazo",
                    "depende_de": [],
                },
            ]
        )

    FakeOrchestrator.run_side_effect = _side_effect
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    fila = session.steps[(str(mission_id), 2)]
    assert fila["agente"] == "research"
    assert fila["instruccion"] == "paso de reemplazo"
    assert fila["status"] == "pending"


async def test_mision_nueva_persiste_y_recarga_depende_de_a_traves_de_usage():
    """WP-V5-05: `agent_steps` no tiene columna propia para `depende_de`
    (sin migración nueva, ver `run_mission.py`) — viaja escondido dentro de
    `usage` en el INSERT (`_insert_steps`) y se reconstruye en el SELECT
    (`_load_steps`/`_paso_con_depende_de`). Verifica el round-trip completo
    con el SQL real de este módulo (no fakeado) sobre `FakeSession`."""
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, objetivo="Investiga")
    FakeOrchestrator.plan_response = [
        {"seq": 1, "agente": "research", "instruccion": "uno", "depende_de": []},
        {"seq": 2, "agente": "data_analyst", "instruccion": "dos", "depende_de": [0]},
    ]
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id), deps)

    mission = FakeOrchestrator.run_calls[0]["mission"]
    paso_1 = next(p for p in mission.plan if p["seq"] == 1)
    paso_2 = next(p for p in mission.plan if p["seq"] == 2)
    assert paso_1["depende_de"] == []
    assert paso_2["depende_de"] == [0]
    # `depende_de` se limpia del `usage` que de verdad expone `Mission.plan`
    # (no aparece mezclado con datos de uso reales) — ver `_paso_con_depende_de`.
    assert paso_1["usage"] is None
    assert paso_2["usage"] is None
    # la fila cruda en la "BD" sí lo guarda dentro de `usage` (es donde vive).
    assert session.steps[(str(mission_id), 1)]["usage"] == {"depende_de": []}
    assert session.steps[(str(mission_id), 2)]["usage"] == {"depende_de": [0]}


# ---------------------------------------------------------------------------
# Reanudación
# ---------------------------------------------------------------------------


async def test_resume_resetea_el_paso_pendiente_y_aprueba_solo_su_tool_call():
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status="waiting_confirmation")
    session.seed_step(
        mission_id,
        tenant_id,
        seq=1,
        status="done",
        resultado="resultado del paso 1",
    )
    session.seed_step(
        mission_id,
        tenant_id,
        seq=2,
        status="waiting_confirmation",
        usage={
            "pending_tool_call": {
                "id": "call-guardado",
                "name": "enviar_correo",
                "args": {"to": "x@y.com"},
            }
        },
    )
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(
        _envelope(mission_id, tenant_id, resume=True, approved_step_seq=2), deps
    )

    # plan() NUNCA se llama en una reanudación.
    assert FakeOrchestrator.plan_calls == []

    # el paso 2 se resetea a "pending" ANTES de que el Orchestrator lo vea.
    mission = FakeOrchestrator.run_calls[0]["mission"]
    assert mission.resume_step_seq == 2
    assert mission.approved_tool_call_id == "call-guardado"
    # `name`/`args` (no solo el `id`) viajan a `Mission`: son los que
    # `Orchestrator._run_resumed_step` necesita para ejecutar la tool DIRECTO
    # en vez de reinvocar al LLM (que acuñaría un `tool_call_id` nuevo que
    # jamás coincidiría con el aprobado, ver `edecan_agents.orchestrator`).
    assert mission.approved_tool_name == "enviar_correo"
    assert mission.approved_tool_args == {"to": "x@y.com"}
    paso_2 = next(p for p in mission.plan if p["seq"] == 2)
    assert paso_2["status"] == "pending"
    paso_1 = next(p for p in mission.plan if p["seq"] == 1)
    assert paso_1["status"] == "done"
    assert paso_1["resultado"] == "resultado del paso 1"

    # la misión pasó a running antes de ejecutar.
    assert session.missions[str(mission_id)]["status"] == "running"


async def test_resume_sin_paso_waiting_confirmation_no_ejecuta_nada():
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status="waiting_confirmation")
    session.seed_step(mission_id, tenant_id, seq=1, status="done")
    # no hay paso seq=2 -> resume pide un paso que no existe.
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(
        _envelope(mission_id, tenant_id, resume=True, approved_step_seq=2), deps
    )

    assert FakeOrchestrator.run_calls == []
    assert session.missions[str(mission_id)]["status"] == "waiting_confirmation"  # sin tocar


async def test_resume_con_paso_en_status_distinto_de_waiting_confirmation_no_ejecuta():
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status="waiting_confirmation")
    session.seed_step(mission_id, tenant_id, seq=1, status="done")
    # seq=2 ya no está "waiting_confirmation": el resume no debe encontrarlo.
    session.seed_step(mission_id, tenant_id, seq=2, status="pending")
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(
        _envelope(mission_id, tenant_id, resume=True, approved_step_seq=2), deps
    )

    assert FakeOrchestrator.run_calls == []


async def test_resume_sin_approved_step_seq_no_replanifica():
    # Payload de cola malformado: `resume=True` sin `approved_step_seq` (el
    # único caller real, `missions.confirm_mission`, siempre manda los dos
    # juntos — esto solo pasaría con un mensaje de cola armado a mano fuera
    # del flujo normal). Debe rechazarse explícito en vez de caer al camino
    # de "misión nueva" y volver a plan()/INSERTar pasos en `agent_steps`
    # (que no tiene UNIQUE(tenant_id, mission_id, seq)).
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status="waiting_confirmation")
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(_envelope(mission_id, tenant_id, resume=True), deps)

    assert FakeOrchestrator.plan_calls == []
    assert FakeOrchestrator.run_calls == []
    assert session.missions[str(mission_id)]["status"] == "waiting_confirmation"  # sin tocar


async def test_resume_conserva_depende_de_de_los_pasos_pendientes_que_nunca_corrieron():
    """WP-V5-05: al reanudar, un paso que YA estaba `pending` (nunca llegó a
    lanzarse, quedó esperando a que el paso `waiting_confirmation` se
    resolviera) debe conservar su `depende_de` original — viajó escondido en
    `usage` desde que se insertó (ver
    `test_mision_nueva_persiste_y_recarga_depende_de_a_traves_de_usage`) y
    `_load_steps` lo reconstruye igual en el camino de reanudación, no solo
    en el de planificación inicial."""
    session = FakeSession()
    mission_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_mission(mission_id, tenant_id, status="waiting_confirmation")
    session.seed_step(mission_id, tenant_id, seq=1, status="done", resultado="resultado del paso 1")
    session.seed_step(
        mission_id,
        tenant_id,
        seq=2,
        status="waiting_confirmation",
        usage={
            "pending_tool_call": {
                "id": "call-guardado",
                "name": "enviar_correo",
                "args": {"to": "x@y.com"},
            }
        },
    )
    # el paso 3 nunca llegó a lanzarse -- sigue "pending" con su
    # `depende_de` tal cual quedó guardado al insertarse (depende de los
    # pasos 1 y 2, índices 0-based 0 y 1).
    session.seed_step(
        mission_id, tenant_id, seq=3, status="pending", usage={"depende_de": [0, 1]}
    )
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_mission_module.handle(
        _envelope(mission_id, tenant_id, resume=True, approved_step_seq=2), deps
    )

    mission = FakeOrchestrator.run_calls[0]["mission"]
    paso_3 = next(p for p in mission.plan if p["seq"] == 3)
    assert paso_3["depende_de"] == [0, 1]
    assert paso_3["status"] == "pending"

    # el paso 2 (el que se reanuda) ya NO trae `depende_de`: su `usage`
    # quedó reemplazado por `pending_tool_call` al pausar, que es
    # exactamente lo que la reanudación necesita (`_run_resumed_step` nunca
    # consulta `depende_de` de todos modos, ejecuta la tool aprobada
    # directo).
    paso_2 = next(p for p in mission.plan if p["seq"] == 2)
    assert paso_2.get("depende_de") is None
    assert paso_2["usage"]["pending_tool_call"]["name"] == "enviar_correo"
