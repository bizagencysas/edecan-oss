"""`Agent.run_turn(..., extra_tools=...)` — el "seam" que permite fusionar
tools ajenas al `ToolRegistry` compartido SOLO para un turno (ARCHITECTURE.md
§15, nace para las tools MCP bring-your-own de `edecan_mcp.tool_adapter`,
pero el contrato en sí no sabe nada de MCP — usa dobles locales de `Tool`).

Duplica (a propósito, mismo criterio que `apps/worker/tests/
test_run_mission_handler.py` vs. `test_automation_handlers.py`: "cada
archivo de test es autónomo") los dobles mínimos de `test_agent.py`
(`FakeLLMRouter`/`FakeProvider`/`FakeTool`/`_persona`/`_ctx`/`_collect`) en
vez de importarlos — el `pyproject.toml` raíz corre la suite completa con
`--import-mode=importlib` y un `pythonpath` curado que deliberadamente NO
incluye `packages/core/tests` (solo `apps/api/tests`/`apps/worker/tests`,
ver el comentario de ese archivo), así que un `from test_agent import ...`
plano rompe en `make test`/una corrida que combine varios paquetes aunque
funcione al correr `packages/core` en aislado (con el `pyproject.toml`
local de ese paquete, modo "prepend"). Duplicar estos ~30 líneas es más
robusto que depender de en qué config de pytest termina corriendo cada vez.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from edecan_core.agent import Agent
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import (
    ConfirmationRequiredEvent,
    DoneEvent,
    PersonaConfig,
    ToolEndEvent,
    ToolStartEvent,
)

# --------------------------------------------------------------------------
# Dobles de prueba locales — duplicados de `test_agent.py`, ver docstring.
# --------------------------------------------------------------------------


@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeStreamChunk:
    type: str
    text: str | None = None
    tool_call: FakeToolCall | None = None


def text_chunk(text: str) -> FakeStreamChunk:
    return FakeStreamChunk(type="text", text=text)


def tool_call_chunk(call_id: str, name: str, arguments: dict[str, Any]) -> FakeStreamChunk:
    return FakeStreamChunk(type="tool_call", tool_call=FakeToolCall(call_id, name, arguments))


class FakeProvider:
    """Cada llamada a `.stream()` consume la siguiente respuesta scripteada."""

    def __init__(self, responses: list[list[FakeStreamChunk]]) -> None:
        self._responses = list(responses)
        self.received_requests: list[Any] = []

    async def stream(self, req: Any):
        self.received_requests.append(req)
        script = self._responses.pop(0) if self._responses else []
        for chunk in script:
            yield chunk


class FakeLLMRouter:
    def __init__(self, provider: FakeProvider, model: str = "fake-model-1") -> None:
        self._provider = provider
        self._model = model

    def resolve(self, alias: str, tenant_flags: dict) -> tuple[FakeProvider, str]:
        return self._provider, self._model


class FakeTool(Tool):
    def __init__(
        self,
        name: str = "fake_tool",
        description: str = "Herramienta falsa para tests.",
        dangerous: bool = False,
        result: ToolResult | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = dangerous
        self._result = result or ToolResult(content="hecho")
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls.append(args)
        return self._result


def _persona(**overrides: Any) -> PersonaConfig:
    return PersonaConfig(**overrides)


def _ctx(**extras: Any) -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras=extras,
    )


async def _collect(agent: Agent, **kwargs: Any) -> list[Any]:
    return [event async for event in agent.run_turn(**kwargs)]


class ExtraTool(Tool):
    """Doble local de una tool "extra" (p. ej. `mcp_acme_buscar`) — nunca
    registrada en ningún `ToolRegistry` compartido."""

    def __init__(
        self,
        name: str = "mcp_acme_buscar",
        description: str = "Tool remota de prueba.",
        dangerous: bool = False,
        requires_flags: frozenset[str] = frozenset(),
        result: ToolResult | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = dangerous
        self.requires_flags = requires_flags
        self._result = result or ToolResult(content="resultado remoto")
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls.append(args)
        return self._result


# ---------------------------------------------------------------------------
# Aparece en specs / se ejecuta (no-dangerous)
# ---------------------------------------------------------------------------


async def test_extra_tool_aparece_en_los_specs_que_recibe_el_llm() -> None:
    registry = ToolRegistry()
    extra = ExtraTool()
    provider = FakeProvider([[text_chunk("listo")]])
    agent = Agent(FakeLLMRouter(provider), registry)

    await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={},
        extra_tools=[extra],
    )

    assert len(provider.received_requests) == 1
    nombres = {spec.name for spec in provider.received_requests[0].tools}
    assert "mcp_acme_buscar" in nombres


async def test_extra_tool_no_dangerous_se_ejecuta_de_punta_a_punta() -> None:
    registry = ToolRegistry()
    extra = ExtraTool(dangerous=False, result=ToolResult(content="hecho por la extra"))
    provider = FakeProvider(
        [
            [tool_call_chunk("call-1", "mcp_acme_buscar", {"q": "hola"})],
            [text_chunk("listo")],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="busca algo",
        flags={},
        extra_tools=[extra],
    )

    assert extra.calls == [{"q": "hola"}]
    tool_end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert tool_end.name == "mcp_acme_buscar"
    assert "hecho por la extra" in tool_end.result_preview
    assert isinstance(events[-1], DoneEvent)


async def test_tool_end_expone_solo_referencias_seguras_de_archivos() -> None:
    file_id = uuid4()
    manifest_id = uuid4()
    extra = ExtraTool(
        result=ToolResult(
            content="archivo creado",
            data={
                "file_id": str(file_id),
                "filename": "../reporte privado.pdf",
                "mime": "application/pdf",
                "secret": "nunca sale en el evento",
                "manifest": {
                    "file_id": str(manifest_id),
                    "filename": "manifest.json",
                    "mime": "application/json",
                },
            },
        )
    )
    provider = FakeProvider(
        [
            [tool_call_chunk("call-archivo", "mcp_acme_buscar", {})],
            [text_chunk("Aquí tienes el archivo.")],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), ToolRegistry())

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="crea el reporte",
        flags={},
        extra_tools=[extra],
    )

    tool_end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert len(tool_end.artifacts) == 2
    assert tool_end.artifacts[0].file_id == file_id
    assert tool_end.artifacts[0].filename == "reporte privado.pdf"
    assert tool_end.artifacts[0].mime == "application/pdf"
    assert tool_end.artifacts[1].file_id == manifest_id
    assert tool_end.artifacts[1].filename == "manifest.json"
    assert "secret" not in tool_end.model_dump_json()


# ---------------------------------------------------------------------------
# dangerous pide confirmación, y la reanudación (segunda llamada a run_turn
# con approved_tool_calls) la ejecuta — igual que cualquier tool del registry.
# ---------------------------------------------------------------------------


async def test_extra_tool_dangerous_pide_confirmacion() -> None:
    registry = ToolRegistry()
    extra = ExtraTool(dangerous=True)
    provider = FakeProvider([[tool_call_chunk("call-1", "mcp_acme_buscar", {"q": "hola"})]])
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="busca algo peligroso",
        flags={},
        extra_tools=[extra],
    )

    assert len(events) == 1
    confirmacion = events[0]
    assert isinstance(confirmacion, ConfirmationRequiredEvent)
    assert confirmacion.name == "mcp_acme_buscar"
    assert confirmacion.tool_call_id == "call-1"
    assert extra.calls == []  # todavía NO se ejecutó


async def test_extra_tool_dangerous_preaprobada_se_ejecuta() -> None:
    """Mismo patrón que `test_agent.py::test_tool_dangerous_preaprobada_se_ejecuta`
    (el `tool_call_id` que el modelo "genera" en el guion ya viene
    pre-aprobado en `ctx.extras["approved_tool_calls"]` ANTES de que corra
    el turno) — acá con una `extra_tool` en vez de una del registry, para
    probar que el gate de confirmación de `Agent.run_turn` no distingue entre
    ambas: "aplica también al camino de confirmación/reanudación" (docstring
    de `run_turn`)."""
    registry = ToolRegistry()
    extra = ExtraTool(dangerous=True, result=ToolResult(content="ejecutada tras aprobar"))
    provider = FakeProvider(
        [
            [tool_call_chunk("call-1", "mcp_acme_buscar", {"q": "hola"})],
            [text_chunk("listo, ya lo hice")],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(approved_tool_calls={"call-1"}),
        persona=_persona(),
        history=[],
        user_text="busca algo peligroso",
        flags={},
        extra_tools=[extra],
    )

    assert extra.calls == [{"q": "hola"}]
    tool_start = next(e for e in events if isinstance(e, ToolStartEvent))
    assert tool_start.name == "mcp_acme_buscar"
    tool_end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert "ejecutada tras aprobar" in tool_end.result_preview
    assert isinstance(events[-1], DoneEvent)


# ---------------------------------------------------------------------------
# requires_flags — mismo filtro que ToolRegistry.specs()
# ---------------------------------------------------------------------------


async def test_extra_tool_sin_flag_satisfecho_no_aparece_ni_se_resuelve() -> None:
    registry = ToolRegistry()
    extra = ExtraTool(requires_flags=frozenset({"tools.mcp"}))
    provider = FakeProvider(
        [
            [tool_call_chunk("call-1", "mcp_acme_buscar", {})],
            [text_chunk("listo")],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={"tools.mcp": False},
        extra_tools=[extra],
    )

    # No aparece en los specs de la primera llamada al LLM…
    nombres = {spec.name for spec in provider.received_requests[0].tools}
    assert "mcp_acme_buscar" not in nombres

    # …y si el modelo la pide de todos modos, se resuelve como "desconocida":
    # NUNCA se ejecuta (sin ToolStart/ToolEndEvent — mismo tratamiento que
    # cualquier tool inexistente, ver `Agent._run_turn`, rama `tool is None`)
    # y el error queda en el `tool_result` que se le reenvía al LLM en la
    # segunda llamada.
    assert extra.calls == []
    assert not any(isinstance(e, ToolStartEvent) for e in events)
    assert isinstance(events[-1], DoneEvent)

    segunda_llamada = provider.received_requests[1]
    ultimo_mensaje = segunda_llamada.messages[-1]
    assert ultimo_mensaje.role == "tool"
    contenido = str(ultimo_mensaje.content)
    assert "desconocida" in contenido
    assert "mcp_acme_buscar" in contenido


async def test_extra_tool_con_flag_satisfecho_si_aparece() -> None:
    registry = ToolRegistry()
    extra = ExtraTool(requires_flags=frozenset({"tools.mcp"}))
    provider = FakeProvider([[text_chunk("listo")]])
    agent = Agent(FakeLLMRouter(provider), registry)

    await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={"tools.mcp": True},
        extra_tools=[extra],
    )
    nombres = {spec.name for spec in provider.received_requests[0].tools}
    assert "mcp_acme_buscar" in nombres


# ---------------------------------------------------------------------------
# Colisión de nombre: gana el registry base.
# ---------------------------------------------------------------------------


async def test_colision_de_nombre_gana_el_registry_base() -> None:
    registry = ToolRegistry()
    base_tool = FakeTool(name="mismo_nombre", result=ToolResult(content="soy del registry base"))
    registry.register(base_tool)
    extra = ExtraTool(name="mismo_nombre", result=ToolResult(content="soy la extra"))

    provider = FakeProvider(
        [
            [tool_call_chunk("call-1", "mismo_nombre", {})],
            [text_chunk("listo")],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={},
        extra_tools=[extra],
    )

    # Solo una spec con ese nombre en la lista que ve el modelo.
    specs_con_ese_nombre = [
        s for s in provider.received_requests[0].tools if s.name == "mismo_nombre"
    ]
    assert len(specs_con_ese_nombre) == 1
    assert specs_con_ese_nombre[0].description == base_tool.description

    # Y la que se ejecuta es la del registry base, nunca la extra.
    assert base_tool.calls == [{}]
    assert extra.calls == []
    tool_end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert "soy del registry base" in tool_end.result_preview


# ---------------------------------------------------------------------------
# No contamina el registry compartido para el siguiente turno.
# ---------------------------------------------------------------------------


async def test_extra_tools_no_contamina_el_registry_para_el_siguiente_turno() -> None:
    registry = ToolRegistry()
    extra = ExtraTool()
    provider = FakeProvider([[text_chunk("primer turno")], [text_chunk("segundo turno")]])
    agent = Agent(FakeLLMRouter(provider), registry)

    await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={},
        extra_tools=[extra],
    )

    # El registry COMPARTIDO nunca vio `.register()` — ni la conoce ni la
    # ofrece por su cuenta.
    assert registry.get("mcp_acme_buscar") is None
    assert "mcp_acme_buscar" not in {s.name for s in registry.specs({})}

    # Un turno SIGUIENTE sin extra_tools no la ve en absoluto.
    await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hola de nuevo", flags={}
    )
    nombres_segundo_turno = {spec.name for spec in provider.received_requests[1].tools}
    assert "mcp_acme_buscar" not in nombres_segundo_turno


async def test_extra_tools_none_no_rompe_nada() -> None:
    """`extra_tools` es opcional — el default `None` debe comportarse
    exactamente como antes de este cambio (sin `extra_tools` en absoluto)."""
    registry = ToolRegistry()
    provider = FakeProvider([[text_chunk("hola")]])
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hola", flags={}
    )
    assert isinstance(events[-1], DoneEvent)
