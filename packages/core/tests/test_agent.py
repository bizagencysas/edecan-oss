"""`Agent.run_turn` — loop de tool-use (ARCHITECTURE.md §9, §10.7).

`FakeLLMRouter`/`FakeProvider` están *scripteados*: cada llamada a
`.stream()` consume la siguiente respuesta de un guion (p. ej. 1ª respuesta
`tool_use`, 2ª respuesta de puro texto) — así se ejercita el loop completo
sin tocar red ni importar `edecan_llm` (`edecan_core` no depende de él, ver
`llm_types.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from edecan_core.agent import MAX_TOOL_ITERATIONS, Agent
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import (
    ConfirmationRequiredEvent,
    DoneEvent,
    ErrorEvent,
    PersonaConfig,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
)

# --------------------------------------------------------------------------
# Dobles de prueba locales (no importan `edecan_llm`, ver ARCHITECTURE.md §10.1)
# --------------------------------------------------------------------------


@dataclass
class FakeToolCall:
    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class FakeStreamChunk:
    type: str
    text: str | None = None
    tool_call: FakeToolCall | None = None
    usage: FakeUsage | None = None


def text_chunk(text: str) -> FakeStreamChunk:
    return FakeStreamChunk(type="text", text=text)


def tool_call_chunk(call_id: str, name: str, arguments: dict[str, Any]) -> FakeStreamChunk:
    return FakeStreamChunk(type="tool_call", tool_call=FakeToolCall(call_id, name, arguments))


def usage_chunk(input_tokens: int = 1, output_tokens: int = 1) -> FakeStreamChunk:
    return FakeStreamChunk(type="usage", usage=FakeUsage(input_tokens, output_tokens))


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
        self.resolve_calls: list[tuple[str, dict]] = []

    def resolve(self, alias: str, tenant_flags: dict) -> tuple[FakeProvider, str]:
        self.resolve_calls.append((alias, tenant_flags))
        return self._provider, self._model


class FakeTool(Tool):
    def __init__(
        self,
        name: str = "fake_tool",
        description: str = "Herramienta falsa para tests.",
        dangerous: bool = False,
        requires_flags: frozenset[str] = frozenset(),
        result: ToolResult | None = None,
        raises: Exception | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = dangerous
        self.requires_flags = requires_flags
        self._result = result or ToolResult(content="hecho")
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls.append(args)
        if self._raises is not None:
            raise self._raises
        return self._result


class _RaisingLLMRouter:
    def resolve(self, alias: str, tenant_flags: dict) -> tuple[Any, str]:
        raise RuntimeError("boom: no hay proveedor configurado")


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


# --------------------------------------------------------------------------
# Turno sin herramientas
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turno_sin_tools_emite_text_delta_y_done_en_orden():
    provider = FakeProvider([[text_chunk("Hola"), text_chunk(" mundo"), usage_chunk(3, 5)]])
    router = FakeLLMRouter(provider)
    agent = Agent(router, ToolRegistry())

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hola",
        flags={},
    )

    assert [type(e) for e in events] == [TextDeltaEvent, TextDeltaEvent, DoneEvent]
    assert events[0].text == "Hola"
    assert events[1].text == " mundo"
    assert events[2].usage == {"input_tokens": 3, "output_tokens": 5}
    # Se resolvió el LLM una sola vez para todo el turno.
    assert router.resolve_calls == [("principal", {})]


# --------------------------------------------------------------------------
# Turno con una herramienta no peligrosa
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_turno_con_tool_use_luego_texto():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "fake_tool", {"x": 1}), usage_chunk(2, 2)],
            [text_chunk("Listo"), usage_chunk(1, 1)],
        ]
    )
    router = FakeLLMRouter(provider)
    tool = FakeTool(name="fake_tool", result=ToolResult(content="resultado de la tool"))
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(router, registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="usa la tool", flags={}
    )

    assert [type(e) for e in events] == [
        ToolStartEvent,
        ToolEndEvent,
        TextDeltaEvent,
        DoneEvent,
    ]
    assert events[0].name == "fake_tool"
    assert events[0].args == {"x": 1}
    assert events[1].result_preview == "resultado de la tool"
    assert events[2].text == "Listo"
    assert events[3].usage == {"input_tokens": 3, "output_tokens": 3}
    assert tool.calls == [{"x": 1}]

    # La 2ª CompletionRequest ya lleva el turno assistant (tool_use) + tool (resultado).
    second_request = provider.received_requests[1]
    roles = [m.role for m in second_request.messages]
    assert roles == ["user", "assistant", "tool"]


@pytest.mark.asyncio
async def test_tool_desconocida_no_rompe_el_turno():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "no_existe", {})],
            [text_chunk("ok"), usage_chunk()],
        ]
    )
    agent = Agent(FakeLLMRouter(provider), ToolRegistry())

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hola", flags={}
    )

    # Sin `tool_start`/`tool_end` (no hay Tool real que ejecutar), pero el
    # turno continúa a la 2ª vuelta con normalidad.
    assert [type(e) for e in events] == [TextDeltaEvent, DoneEvent]
    second_request = provider.received_requests[1]
    tool_message = second_request.messages[-1]
    assert tool_message.role == "tool"
    assert "Error" in tool_message.content[0]["content"]


# --------------------------------------------------------------------------
# requires_flags — mismo filtro que ToolRegistry.specs(), ahora también al
# EJECUTAR (Hallazgo 1, docs/seguridad-modelo-amenazas.md): antes de este
# fix, `self._registry.get(call.name)` resolvía por nombre contra el
# registro COMPLETO sin volver a chequear `flags`, así que una tool con
# `requires_flags` no satisfecho se ejecutaba igual apenas el modelo pidiera
# su nombre — aunque `specs(flags)` nunca la hubiera anunciado. Mismo
# escenario que ya cubre `test_agent_extra_tools.py::
# test_extra_tool_sin_flag_satisfecho_no_aparece_ni_se_resuelve` para
# `extra_tools` (que siempre filtró bien); esto pinea el camino del registry
# base, que era el que fallaba.
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_con_flag_no_satisfecho_no_se_ejecuta():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "premium_tool", {"x": 1})],
            [text_chunk("ok"), usage_chunk()],
        ]
    )
    tool = FakeTool(name="premium_tool", requires_flags=frozenset({"tools.ads"}))
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    # Confirma la premisa: con flags={}, esta tool NUNCA se habría anunciado.
    assert registry.specs({}) == []

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hazlo", flags={}
    )

    # Mismo tratamiento que una tool desconocida: sin ToolStart/ToolEndEvent,
    # el turno sigue con normalidad, y la tool JAMÁS se ejecuta.
    assert [type(e) for e in events] == [TextDeltaEvent, DoneEvent]
    assert tool.calls == []
    second_request = provider.received_requests[1]
    tool_message = second_request.messages[-1]
    assert tool_message.role == "tool"
    contenido = str(tool_message.content)
    assert "desconocida" in contenido
    assert "premium_tool" in contenido


@pytest.mark.asyncio
async def test_tool_con_flag_satisfecho_se_ejecuta():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "premium_tool", {"x": 1})],
            [text_chunk("ok"), usage_chunk()],
        ]
    )
    tool = FakeTool(name="premium_tool", requires_flags=frozenset({"tools.ads"}))
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hazlo",
        flags={"tools.ads": True},
    )

    assert [type(e) for e in events] == [ToolStartEvent, ToolEndEvent, TextDeltaEvent, DoneEvent]
    assert tool.calls == [{"x": 1}]


@pytest.mark.asyncio
async def test_tool_dangerous_con_flag_no_satisfecho_nunca_pide_confirmacion():
    """Una tool `dangerous` SIN su flag de plan no debe ni siquiera llegar al
    gate de confirmación — si lo hiciera, un humano vería una tarjeta
    `ConfirmationRequiredEvent` pidiéndole aprobar una capacidad que ni
    siquiera es parte de su plan, sin ningún aviso de eso (ver
    docs/seguridad-modelo-amenazas.md, Hallazgo 1: "quien confirma ve el JSON
    crudo de los argumentos, no un aviso de esto no está en tu plan"). Debe
    tratarse como herramienta desconocida, igual que la no-`dangerous`."""
    provider = FakeProvider([[tool_call_chunk("call_1", "premium_danger_tool", {"y": 2})]])
    tool = FakeTool(
        name="premium_danger_tool", dangerous=True, requires_flags=frozenset({"tools.ads"})
    )
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hazlo", flags={}
    )

    assert not any(isinstance(e, ConfirmationRequiredEvent) for e in events)
    assert tool.calls == []


# --------------------------------------------------------------------------
# Gate de confirmación para herramientas `dangerous`
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_dangerous_sin_aprobar_pide_confirmacion_y_termina():
    provider = FakeProvider([[tool_call_chunk("call_1", "danger_tool", {"y": 2})]])
    tool = FakeTool(name="danger_tool", dangerous=True)
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hazlo", flags={}
    )

    assert len(events) == 1
    assert isinstance(events[0], ConfirmationRequiredEvent)
    assert events[0].tool_call_id == "call_1"
    assert events[0].name == "danger_tool"
    assert events[0].args == {"y": 2}
    # La tool NUNCA se ejecutó.
    assert tool.calls == []
    # Y no se llamó al LLM una segunda vez.
    assert len(provider.received_requests) == 1


@pytest.mark.asyncio
async def test_tool_no_peligrosa_junto_a_dangerous_sin_aprobar_no_ejecuta_ninguna():
    """Tool-use paralelo: Anthropic permite varias `tool_use` en un mismo
    turno y nada en este código lo desactiva. Si el modelo pide una tool NO
    peligrosa junto con una `dangerous` sin aprobar en la MISMA tanda,
    ninguna de las dos debe ejecutarse todavía — si la no peligrosa corriera
    igual, su resultado real quedaría huérfano: el turno corta antes de
    llegar al `messages.append` que lo registraría, y `edecan_api` solo
    persiste mensajes en el evento `done`, nunca en `confirmation_required`."""
    provider = FakeProvider(
        [
            [
                tool_call_chunk("call_safe", "safe_tool", {"a": 1}),
                tool_call_chunk("call_danger", "danger_tool", {"b": 2}),
            ]
        ]
    )
    safe_tool = FakeTool(name="safe_tool")
    danger_tool = FakeTool(name="danger_tool", dangerous=True)
    registry = ToolRegistry()
    registry.register(safe_tool)
    registry.register(danger_tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hazlo", flags={}
    )

    assert len(events) == 1
    assert isinstance(events[0], ConfirmationRequiredEvent)
    assert events[0].tool_call_id == "call_danger"
    # Ninguna de las dos tools corrió de verdad: ni la peligrosa (obvio) ni
    # la segura que venía antes en la misma tanda (el bug que se corrige acá).
    assert safe_tool.calls == []
    assert danger_tool.calls == []


@pytest.mark.asyncio
async def test_tool_dangerous_preaprobada_se_ejecuta():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "danger_tool", {"y": 2})],
            [text_chunk("hecho"), usage_chunk()],
        ]
    )
    tool = FakeTool(name="danger_tool", dangerous=True)
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent,
        ctx=_ctx(approved_tool_calls={"call_1"}),
        persona=_persona(),
        history=[],
        user_text="hazlo",
        flags={},
    )

    assert [type(e) for e in events] == [ToolStartEvent, ToolEndEvent, TextDeltaEvent, DoneEvent]
    assert tool.calls == [{"y": 2}]


# --------------------------------------------------------------------------
# Errores
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_error_al_ejecutar_tool_se_convierte_en_tool_result_y_sigue():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "fake_tool", {})],
            [text_chunk("segunda vuelta"), usage_chunk()],
        ]
    )
    tool = FakeTool(name="fake_tool", raises=ValueError("algo salió mal"))
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hola", flags={}
    )

    assert [type(e) for e in events] == [
        ToolStartEvent,
        ToolEndEvent,
        TextDeltaEvent,
        DoneEvent,
    ]
    assert events[1].result_preview == "Error: algo salió mal"


@pytest.mark.asyncio
async def test_excepcion_fatal_emite_un_unico_error_event():
    agent = Agent(_RaisingLLMRouter(), ToolRegistry())

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="hola", flags={}
    )

    assert len(events) == 1
    assert isinstance(events[0], ErrorEvent)
    assert "boom" in events[0].message


# --------------------------------------------------------------------------
# Memoria
# --------------------------------------------------------------------------


class _FakeMemoryStore:
    def __init__(self, hits: list[Any]) -> None:
        self._hits = hits
        self.search_calls: list[tuple[Any, Any, str]] = []

    async def search(self, tenant_id: Any, user_id: Any, query: str, k: int = 8) -> list[Any]:
        self.search_calls.append((tenant_id, user_id, query))
        return self._hits


@dataclass
class _FakeHit:
    content: str


@pytest.mark.asyncio
async def test_memoria_activada_busca_y_alimenta_el_prompt():
    provider = FakeProvider([[text_chunk("ok"), usage_chunk()]])
    store = _FakeMemoryStore([_FakeHit(content="Le gusta el café solo")])
    agent = Agent(FakeLLMRouter(provider), ToolRegistry())

    await _collect(
        agent,
        ctx=_ctx(memory_store=store),
        persona=_persona(memoria_activada=True),
        history=[],
        user_text="¿qué me gusta?",
        flags={},
    )

    assert len(store.search_calls) == 1
    system_prompt = provider.received_requests[0].system
    assert "Le gusta el café solo" in system_prompt


@pytest.mark.asyncio
async def test_memoria_desactivada_no_busca():
    provider = FakeProvider([[text_chunk("ok"), usage_chunk()]])
    store = _FakeMemoryStore([_FakeHit(content="no debería aparecer")])
    agent = Agent(FakeLLMRouter(provider), ToolRegistry())

    await _collect(
        agent,
        ctx=_ctx(memory_store=store),
        persona=_persona(memoria_activada=False),
        history=[],
        user_text="hola",
        flags={},
    )

    assert store.search_calls == []


# --------------------------------------------------------------------------
# Tope de iteraciones
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_para_a_las_max_tool_iterations_y_emite_done():
    respuestas = [
        [tool_call_chunk(f"call_{i}", "fake_tool", {})] for i in range(MAX_TOOL_ITERATIONS)
    ]
    provider = FakeProvider(respuestas)
    tool = FakeTool(name="fake_tool")
    registry = ToolRegistry()
    registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="loop", flags={}
    )

    assert len(provider.received_requests) == MAX_TOOL_ITERATIONS
    assert len(tool.calls) == MAX_TOOL_ITERATIONS
    assert isinstance(events[-1], DoneEvent)
    tipos_tool_start = [e for e in events if isinstance(e, ToolStartEvent)]
    assert len(tipos_tool_start) == MAX_TOOL_ITERATIONS
