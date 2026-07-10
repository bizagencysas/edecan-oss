"""MCP bring-your-own en `apps/worker` (`ARCHITECTURE.md` §15, WP-V6-07):
`Deps.mcp_tools_para` (`edecan_worker/deps.py`) y su cableado en
`run_mission.py`/`run_automation.py`.

Tres grupos, cada uno con sus propios dobles locales (mismo criterio de
"cada archivo de test es autónomo" que `test_run_mission_handler.py`/
`test_automation_handlers.py`, que tampoco comparten sus `FakeSession`/
`FakeOrchestrator` entre sí pese a solaparse conceptualmente):

1. `Deps.mcp_tools_para`/`Deps._build_mcp_tools` en aislamiento — la lógica
   NUEVA real de este WP (flag de plan, filas `connector_accounts`, vault,
   fail-open). `edecan_mcp` SÍ se ejercita de verdad acá (agregado a
   `sys.path`, mismo criterio que `test_mcp_router.py`/`test_ads_router.py`
   para paquetes hermanos que `apps/worker` todavía no declara como
   dependencia formal), con `MCPClient`/`_build_transport` monkeypatcheados
   (cero red real) — ya no hace falta redemostrar el protocolo MCP en sí
   (eso lo cubre `packages/mcp/tests/` exhaustivamente).
2. `run_mission.py`: verifica que el merge ocurre ANTES de construir
   `Orchestrator` (`edecan_agents` fakeado vía `sys.modules`, mismo patrón
   que `test_run_mission_handler.py` — no se reimporta esa suite, se
   reconstruye el mínimo necesario acá).
3. `run_automation.py`: ídem, más el comportamiento documentado de que
   `_build_safe_registry` (`edecan_automations.runner`, con su propia suite
   en `packages/automations/tests/`) excluye toda tool `dangerous=True` —
   como las tools MCP SIEMPRE lo son, quedan excluidas de cualquier
   automatización headless por diseño. Se replica localmente el contrato ya
   documentado de esa función (no se importa `edecan_automations` de verdad,
   `ARCHITECTURE.md` §10.1) para probarlo sin acoplarse a su código interno.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
from edecan_core.tools import Tool, ToolContext, ToolRegistry, ToolResult
from edecan_schemas import JobEnvelope
from fakes import make_deps

_MCP_SRC = str(Path(__file__).resolve().parents[3] / "packages" / "mcp")
if _MCP_SRC not in sys.path:
    sys.path.insert(0, _MCP_SRC)

import edecan_worker.deps as worker_deps  # noqa: E402


@asynccontextmanager
async def _session_factory_cm(session: Any):
    """`Deps.session_factory` real es `edecan_db.session.get_session`
    (`(tenant_id) -> AsyncSession` como context manager async) — este doble
    ignora `tenant_id` y siempre entrega la MISMA `session` fake que arma
    cada test."""
    yield session

# ---------------------------------------------------------------------------
# Grupo 1 — Deps.mcp_tools_para / Deps._build_mcp_tools
# ---------------------------------------------------------------------------


class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _FakeResult:
        return self

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _FakeMCPSession:
    """Solo entiende `SELECT ... FROM connector_accounts` filtrando por
    `connector_key` (lo único que toca `_build_mcp_tools`) — cualquier otra
    query revienta con `AssertionError`, a propósito: si `mcp_tools_para`
    alguna vez emite otra cosa, este test debe fallar ruidosamente. Ya NO
    devuelve `scopes` (`ARCHITECTURE.md` §15.g pinned: `connector_accounts`
    es identidad pura para MCP, la config completa vive en el vault)."""

    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []
        self.executed: list[tuple[str, dict[str, Any]]] = []

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        if "FROM connector_accounts" in sql and params.get("connector_key") == "mcp":
            return _FakeResult(rows=list(self._rows))
        raise AssertionError(f"query inesperada en el fake: {sql}")


class _FakeVault:
    def __init__(self, bundles: dict[uuid.UUID, Any] | None = None) -> None:
        self._bundles = bundles or {}

    async def get(self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID) -> Any:
        return self._bundles.get(connector_account_id)


class _Bundle:
    def __init__(self, access_token: str) -> None:
        self.access_token = access_token


def _mcp_row(*, account_id: uuid.UUID, nombre: str) -> dict[str, Any]:
    return {"id": account_id, "external_account_id": nombre}


def _mcp_bundle(
    *,
    nombre: str,
    transporte: str,
    url: str | None = None,
    comando: str | None = None,
    headers: dict[str, str] | None = None,
) -> _Bundle:
    """`_Bundle` con la forma EXACTA que produce
    `edecan_mcp.provider_config.serializar_config_mcp` (`ARCHITECTURE.md`
    §15.g pinned) — `{nombre, transporte, url, comando, headers}` todo
    junto."""
    payload = {
        "nombre": nombre,
        "transporte": transporte,
        "url": url,
        "comando": comando,
        "headers": headers or {},
    }
    return _Bundle(json.dumps(payload))


async def test_mcp_tools_para_sin_tenant_id_da_lista_vacia() -> None:
    resultado = await worker_deps.Deps.mcp_tools_para(
        object(), None, _FakeMCPSession(), {"tools.mcp": True}
    )
    assert resultado == []


async def test_mcp_tools_para_sin_flag_da_lista_vacia_sin_tocar_la_sesion() -> None:
    fila = _mcp_row(account_id=uuid.uuid4(), nombre="acme")
    session = _FakeMCPSession(rows=[fila])
    deps = make_deps()
    resultado = await deps.mcp_tools_para(uuid.uuid4(), session, {"tools.mcp": False})
    assert resultado == []
    assert session.executed == []  # el gate de flag corta ANTES de tocar la sesión


async def test_mcp_tools_para_sin_servidores_da_lista_vacia() -> None:
    deps = make_deps()
    session = _FakeMCPSession(rows=[])
    resultado = await deps.mcp_tools_para(uuid.uuid4(), session, {"tools.mcp": True})
    assert resultado == []


async def test_mcp_tools_para_construye_tools_desde_configs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from edecan_mcp import tool_adapter as mod

    tenant_id = uuid.uuid4()
    account_id = uuid.uuid4()
    row = _mcp_row(account_id=account_id, nombre="Acme")

    class _ClienteFalso:
        async def initialize(self) -> dict:
            return {}

        async def list_tools(self) -> list[dict]:
            return [
                {
                    "name": "buscar",
                    "description": "Busca cosas.",
                    "input_schema": {"type": "object"},
                }
            ]

        async def close(self) -> None:
            pass

    monkeypatch.setattr(mod, "MCPClient", lambda transport: _ClienteFalso())
    monkeypatch.setattr(mod, "_build_transport", lambda config, headers: object())

    async def _validar_ok(url: str, *, local_mode: bool) -> None:
        return None

    monkeypatch.setattr(mod, "validar_url_mcp", _validar_ok)

    bundle = _mcp_bundle(
        nombre="Acme",
        transporte="http",
        url="https://acme.example.com/rpc",
        headers={"Authorization": "Bearer xyz"},
    )
    deps = make_deps(vault=lambda session: _FakeVault({account_id: bundle}))

    resultado = await deps.mcp_tools_para(
        tenant_id, _FakeMCPSession(rows=[row]), {"tools.mcp": True}
    )

    assert len(resultado) == 1
    tool = resultado[0]
    assert tool.name == "mcp_acme_buscar"
    assert tool.dangerous is True
    assert tool.requires_flags == frozenset({"tools.mcp"})


async def test_mcp_tools_para_falla_abierto_si_la_sesion_lanza() -> None:
    class _SesionRota:
        async def execute(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("Postgres caído")

    deps = make_deps()
    resultado = await deps.mcp_tools_para(uuid.uuid4(), _SesionRota(), {"tools.mcp": True})
    assert resultado == []


async def test_mcp_tools_para_falla_abierto_si_edecan_mcp_no_esta_instalado(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mismo truco que exige la ausencia real de un paquete: un valor `None`
    en `sys.modules[nombre]` hace que CUALQUIER `import edecan_mcp[...]`
    posterior falle con `ImportError` de inmediato — sin necesidad de
    desinstalar el paquete de verdad del entorno de test."""
    monkeypatch.setitem(sys.modules, "edecan_mcp", None)
    monkeypatch.setitem(sys.modules, "edecan_mcp.provider_config", None)
    monkeypatch.setitem(sys.modules, "edecan_mcp.tool_adapter", None)

    deps = make_deps()
    session = _FakeMCPSession(rows=[_mcp_row(account_id=uuid.uuid4(), nombre="acme")])
    resultado = await deps.mcp_tools_para(uuid.uuid4(), session, {"tools.mcp": True})
    assert resultado == []


# ---------------------------------------------------------------------------
# Grupo 2 — run_mission.py: el merge ocurre ANTES de construir Orchestrator
# ---------------------------------------------------------------------------


class _FakeMCPTool(Tool):
    name = "mcp_acme_buscar"
    description = "[MCP:Acme] Tool remota de prueba."
    input_schema = {"type": "object", "properties": {}}
    dangerous = True
    requires_flags = frozenset({"tools.mcp"})

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content="no se invoca en estos tests de wiring")


class _FakeOrchestratorQueSnapshotea:
    """A diferencia del `FakeOrchestrator` de `test_run_mission_handler.py`
    (que solo registra llamadas), este captura si la tool MCP YA estaba en
    el registry EN EL MOMENTO de construirse — la única forma de probar
    "antes de", no solo "en algún momento"."""

    instancias: list[_FakeOrchestratorQueSnapshotea] = []

    def __init__(self, llm_router: Any, registry: Any) -> None:
        self.registry = registry
        self.tenia_la_tool_mcp_al_construirse = registry.get("mcp_acme_buscar") is not None
        type(self).instancias.append(self)

    async def plan(
        self, objetivo: str, flags: dict[str, Any], settings: Any
    ) -> list[dict[str, Any]]:
        return [{"seq": 1, "agente": "research", "instruccion": "Investiga"}]

    async def run(self, mission: Any, deps: Any) -> None:
        pass


class _FakeMission:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _MissionFakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _MissionFakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _MissionFakeSession:
    """Lo mínimo para que `run_mission.handle()` llegue a construir
    `Orchestrator` con una misión "nueva" — sin resume, sin persistencia de
    pasos post-ejecución (no hace falta para este test de ordering)."""

    def __init__(self, *, mission_row: dict[str, Any], tenant_row: dict[str, Any]) -> None:
        self._mission = mission_row
        self._tenant = tenant_row
        self._mcp_rows: list[dict[str, Any]] = []
        self.steps: dict[int, dict[str, Any]] = {}

    def con_servidor_mcp(self, row: dict[str, Any]) -> _MissionFakeSession:
        self._mcp_rows = [row]
        return self

    async def execute(
        self, clause: Any, params: dict[str, Any] | None = None
    ) -> _MissionFakeResult:
        sql = str(clause)
        params = dict(params or {})
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM agent_missions" in sql:
            return _MissionFakeResult(rows=[self._mission])
        if primer_token == "SELECT" and "FROM tenants" in sql:
            return _MissionFakeResult(rows=[self._tenant])
        if primer_token == "SELECT" and "FROM connector_accounts" in sql:
            if params.get("connector_key") == "mcp":
                return _MissionFakeResult(rows=list(self._mcp_rows))
            return _MissionFakeResult(rows=[{"id": uuid.uuid4()}])  # LLM: cualquier cuenta sirve
        if primer_token == "SELECT" and "FROM agent_steps" in sql:
            return _MissionFakeResult(rows=list(self.steps.values()))
        if primer_token == "INSERT" and "agent_steps" in sql:
            self.steps[params["seq"]] = {
                "seq": params["seq"],
                "agente": params["agente"],
                "instruccion": params["instruccion"],
                "status": "pending",
                "resultado": None,
                "usage": None,
            }
            return _MissionFakeResult()
        if primer_token == "UPDATE":
            return _MissionFakeResult()
        raise AssertionError(f"query inesperada en el fake de misión: {sql}")


class _FakeLLMVaultSiempreValido:
    async def get(self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID) -> Any:
        return _Bundle(json.dumps({"kind": "anthropic", "api_key": "sk-ant-fake"}))


def _install_fake_edecan_agents(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_module = types.ModuleType("edecan_agents")
    fake_module.Orchestrator = _FakeOrchestratorQueSnapshotea  # type: ignore[attr-defined]
    fake_module.Mission = _FakeMission  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_agents", fake_module)


async def test_run_mission_registra_tool_mcp_antes_de_construir_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_worker.handlers.run_mission as run_mission_module

    _FakeOrchestratorQueSnapshotea.instancias = []
    _install_fake_edecan_agents(monkeypatch)
    monkeypatch.setattr(run_mission_module, "_build_registry", lambda: ToolRegistry())

    async def _fake_mcp_tools_para(tenant_id: Any, session: Any, flags: Any) -> list[Any]:
        return [_FakeMCPTool()]

    tenant_id = uuid.uuid4()
    mission_id = uuid.uuid4()
    session = _MissionFakeSession(
        mission_row={
            "id": str(mission_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "objetivo": "Objetivo de prueba",
            "status": "planning",
            "plan": None,
            "resultado": None,
            "presupuesto": {"max_steps": 8},
            "error": None,
        },
        tenant_row={"plan_key": "hosted_pro"},
    )

    deps = make_deps(
        session_factory=lambda t: _session_factory_cm(session),
        vault=lambda s: _FakeLLMVaultSiempreValido(),
    )
    deps.mcp_tools_para = _fake_mcp_tools_para  # type: ignore[method-assign]

    envelope = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_mission",
        payload={"mission_id": str(mission_id)},
    )
    await run_mission_module.handle(envelope, deps)

    assert len(_FakeOrchestratorQueSnapshotea.instancias) == 1
    instancia = _FakeOrchestratorQueSnapshotea.instancias[0]
    assert instancia.tenia_la_tool_mcp_al_construirse is True
    assert instancia.registry.get("mcp_acme_buscar") is not None


# ---------------------------------------------------------------------------
# Grupo 3 — run_automation.py: merge antes de _apply_agent_profile, y las
# tools MCP (dangerous=True) quedan excluidas de cualquier automatización
# headless por diseño (documentado + testeado localmente, ver docstring del
# módulo).
# ---------------------------------------------------------------------------


class _AutomationFakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self._rows = rows or []

    def mappings(self) -> _AutomationFakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return dict(self._rows[0]) if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows]


class _AutomationFakeSession:
    def __init__(self, *, automation_row: dict[str, Any], tenant_row: dict[str, Any]) -> None:
        self._automation = automation_row
        self._tenant = tenant_row
        self._mcp_rows: list[dict[str, Any]] = []

    def con_servidor_mcp(self, row: dict[str, Any]) -> _AutomationFakeSession:
        self._mcp_rows = [row]
        return self

    async def execute(
        self, clause: Any, params: dict[str, Any] | None = None
    ) -> _AutomationFakeResult:
        sql = str(clause)
        params = dict(params or {})
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM automations" in sql:
            return _AutomationFakeResult(rows=[self._automation])
        if primer_token == "SELECT" and "FROM tenants" in sql:
            return _AutomationFakeResult(rows=[self._tenant])
        if primer_token == "SELECT" and "FROM connector_accounts" in sql:
            if params.get("connector_key") == "mcp":
                return _AutomationFakeResult(rows=list(self._mcp_rows))
            return _AutomationFakeResult(rows=[{"id": uuid.uuid4()}])
        if primer_token == "SELECT" and "FROM personas" in sql:
            return _AutomationFakeResult(rows=[])
        if primer_token == "INSERT" and "automation_runs" in sql:
            return _AutomationFakeResult()
        if primer_token == "UPDATE":
            return _AutomationFakeResult()
        raise AssertionError(f"query inesperada en el fake de automatización: {sql}")


class _FakeRunnerDeps:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


_run_automation_capturas: list[Any] = []


async def _fake_run_automation_turn(automation: Any, run_deps: Any) -> None:
    _run_automation_capturas.append(run_deps)


def _install_fake_edecan_automations(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_runner_module = types.ModuleType("edecan_automations.runner")
    fake_runner_module.RunnerDeps = _FakeRunnerDeps  # type: ignore[attr-defined]
    fake_runner_module.run_automation = _fake_run_automation_turn  # type: ignore[attr-defined]
    fake_package = types.ModuleType("edecan_automations")
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.runner", fake_runner_module)


async def test_run_automation_registra_tool_mcp_antes_del_perfil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_worker.handlers.run_automation as run_automation_module

    _run_automation_capturas.clear()
    _install_fake_edecan_automations(monkeypatch)
    monkeypatch.setattr(run_automation_module, "_build_registry", lambda: ToolRegistry())

    async def _fake_mcp_tools_para(tenant_id: Any, session: Any, flags: Any) -> list[Any]:
        return [_FakeMCPTool()]

    tenant_id = uuid.uuid4()
    automation_id = uuid.uuid4()
    session = _AutomationFakeSession(
        automation_row={
            "id": str(automation_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "nombre": "Automatización de prueba",
            "enabled": True,
            "accion": json.dumps({"kind": "agent_instruction", "instruccion": "Haz algo."}),
        },
        tenant_row={"plan_key": "hosted_pro"},
    )

    deps = make_deps(
        session_factory=lambda t: _session_factory_cm(session),
        vault=lambda s: _FakeLLMVaultSiempreValido(),
    )
    deps.mcp_tools_para = _fake_mcp_tools_para  # type: ignore[method-assign]

    envelope = JobEnvelope(
        job_id=uuid.uuid4(),
        tenant_id=tenant_id,
        type="run_automation",
        payload={"automation_id": str(automation_id)},
    )
    await run_automation_module.handle(envelope, deps)

    assert len(_run_automation_capturas) == 1
    run_deps = _run_automation_capturas[0]
    # `_apply_agent_profile` sin `accion.agente` deja el registry SIN envolver
    # (ver su docstring) — así que el mismo registry que recibió el merge es
    # el que termina en `RunnerDeps.registry`.
    assert run_deps.registry.get("mcp_acme_buscar") is not None


def _replica_build_safe_registry(
    full_registry: ToolRegistry, flags: dict[str, Any]
) -> ToolRegistry:
    """Replica LOCAL del contrato ya documentado de
    `edecan_automations.runner._build_safe_registry` (leído de su código
    real, ver el docstring de `run_automation.py`): excluye toda tool
    `dangerous=True` y las de `EXCLUDED_TOOL_NAMES`. NO se importa
    `edecan_automations` de verdad (`ARCHITECTURE.md` §10.1) — este helper
    prueba que, CON ese contrato documentado, una tool MCP (siempre
    `dangerous=True`) queda excluida; el contrato en sí ya tiene su propia
    suite exhaustiva en `packages/automations/tests/`."""
    excluidas = frozenset({"delegar_mision", "gestionar_automatizacion"})
    safe = ToolRegistry()
    for spec in full_registry.specs(flags):
        if spec.name in excluidas:
            continue
        tool = full_registry.get(spec.name)
        if tool is None or tool.dangerous:
            continue
        safe.register(tool)
    return safe


def test_tool_mcp_dangerous_queda_excluida_de_automatizaciones_headless() -> None:
    registry = ToolRegistry()
    registry.register(_FakeMCPTool())
    flags = {"tools.mcp": True}

    assert registry.get("mcp_acme_buscar").dangerous is True  # precondición del diseño

    safe_registry = _replica_build_safe_registry(registry, flags)

    assert safe_registry.get("mcp_acme_buscar") is None
    assert "mcp_acme_buscar" not in {s.name for s in safe_registry.specs(flags)}
