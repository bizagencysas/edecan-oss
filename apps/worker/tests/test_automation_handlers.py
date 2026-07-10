"""Jobs `run_automation`/`automation_scan` (`ROADMAP_V2.md` §7.3, §7.4, §7.6;
dueño WP-V2-07) y la cadencia nueva de `edecan_worker.scheduler`.

`edecan_automations` se fakea vía monkeypatch de import (inyectando un
módulo falso en `sys.modules`, mismo patrón que `test_run_mission_handler.py`
con `edecan_agents`/`test_send_reminder_scan.py` con `edecan_core.queue`): el
`runner`/`engine` reales ya tienen su propia suite exhaustiva en
`packages/automations/tests/`, así que aquí solo importa verificar que los
HANDLERS (a) filtran siempre por `tenant_id`, (b) cargan/persisten
`automations`/`automation_runs` con el SQL correcto, y (c) arman
`RunnerDeps`/encolan `run_automation` con los datos correctos.

`FakeSession` es un almacén en memoria de `automations`/`automation_runs`/
`tenants`/`personas` que entiende (por prefijo + subcadena) el SQL que emiten
`run_automation.py`/`automation_scan.py` — mismo espíritu que `FakeSession`
en `test_run_mission_handler.py`.
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
import edecan_worker.scheduler as scheduler_module
import pytest
from edecan_schemas import FLAG_AUTOMATIONS_RULES, PLANES, JobEnvelope
from fakes import install_fake_edecan_core_queue, make_deps

# `Deps.llm_router_for` (bring-your-own, WP-V3-02) ahora LANZA
# `TenantLLMNotConnectedError` en vez de degradar a la plataforma cuando no
# puede resolver un proveedor propio del tenant (ver
# `apps/worker/tests/test_llm_por_tenant.py`, que cubre esa resolución en
# detalle). Este archivo no prueba esa resolución en sí, solo `run_automation`
# — así que `FakeSession`/`_FakeLLMVault` simulan un tenant que YA conectó un
# proveedor LLM válido, para que las pruebas de "camino feliz" no se
# interrumpan por la resolución bring-your-own. UUID fijo porque `execute()`
# no varía la respuesta por tenant.
_LLM_ACCOUNT_ID = uuid.uuid4()


class _FakeLLMVault:
    async def get(self, *, tenant_id: uuid.UUID, connector_account_id: uuid.UUID) -> Any:
        if connector_account_id != _LLM_ACCOUNT_ID:
            return None

        class _Bundle:
            access_token = json.dumps({"kind": "anthropic", "api_key": "sk-ant-fake-de-prueba"})

        return _Bundle()

# ---------------------------------------------------------------------------
# FakeSession: automations / automation_runs / tenants / personas
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
        self.executed: list[tuple[str, dict[str, Any]]] = []

    def seed_automation(
        self, automation_id: uuid.UUID, tenant_id: uuid.UUID, **fields: Any
    ) -> None:
        row = {
            "id": str(automation_id),
            "tenant_id": str(tenant_id),
            "user_id": str(uuid.uuid4()),
            "nombre": "Automatización de prueba",
            "descripcion": "",
            "trigger": json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY"}),
            "accion": json.dumps(
                {"kind": "agent_instruction", "instruccion": "Resume mis correos."}
            ),
            "enabled": True,
            "next_run_at": None,
            "last_run_at": None,
        }
        row.update(fields)
        self.automations[str(automation_id)] = row

    def seed_tenant(self, tenant_id: uuid.UUID, plan_key: str) -> None:
        self.tenants[str(tenant_id)] = {"plan_key": plan_key}

    def seed_persona(self, user_id: uuid.UUID, **fields: Any) -> None:
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

    async def execute(self, clause: Any, params: dict[str, Any] | None = None) -> _FakeResult:
        sql = str(clause)
        params = dict(params or {})
        self.executed.append((sql, params))
        primer_token = sql.strip().split(None, 1)[0].upper()

        if primer_token == "SELECT" and "FROM automations" in sql and "next_run_at <=" in sql:
            # _list_due_schedule_automations: barrido global (sin tenant_id).
            ahora = params["now"]
            rows = [
                row
                for row in self.automations.values()
                if row["enabled"]
                and row.get("next_run_at") is not None
                and row["next_run_at"] <= ahora
            ]
            rows.sort(key=lambda r: r["next_run_at"])
            return _FakeResult(rows=rows)

        if primer_token == "SELECT" and "FROM automations" in sql:
            row = self.automations.get(params["id"])
            if row is not None and row["tenant_id"] == params["tenant_id"]:
                return _FakeResult(rows=[row])
            return _FakeResult(rows=[])

        if primer_token == "SELECT" and "FROM tenants" in sql:
            row = self.tenants.get(params["id"])
            return _FakeResult(rows=[row] if row is not None else [])

        if primer_token == "SELECT" and "FROM connector_accounts" in sql:
            # Ver el comentario junto a `_LLM_ACCOUNT_ID`: este archivo
            # simula un tenant que ya conectó su LLM propio, siempre.
            return _FakeResult(rows=[{"id": _LLM_ACCOUNT_ID}])

        if primer_token == "SELECT" and "FROM personas" in sql:
            row = self.personas.get(params["user_id"])
            return _FakeResult(rows=[row] if row is not None else [])

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

        if primer_token == "UPDATE" and "automations" in sql and "next_run_at" in sql:
            row = self.automations.get(params["id"])
            if row is not None:
                row["next_run_at"] = params["next_run_at"]
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


# ---------------------------------------------------------------------------
# `edecan_automations.runner` falso (para los tests de run_automation.py)
# ---------------------------------------------------------------------------


class FakeRunnerDeps:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeRunner:
    """Instalado como `edecan_automations.runner` falso — registra cada
    llamada a `run_automation(automation, deps)` en `calls` (se resetea por
    test vía la fixture `fake_runner`)."""

    calls: list[tuple[dict[str, Any], FakeRunnerDeps]] = []
    side_effect: Any = None  # callable(automation, deps) opcional

    @staticmethod
    async def run_automation(automation: dict[str, Any], deps: FakeRunnerDeps) -> None:
        _FakeRunner.calls.append((automation, deps))
        if _FakeRunner.side_effect is not None:
            await _FakeRunner.side_effect(automation, deps)


REGISTRY_SENTINEL = object()
"""Devuelto por el `_build_registry` monkeypatcheado (ver `fake_runner`
abajo): `run_automation.py` construye un `edecan_core.tools.ToolRegistry`
REAL vía `load_entry_points(group="edecan.tools")` — aislar el test de esa
máquina real (entry points instalados en el venv actual) lo hace más rápido
y determinista, mismo criterio que `test_run_mission_handler.py`."""


def _sin_configurar_compute_next_run(
    rrule: str, after: datetime, anchor: datetime | None = None
) -> None:
    raise AssertionError(
        "compute_next_run se llamó sin que el test lo configurara "
        "(ver _install_fake_compute_next_run)"
    )


@pytest.fixture(autouse=True)
def fake_runner(monkeypatch: pytest.MonkeyPatch):
    """Registra un `edecan_automations` falso en `sys.modules` (mismo patrón
    que `fakes.install_fake_edecan_core_queue`, ver su docstring): hace falta
    stubear TANTO el paquete padre `edecan_automations` COMO los submódulos
    `.runner`/`.engine` — `from edecan_automations.runner import X` resuelve
    el paquete padre primero, y si solo se stubea el submódulo, Python igual
    ejecuta el `__init__.py` REAL del paquete padre (que a su vez importa
    `edecan_core.Tool` vía `.tools`) para resolverlo.

    `automation_scan.py` importa `edecan_automations.engine` de forma
    incondicional al tope de `handle()` (incluso cuando no hay
    automatizaciones vencidas), así que TODOS los tests de este archivo
    necesitan ese submódulo stubeado -no solo los que llaman
    `_install_fake_compute_next_run`- con un `compute_next_run` por defecto
    que revienta si se invoca sin que el test lo haya configurado
    explícitamente (evita un falso positivo silencioso)."""
    _FakeRunner.calls = []
    _FakeRunner.side_effect = None

    fake_runner_module = types.ModuleType("edecan_automations.runner")
    fake_runner_module.RunnerDeps = FakeRunnerDeps  # type: ignore[attr-defined]
    fake_runner_module.run_automation = _FakeRunner.run_automation  # type: ignore[attr-defined]
    fake_engine_module = types.ModuleType("edecan_automations.engine")
    fake_engine_module.compute_next_run = _sin_configurar_compute_next_run  # type: ignore[attr-defined]
    fake_package = types.ModuleType("edecan_automations")
    fake_package.runner = fake_runner_module  # type: ignore[attr-defined]
    fake_package.engine = fake_engine_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.runner", fake_runner_module)
    monkeypatch.setitem(sys.modules, "edecan_automations.engine", fake_engine_module)
    monkeypatch.setattr(run_automation_module, "_build_registry", lambda: REGISTRY_SENTINEL)
    return _FakeRunner


# ---------------------------------------------------------------------------
# `edecan_agents` falso (solo para los tests de `accion.agente` — a
# diferencia de `edecan_automations`, `run_automation.py` lo importa de
# forma perezosa DENTRO de `_apply_agent_profile`, y solo cuando
# `accion.agente` viene truthy: los tests que nunca fijan `agente` no
# necesitan instalar este fake, mismo motivo por el que esto no es un
# fixture autouse como `fake_runner` de arriba).
# ---------------------------------------------------------------------------


class FakeAgentProfile:
    """Doble de `edecan_agents.profiles.AgentProfile`: solo los atributos que
    `_apply_agent_profile` lee (`nombre`/`system_prompt_extra`/
    `allowed_tools`/`disponible`)."""

    def __init__(
        self,
        *,
        nombre: str,
        system_prompt_extra: str,
        allowed_tools: frozenset[str],
        disponible: bool,
    ) -> None:
        self.nombre = nombre
        self.system_prompt_extra = system_prompt_extra
        self.allowed_tools = allowed_tools
        self.disponible = disponible


class FakeRestrictedRegistry:
    """Doble de `edecan_agents.RestrictedRegistry`: solo registra con qué se
    construyó, no reimplementa el filtrado real de `.get`/`.specs` (eso ya lo
    prueba `packages/agents/tests/test_registry_view.py` — acá solo importa
    verificar el WIRING: que `_apply_agent_profile` la invoque con el
    registro y el `allowed_tools` correctos)."""

    def __init__(self, wrapped: Any, allowed_tools: frozenset[str]) -> None:
        self.wrapped = wrapped
        self.allowed_tools = allowed_tools


def _install_fake_edecan_agents(
    monkeypatch: pytest.MonkeyPatch, profiles: dict[str, FakeAgentProfile]
) -> None:
    """Registra un `edecan_agents` falso en `sys.modules` (mismo patrón que
    `test_run_mission_handler.py`/`fakes.install_fake_edecan_core_queue`)."""
    fake_module = types.ModuleType("edecan_agents")
    fake_module.PROFILES = profiles  # type: ignore[attr-defined]
    fake_module.RestrictedRegistry = FakeRestrictedRegistry  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_agents", fake_module)


# ---------------------------------------------------------------------------
# run_automation.handle — casos borde
# ---------------------------------------------------------------------------


async def test_run_automation_sin_tenant_id_lanza_value_error() -> None:
    session = FakeSession()
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())
    with pytest.raises(ValueError):
        await run_automation_module.handle(_envelope(uuid.uuid4(), None), deps)


async def test_run_automation_no_encontrada_no_hace_nada() -> None:
    session = FakeSession()
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(uuid.uuid4(), uuid.uuid4()), deps)

    assert _FakeRunner.calls == []
    assert session.automation_runs == {}


async def test_run_automation_de_otro_tenant_se_trata_como_no_encontrada() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_dueno = uuid.uuid4()
    tenant_atacante = uuid.uuid4()
    session.seed_automation(automation_id, tenant_dueno)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_atacante), deps)

    assert _FakeRunner.calls == []


async def test_run_automation_desactivada_se_ignora() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id, enabled=False)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    assert _FakeRunner.calls == []
    assert session.automation_runs == {}


async def test_run_automation_plan_sin_flag_automations_rules_no_ejecuta(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A la fecha de este WP TODOS los planes reales traen automations.rules
    # en True (ARCHITECTURE.md §10.13) -- se inyecta un plan temporal sin el
    # flag para ejercitar esta compuerta (protege contra un downgrade de
    # plan entre el momento en que se encoló el job y el momento en que
    # corre, ver docstring del módulo).
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id)
    session.seed_tenant(tenant_id, "plan_sin_automatizaciones")
    plan_sin_flag = PLANES["free_selfhost"].model_copy(
        update={"flags": {**PLANES["free_selfhost"].flags, FLAG_AUTOMATIONS_RULES: False}}
    )
    fake_planes = {**PLANES, "plan_sin_automatizaciones": plan_sin_flag}
    monkeypatch.setattr(run_automation_module, "PLANES", fake_planes)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    assert _FakeRunner.calls == []
    assert session.automation_runs == {}


# ---------------------------------------------------------------------------
# run_automation.handle — ejecución real
# ---------------------------------------------------------------------------


async def test_run_automation_arma_runner_deps_con_ctx_correcto() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id, user_id=str(user_id))
    session.seed_tenant(tenant_id, "hosted_pro")
    session.seed_persona(user_id, nombre_asistente="Jarvis")
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    assert len(_FakeRunner.calls) == 1
    automation_arg, run_deps = _FakeRunner.calls[0]
    assert automation_arg["id"] == str(automation_id)
    assert automation_arg["accion"] == {
        "kind": "agent_instruction",
        "instruccion": "Resume mis correos.",
    }

    assert run_deps.ctx.tenant_id == tenant_id
    assert run_deps.ctx.user_id == user_id
    assert run_deps.ctx.session is session
    assert run_deps.ctx.settings is deps.settings
    # bring-your-own (WP-V3-02): este tenant tiene una config LLM propia
    # válida (`_FakeLLMVault`), así que `ctx.llm` es el router del TENANT,
    # no `deps.llm_router` de plataforma.
    assert run_deps.ctx.llm is not None
    assert run_deps.ctx.llm is not deps.llm_router
    assert run_deps.ctx.extras["approved_tool_calls"] == set()
    assert run_deps.ctx.extras["flags"]["automations.rules"] is True

    assert run_deps.llm_router is run_deps.ctx.llm  # mismo router tenant en ambos lugares
    assert run_deps.registry is REGISTRY_SENTINEL
    assert run_deps.flags["automations.rules"] is True
    assert run_deps.persona.nombre_asistente == "Jarvis"

    # se creó la fila automation_runs en 'running' ANTES de invocar al runner.
    [run_row] = session.automation_runs.values()
    assert run_row["status"] == "running"
    assert run_row["tenant_id"] == str(tenant_id)
    assert run_row["automation_id"] == str(automation_id)


async def test_run_automation_sin_tenant_en_bd_usa_plan_free_selfhost() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id)
    # nota: NO se llama session.seed_tenant(...) -> _load_tenant devuelve None.
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    _, run_deps = _FakeRunner.calls[0]
    assert run_deps.flags["automations.rules"] is True  # free_selfhost también lo trae en True


async def test_run_automation_sin_persona_usa_default() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id)
    # nota: NO se llama session.seed_persona(...) -> fila default (Edecán).
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    _, run_deps = _FakeRunner.calls[0]
    assert run_deps.persona.nombre_asistente == "Edecán"
    assert run_deps.persona.memoria_activada is True


async def test_run_automation_con_agente_disponible_recorta_registry_y_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`accion.agente` nombrando un perfil `disponible=True` debe recortar el
    `ToolRegistry` con `RestrictedRegistry(registry, perfil.allowed_tools)` y
    reemplazar la persona por una armada desde el perfil — igual que
    `Orchestrator._run_step` para un paso de misión (ver docstring de
    `run_automation.py`, sección "Perfil de agente opcional")."""
    perfil = FakeAgentProfile(
        nombre="Investigación",
        system_prompt_extra="Eres el sub-agente de investigación.",
        allowed_tools=frozenset({"buscar_web", "consultar_documentos"}),
        disponible=True,
    )
    _install_fake_edecan_agents(monkeypatch, {"research": perfil})

    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(
        automation_id,
        tenant_id,
        accion=json.dumps(
            {
                "kind": "agent_instruction",
                "instruccion": "Investiga la competencia.",
                "agente": "research",
            }
        ),
    )
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    _, run_deps = _FakeRunner.calls[0]
    assert isinstance(run_deps.registry, FakeRestrictedRegistry)
    assert run_deps.registry.wrapped is REGISTRY_SENTINEL
    assert run_deps.registry.allowed_tools == perfil.allowed_tools
    assert run_deps.persona.nombre_asistente == "Investigación"
    assert run_deps.persona.instrucciones == perfil.system_prompt_extra
    assert run_deps.persona.memoria_activada is False


async def test_run_automation_con_agente_desconocido_no_toca_registry_ni_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Una clave que no existe en `PROFILES` (typo) NO se redirige a un
    fallback (a diferencia de `Orchestrator.plan`/`_run_step`, que sí
    redirigen a `research`): deja el registro/persona genéricos tal cual,
    ver docstring del módulo."""
    _install_fake_edecan_agents(monkeypatch, {})  # PROFILES vacío: nunca resuelve.

    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session.seed_automation(
        automation_id,
        tenant_id,
        user_id=str(user_id),
        accion=json.dumps(
            {
                "kind": "agent_instruction",
                "instruccion": "Investiga la competencia.",
                "agente": "un_typo_que_no_existe",
            }
        ),
    )
    session.seed_persona(user_id, nombre_asistente="Jarvis")
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    _, run_deps = _FakeRunner.calls[0]
    assert run_deps.registry is REGISTRY_SENTINEL
    assert run_deps.persona.nombre_asistente == "Jarvis"


async def test_run_automation_con_agente_no_disponible_no_toca_registry_ni_persona(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un perfil `disponible=False` (los 13 declarados-no-activos de
    `profiles.py`) tampoco se aplica: mismo criterio defensivo que
    `Orchestrator._run_step` (`if perfil is None or not perfil.disponible`)."""
    perfil_no_disponible = FakeAgentProfile(
        nombre="Ventas",
        system_prompt_extra="Eres el sub-agente de ventas.",
        allowed_tools=frozenset({"enviar_correo"}),
        disponible=False,
    )
    _install_fake_edecan_agents(monkeypatch, {"sales": perfil_no_disponible})

    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(
        automation_id,
        tenant_id,
        accion=json.dumps(
            {"kind": "agent_instruction", "instruccion": "Sigue al prospecto.", "agente": "sales"}
        ),
    )
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    _, run_deps = _FakeRunner.calls[0]
    assert run_deps.registry is REGISTRY_SENTINEL


async def test_run_automation_save_run_persiste_status_detalle_y_last_run_at() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id)

    async def _side_effect(automation: dict[str, Any], deps: FakeRunnerDeps) -> None:
        await deps.save_run("done", {"resultado": "listo"})

    _FakeRunner.side_effect = _side_effect
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    [run_row] = session.automation_runs.values()
    assert run_row["status"] == "done"
    assert run_row["detalle"] == {"resultado": "listo"}
    assert session.automations[str(automation_id)]["last_run_at"] == "touched"


async def test_run_automation_save_run_waiting_confirmation() -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(automation_id, tenant_id)

    async def _side_effect(automation: dict[str, Any], deps: FakeRunnerDeps) -> None:
        pendiente = {"tool_call_id": "call-1", "name": "enviar_correo", "args": {}}
        await deps.save_run("waiting_confirmation", {"pendiente": pendiente})

    _FakeRunner.side_effect = _side_effect
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    await run_automation_module.handle(_envelope(automation_id, tenant_id), deps)

    [run_row] = session.automation_runs.values()
    assert run_row["status"] == "waiting_confirmation"
    assert run_row["detalle"]["pendiente"]["name"] == "enviar_correo"


# ---------------------------------------------------------------------------
# automation_scan.handle
# ---------------------------------------------------------------------------


def _install_fake_compute_next_run(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    """Mismo motivo que `fake_runner` para stubear TAMBIÉN el paquete padre
    -reutiliza el que ya dejó `fake_runner` (autouse, corre antes que el
    cuerpo de cada test) en vez de reemplazarlo, para no perder su atributo
    `.runner`."""
    fake_engine_module = types.ModuleType("edecan_automations.engine")
    fake_engine_module.compute_next_run = fn  # type: ignore[attr-defined]
    fake_package = sys.modules.get("edecan_automations") or types.ModuleType("edecan_automations")
    fake_package.engine = fake_engine_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "edecan_automations", fake_package)
    monkeypatch.setitem(sys.modules, "edecan_automations.engine", fake_engine_module)


async def test_scan_encola_vencida_y_reprograma_next_run_at(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    ahora = datetime.now(UTC)
    proxima = ahora + timedelta(days=1)
    session.seed_automation(
        automation_id,
        tenant_id,
        next_run_at=ahora - timedelta(minutes=5),
        trigger=json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY"}),
    )
    _install_fake_compute_next_run(monkeypatch, lambda rrule, after, anchor=None: proxima)

    encolados: list[tuple[str, dict, uuid.UUID]] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert encolados == [("run_automation", {"automation_id": str(automation_id)}, tenant_id)]
    assert session.automations[str(automation_id)]["next_run_at"] == proxima


async def test_scan_pasa_next_run_at_persistido_como_anchor_no_el_now_volatil(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión del bug real: `automation_scan` debe anclar la FASE de la
    recurrencia en el `next_run_at` ya persistido (estable) al recomputar,
    no en el `now` volátil del sondeo — si no, el minuto/segundo deriva sin
    fin de un ciclo a otro (ver docstring de `compute_next_run`). Antes del
    fix este handler llamaba `compute_next_run(rrule, after=now)` sin
    `anchor`, así que este test habría fallado."""
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    anchor_persistido = datetime.now(UTC) - timedelta(minutes=5)
    session.seed_automation(
        automation_id,
        tenant_id,
        next_run_at=anchor_persistido,
        trigger=json.dumps({"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"}),
    )

    llamadas: list[dict[str, Any]] = []

    def _fake_compute_next_run(
        rrule: str, after: datetime, anchor: datetime | None = None
    ) -> datetime:
        llamadas.append({"rrule": rrule, "after": after, "anchor": anchor})
        return after + timedelta(days=1)

    _install_fake_compute_next_run(monkeypatch, _fake_compute_next_run)

    async def fake_enqueue(settings, job_type, payload, tid):
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert len(llamadas) == 1
    assert llamadas[0]["anchor"] == anchor_persistido
    assert llamadas[0]["anchor"] != llamadas[0]["after"]


async def test_scan_sin_vencidas_no_encola_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    # Vence en el futuro: NO debe encolarse.
    session.seed_automation(
        automation_id, tenant_id, next_run_at=datetime.now(UTC) + timedelta(hours=1)
    )
    encolados: list[Any] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert encolados == []


async def test_scan_ignora_automatizaciones_desactivadas(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(
        automation_id,
        tenant_id,
        enabled=False,
        next_run_at=datetime.now(UTC) - timedelta(minutes=5),
    )
    encolados: list[Any] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert encolados == []


async def test_scan_webhook_no_tiene_next_run_at_nunca_se_barre(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    # trigger webhook: next_run_at siempre None (routers/automations.py
    # nunca lo fija para este kind) -> el filtro `IS NOT NULL` ya lo excluye.
    session.seed_automation(
        automation_id,
        tenant_id,
        trigger=json.dumps({"kind": "webhook", "hook_secret": "x"}),
        next_run_at=None,
    )
    encolados: list[Any] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert encolados == []


async def test_scan_rrule_agotada_deja_next_run_at_en_null(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = FakeSession()
    automation_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    session.seed_automation(
        automation_id, tenant_id, next_run_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    _install_fake_compute_next_run(
        monkeypatch, lambda rrule, after, anchor=None: None
    )  # ya no hay más

    async def fake_enqueue(settings, job_type, payload, tid):
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    assert session.automations[str(automation_id)]["next_run_at"] is None


async def test_scan_multiples_tenants_encola_cada_uno(monkeypatch: pytest.MonkeyPatch) -> None:
    session = FakeSession()
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()
    automation_a, automation_b = uuid.uuid4(), uuid.uuid4()
    vencido = datetime.now(UTC) - timedelta(minutes=1)
    session.seed_automation(automation_a, tenant_a, next_run_at=vencido)
    session.seed_automation(automation_b, tenant_b, next_run_at=vencido)
    _install_fake_compute_next_run(
        monkeypatch, lambda rrule, after, anchor=None: datetime.now(UTC) + timedelta(days=1)
    )

    encolados: list[tuple[str, dict, uuid.UUID]] = []

    async def fake_enqueue(settings, job_type, payload, tid):
        encolados.append((job_type, payload, tid))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    deps = make_deps(session_factory=_session_factory(session), vault=lambda s: _FakeLLMVault())

    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=None, type="automation_scan", payload={})
    await automation_scan_module.handle(env, deps)

    tenants_encolados = {tid for _, _, tid in encolados}
    assert tenants_encolados == {tenant_a, tenant_b}


# ---------------------------------------------------------------------------
# scheduler.py — cadencia nueva de automation_scan (60s, ver su docstring
# "Dos cadencias, un solo loop"). No se toca/rompe test_scheduler.py.
# ---------------------------------------------------------------------------


async def test_tick_automations_encola_automation_scan(monkeypatch: pytest.MonkeyPatch) -> None:
    llamadas = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        llamadas.append((job_type, payload, tenant_id))
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)

    from edecan_worker.config import Settings

    await scheduler_module._tick_automations(Settings())

    assert llamadas == [("automation_scan", {}, None)]


async def test_run_forever_no_dispara_automation_scan_en_el_primer_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión explícita del diseño documentado en scheduler.py ("Dos
    cadencias, un solo loop"): el primer tick de `run_forever` NUNCA debe
    encolar `automation_scan` junto con los jobs de v1, o
    `test_run_forever_se_detiene_al_marcar_el_stop_event` (test_scheduler.py,
    fuera de lo que este WP puede tocar) se rompería."""
    import asyncio

    from edecan_worker.config import Settings

    llamadas = []

    async def fake_enqueue(settings, job_type, payload, tenant_id):
        llamadas.append(job_type)
        return uuid.uuid4()

    install_fake_edecan_core_queue(monkeypatch, fake_enqueue)
    monkeypatch.setattr(scheduler_module, "INTERVALO_SEGUNDOS", 3600)

    stop_event = asyncio.Event()

    async def detener_tras_un_tick():
        while len(llamadas) < 1:
            await asyncio.sleep(0)
        stop_event.set()

    await asyncio.wait_for(
        asyncio.gather(
            scheduler_module.run_forever(Settings(), stop_event=stop_event),
            detener_tras_un_tick(),
        ),
        timeout=5,
    )

    assert "automation_scan" not in llamadas
    assert llamadas == list(scheduler_module.JOBS_PERIODICOS)
