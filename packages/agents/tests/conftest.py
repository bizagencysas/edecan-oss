"""Fixtures y dobles de prueba de `edecan_agents` (ver `ARCHITECTURE.md` §10.1:
"los tests no importan paquetes hermanos, usan fakes/stubs").

Ningún fake de este módulo importa `edecan_core`/`edecan_db`/`edecan_llm`/
`edecan_schemas`: todos son duck-typed por `SimpleNamespace`/dataclasses
locales que solo implementan la superficie que `edecan_agents` realmente usa
de cada colaborador — mismo criterio que `packages/toolkit/tests/conftest.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

# ---------------------------------------------------------------------------
# LLM: `FakeProvider`/`FakeLLMRouter` — duck-typed como
# `edecan_llm.router.LLMRouter` (`.resolve(alias, flags) -> (provider, modelo)`)
# y `edecan_llm.base.LLMProvider` (`provider.complete(req) -> respuesta con .text`).
# ---------------------------------------------------------------------------


class FakeProvider:
    """Cada `.complete()` consume la siguiente entrada de `responses`: un
    `str` se envuelve en una respuesta con `.text`; una `Exception` se
    lanza (para simular un fallo de red/proveedor)."""

    def __init__(self, responses: list[str | Exception] | None = None) -> None:
        self._responses = list(responses or [])
        self.requests: list[Any] = []

    async def complete(self, req: Any) -> SimpleNamespace:
        self.requests.append(req)
        if not self._responses:
            return SimpleNamespace(text="")
        outcome = self._responses.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return SimpleNamespace(text=outcome)


class FakeLLMRouter:
    """`.resolve(alias, flags)` siempre devuelve el mismo `(provider, modelo)`
    y registra cada llamada en `resolved` para poder hacer asserts sobre qué
    `flags` se le pasaron."""

    def __init__(self, provider: FakeProvider | None = None, model: str = "modelo-fake") -> None:
        self.provider = provider or FakeProvider()
        self.model = model
        self.resolved: list[tuple[str, dict[str, Any]]] = []

    def resolve(self, alias: str, flags: dict[str, Any]) -> tuple[FakeProvider, str]:
        self.resolved.append((alias, dict(flags)))
        return self.provider, self.model


@pytest.fixture
def make_llm_router():
    def _make(responses: list[str | Exception] | None = None) -> FakeLLMRouter:
        return FakeLLMRouter(provider=FakeProvider(responses=responses))

    return _make


# ---------------------------------------------------------------------------
# ToolRegistry falso — duck-typed como `edecan_core.tools.registry.ToolRegistry`
# (solo `.get(name)`/`.specs(flags)`, la superficie que `RestrictedRegistry`
# y `Agent.run_turn` consumen).
# ---------------------------------------------------------------------------


@dataclass
class FakeTool:
    name: str
    description: str = "tool de prueba"
    input_schema: dict[str, Any] = field(
        default_factory=lambda: {"type": "object", "properties": {}}
    )
    dangerous: bool = False
    requires_flags: frozenset[str] = frozenset()


@dataclass
class FakeToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]


class FakeToolRegistry:
    """Sustituto mínimo de `ToolRegistry`: `tools` es `{name: FakeTool}`."""

    def __init__(self, tools: list[FakeTool] | None = None) -> None:
        self._tools = {t.name: t for t in (tools or [])}

    def get(self, name: str) -> FakeTool | None:
        return self._tools.get(name)

    def specs(self, flags: dict[str, Any]) -> list[FakeToolSpec]:
        def _visible(tool: FakeTool) -> bool:
            return all(bool(flags.get(f)) for f in tool.requires_flags)

        return [
            FakeToolSpec(name=t.name, description=t.description, input_schema=t.input_schema)
            for t in self._tools.values()
            if _visible(t)
        ]


# `FakeTool`/`FakeToolRegistry` se exponen a los archivos de test hermanos SOLO
# vía estas factory-fixtures (nunca `from conftest import FakeTool`, ni desde
# un módulo hermano tipo `agents_fakes.py`): la raíz del workspace pone
# `apps/api/tests` en `pythonpath` para TODA la sesión de pytest (ver
# `pyproject.toml` raíz), pero NO agrega `packages/agents/tests` a esa lista.
# Un `from conftest import FakeTool` hecho desde otro paquete resolvía al
# `conftest.py` de `apps/api/tests` (mismo basename, buscado por `sys.path`) y
# rompía con `ImportError: cannot import name ... from 'conftest'`; un
# hipotético `from agents_fakes import FakeTool` rompería distinto pero igual
# de mal, con `ModuleNotFoundError: No module named 'agents_fakes'`, porque
# ese directorio nunca queda en `sys.path` cuando se corre `uv run pytest`
# desde la raíz (`--import-mode=importlib` no lo agrega, y `pythonpath` no lo
# lista) — solo "parece" funcionar si se corre este paquete aislado
# (`uv run pytest packages/agents/tests` o `cd packages/agents && pytest`),
# porque ahí `packages/agents/pyproject.toml` manda con el modo "prepend" por
# defecto, que sí antepone el directorio del test a `sys.path`. Las
# fixtures de abajo no dependen de `sys.path` en absoluto (pytest las inyecta
# por nombre de parámetro), así que funcionan igual bajo cualquier invocación.
@pytest.fixture
def make_tool():
    def _make(name: str, **kwargs: Any) -> FakeTool:
        return FakeTool(name=name, **kwargs)

    return _make


@pytest.fixture
def make_tool_registry():
    def _make(tools: list[FakeTool] | None = None) -> FakeToolRegistry:
        return FakeToolRegistry(tools)

    return _make


# ---------------------------------------------------------------------------
# `Agent` falso — reemplaza a `edecan_core.agent.Agent` vía
# `monkeypatch.setattr(orchestrator, "Agent", factory)` en los tests de
# `Orchestrator.run`. `factory(llm_router, registry, model_alias=...)` imita
# la firma del constructor real (`edecan_core.agent.Agent.__init__`); cada
# llamada consume el SIGUIENTE guion de `scripts` (una lista de eventos por
# paso, en el orden en que `Orchestrator._run_step` construye un `Agent`
# nuevo por paso).
# ---------------------------------------------------------------------------


@dataclass
class FakeEvent:
    """Duck-typed como cualquier variante de `edecan_schemas.AgentEvent`:
    solo trae los atributos que `orchestrator._run_step` realmente lee de
    cada `type` (el resto quedan con su default, sin usarse)."""

    type: str
    text: str = ""
    tool_call_id: str = ""
    name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    usage: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordedAgentCall:
    ctx: Any
    persona: Any
    history: list[Any]
    user_text: str
    flags: dict[str, Any]


class _FakeAgentInstance:
    def __init__(self, events: list[FakeEvent], calls: list[RecordedAgentCall]) -> None:
        self._events = events
        self._calls = calls

    async def run_turn(self, *, ctx, persona, history, user_text, flags):
        self._calls.append(
            RecordedAgentCall(
                ctx=ctx, persona=persona, history=list(history), user_text=user_text, flags=flags
            )
        )
        for event in self._events:
            yield event


class FakeAgentFactory:
    """Callable duck-typed como la CLASE `Agent`
    (`Agent(llm_router, registry, model_alias=...)`).

    `scripts`: una lista de "guiones" (uno por paso ejecutado, en orden) —
    cada guion es una lista de `FakeEvent` que `run_turn` va a `yield`. Si se
    piden más pasos que guiones dados, el guion sobrante es `[]` (el paso
    "termina" sin texto: útil para tests de presupuesto/budget que no
    necesitan verificar el contenido de cada paso).

    `registries`/`model_aliases`/`calls` quedan disponibles después de correr
    `Orchestrator.run` para verificar qué `RestrictedRegistry`/`model_alias`/
    `persona`/`history` recibió cada paso.
    """

    def __init__(self, scripts: list[list[FakeEvent]]) -> None:
        self._scripts = list(scripts)
        self.registries: list[Any] = []
        self.model_aliases: list[Any] = []
        self.calls: list[RecordedAgentCall] = []

    def __call__(
        self, llm_router: Any, registry: Any, *, model_alias: Any = None
    ) -> _FakeAgentInstance:
        self.registries.append(registry)
        self.model_aliases.append(model_alias)
        script = self._scripts.pop(0) if self._scripts else []
        return _FakeAgentInstance(script, self.calls)


# `FakeEvent`/`FakeAgentFactory` también solo se exponen vía factory-fixture
# (ver el comentario junto a `make_tool` más arriba sobre el choque de
# basename con `apps/api/tests/conftest.py`).
@pytest.fixture
def make_event():
    def _make(type: str, **kwargs: Any) -> FakeEvent:
        return FakeEvent(type=type, **kwargs)

    return _make


@pytest.fixture
def make_agent_factory():
    def _make(scripts: list[list[FakeEvent]]) -> FakeAgentFactory:
        return FakeAgentFactory(scripts)

    return _make


# ---------------------------------------------------------------------------
# `RunDeps` falso — duck-typed (ver `orchestrator.RunDeps`), registra cada
# `save_step`/`save_mission` para poder hacer asserts sobre la secuencia de
# estados persistidos.
# ---------------------------------------------------------------------------


class RecordingDeps:
    def __init__(
        self,
        *,
        session: Any = None,
        settings: Any = None,
        vault: Any = None,
        flags: dict[str, Any] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings if settings is not None else SimpleNamespace()
        self.vault = vault
        self.flags = flags if flags is not None else {}
        self.step_calls: list[dict[str, Any]] = []
        self.mission_calls: list[dict[str, Any]] = []
        self.insert_steps_calls: list[list[dict[str, Any]]] = []

    async def save_step(self, **kwargs: Any) -> None:
        self.step_calls.append(kwargs)

    async def save_mission(self, **kwargs: Any) -> None:
        self.mission_calls.append(kwargs)

    async def insert_steps(self, pasos: list[dict[str, Any]]) -> None:
        """`RunDeps.insert_steps` (WP-V5-05, replan) — registra la lista tal
        cual para que los tests de replan puedan verificar qué pasos nuevos
        se insertaron (`seq`/`agente`/`instruccion`/`depende_de`)."""
        self.insert_steps_calls.append([dict(p) for p in pasos])


@pytest.fixture
def make_deps():
    def _make(**overrides: Any) -> RecordingDeps:
        return RecordingDeps(**overrides)

    return _make


# ---------------------------------------------------------------------------
# `ToolContext`/sesión falsos — para los tests de `tools.py` (`delegar_mision`).
# Mismo espíritu que `packages/toolkit/tests/conftest.py`.
# ---------------------------------------------------------------------------


class FakeResult:
    def __init__(self, rows: list[dict[str, Any]] | None = None, scalar_value: Any = None) -> None:
        self._rows = rows or []
        self._scalar_value = scalar_value

    def mappings(self) -> FakeResult:
        return self

    def first(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def scalar(self) -> Any:
        """`DelegarMisionTool._cupo_disponible` usa `.scalar()` (igual que
        `missions.py::_check_missions_quota`) sobre el `SELECT COUNT(*)` de
        `agent_missions` — configurable vía `FakeSession.scalar_results`."""
        return self._scalar_value


@dataclass
class FakeSession:
    llamadas: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    scalar_results: list[Any] = field(default_factory=list)
    """Cola de valores que `execute()` va devolviendo vía `.scalar()`, uno
    por llamada (`.pop(0)`); `None` si se agota o nunca se cargó (mismo
    default que antes de agregar este campo, no rompe ningún test previo que
    no lo usa)."""

    async def execute(self, stmt: Any, params: dict[str, Any] | None = None) -> FakeResult:
        self.llamadas.append((str(stmt), dict(params or {})))
        scalar_value = self.scalar_results.pop(0) if self.scalar_results else None
        return FakeResult(scalar_value=scalar_value)


@pytest.fixture
def make_session():
    return lambda: FakeSession()


@pytest.fixture
def make_ctx():
    def _make_ctx(
        *,
        session: Any = None,
        settings: Any = None,
        tenant_id: UUID | None = None,
        user_id: UUID | None = None,
        extras: dict[str, Any] | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            tenant_id=tenant_id or uuid4(),
            user_id=user_id or uuid4(),
            session=session if session is not None else FakeSession(),
            settings=settings if settings is not None else SimpleNamespace(),
            llm=None,
            vault=None,
            extras=extras if extras is not None else {},
        )

    return _make_ctx
