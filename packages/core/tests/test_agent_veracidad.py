"""`Agent.run_turn` reparte `ToolResult.fidelidad` (`edecan_core.veracidad`) a
sus dos destinos: el turno `role="tool"` que ve el modelo y
`ToolEndEvent.fidelidad` que llega a la app del dueño por el stream SSE.

Harness local mínimo (duplicado a propósito de `test_agent.py`, mismo
criterio de "duplicación deliberada" que ya usa el resto del repo entre
paquetes/tests hermanos — ver p. ej. `edecan_voice.tools`): este archivo
prueba específicamente el contrato de veracidad, no el resto del loop de
tool-use, así que no necesita el harness completo de `test_agent.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from edecan_core.agent import Agent
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_core.veracidad import Fidelidad, InfoFidelidad
from edecan_schemas import PersonaConfig, ToolEndEvent


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


class FakeProvider:
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
    def __init__(self, name: str, result: ToolResult) -> None:
        self.name = name
        self.description = "Herramienta falsa para el test de veracidad."
        self.input_schema = {"type": "object", "properties": {}}
        self._result = result

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return self._result


def _ctx() -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(), user_id=uuid4(), session=None, settings=None, llm=None,
        vault=None, extras={},
    )


async def _collect(agent: Agent, **kwargs: Any) -> list[Any]:
    return [event async for event in agent.run_turn(**kwargs)]


@pytest.mark.asyncio
async def test_sin_fidelidad_declarada_el_turno_del_modelo_no_cambia():
    """`ToolResult.fidelidad=None` (la mayoría de las tools): ningún aviso se
    antepone, y `ToolEndEvent.fidelidad` es `None` — comportamiento IDÉNTICO
    al de antes de este contrato, para no romper ninguna tool existente."""
    provider = FakeProvider(
        [
            [FakeStreamChunk(type="tool_call", tool_call=FakeToolCall("c1", "mi_tool", {}))],
            [FakeStreamChunk(type="text", text="Listo")],
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeTool(name="mi_tool", result=ToolResult(content="resultado normal")))
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=PersonaConfig(), history=[], user_text="x", flags={}
    )

    end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert end.fidelidad is None
    assert end.motivo_simulado is None

    tool_message = provider.received_requests[1].messages[-1]
    assert tool_message.role == "tool"
    assert tool_message.content[0]["content"] == "resultado normal"


@pytest.mark.asyncio
async def test_fidelidad_simulada_antepone_aviso_al_modelo_y_llega_al_toolendevent():
    """El caso motivador: una tool que usó un proveedor SIMULADO. El modelo
    tiene que ver el aviso en el MISMO mensaje del que saca el resultado
    (no puede "no verlo"), y la app tiene que recibir el dato estructurado
    en `ToolEndEvent`, no solo en un log."""
    info = InfoFidelidad(
        familia="tts",
        fidelidad=Fidelidad.SIMULADO,
        fuente="silencio offline (0.5s)",
        motivo_simulado="falta ELEVENLABS_API_KEY",
    )
    resultado_tool = ToolResult(
        content="Convertí «Hola» a un archivo de audio, pero NO es tu voz.",
        fidelidad=info,
    )
    provider = FakeProvider(
        [
            [FakeStreamChunk(type="tool_call", tool_call=FakeToolCall("c1", "mi_tool", {}))],
            [FakeStreamChunk(type="text", text="Listo")],
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeTool(name="mi_tool", result=resultado_tool))
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=PersonaConfig(), history=[], user_text="x", flags={}
    )

    # Destino 1: la app, vía ToolEndEvent — dato estructurado, no un string
    # que haya que parsear ni un log que muere en el sidecar.
    end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert end.fidelidad == "demo"
    assert end.motivo_simulado == "falta ELEVENLABS_API_KEY"
    # El preview NO se contamina con el aviso (sigue siendo el texto crudo de
    # la tool) — el aviso es un canal aparte, estructurado.
    assert end.result_preview == resultado_tool.content

    # Destino 2: el modelo — el turno role="tool" SÍ lleva el aviso antepuesto,
    # en el mismo mensaje del que el modelo saca el resultado.
    tool_message = provider.received_requests[1].messages[-1]
    assert tool_message.role == "tool"
    contenido_para_el_modelo = tool_message.content[0]["content"]
    assert "FUENTE SIMULADA" in contenido_para_el_modelo
    assert "falta ELEVENLABS_API_KEY" in contenido_para_el_modelo
    assert "no afirmes" in contenido_para_el_modelo.lower()
    assert resultado_tool.content in contenido_para_el_modelo


@pytest.mark.asyncio
async def test_fidelidad_real_no_antepone_ningun_aviso():
    info = InfoFidelidad(familia="tts", fidelidad=Fidelidad.REAL, fuente="ElevenLabs")
    resultado_tool = ToolResult(content="Convertí «Hola» a voz real.", fidelidad=info)
    provider = FakeProvider(
        [
            [FakeStreamChunk(type="tool_call", tool_call=FakeToolCall("c1", "mi_tool", {}))],
            [FakeStreamChunk(type="text", text="Listo")],
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeTool(name="mi_tool", result=resultado_tool))
    agent = Agent(FakeLLMRouter(provider), registry)

    events = await _collect(
        agent, ctx=_ctx(), persona=PersonaConfig(), history=[], user_text="x", flags={}
    )

    end = next(e for e in events if isinstance(e, ToolEndEvent))
    assert end.fidelidad == "live"
    assert end.motivo_simulado is None

    tool_message = provider.received_requests[1].messages[-1]
    assert tool_message.content[0]["content"] == "Convertí «Hola» a voz real."
    assert "is_error" not in tool_message.content[0]


@pytest.mark.asyncio
async def test_tool_result_is_error_llega_al_bloque_tool_result():
    """`ToolResult.is_error=True` (p. ej. `buscar_web` con `consulta` vacía)
    tiene que verse en el bloque `tool_result` que arma `Agent.run_turn`
    (`is_error: true`, el campo nativo que Anthropic lee para tratar la
    llamada como fallida en vez de como una respuesta más) — ver
    `ToolResult.is_error` y `_tool_result_block` en `edecan_core.agent`."""
    resultado_tool = ToolResult(
        content="Error: falta el argumento 'consulta'.", is_error=True
    )
    provider = FakeProvider(
        [
            [FakeStreamChunk(type="tool_call", tool_call=FakeToolCall("c1", "mi_tool", {}))],
            [FakeStreamChunk(type="text", text="Listo")],
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeTool(name="mi_tool", result=resultado_tool))
    agent = Agent(FakeLLMRouter(provider), registry)

    await _collect(agent, ctx=_ctx(), persona=PersonaConfig(), history=[], user_text="x", flags={})

    tool_message = provider.received_requests[1].messages[-1]
    bloque = tool_message.content[0]
    assert bloque["is_error"] is True
    assert bloque["content"] == "Error: falta el argumento 'consulta'."


@pytest.mark.asyncio
async def test_tool_result_sin_is_error_no_agrega_la_clave():
    """Default `is_error=False`: la clave NO aparece en el dict — mismo
    criterio que `fidelidad`/`motivo_simulado`, para no cambiar la forma del
    bloque `tool_result` de ninguna tool existente."""
    resultado_tool = ToolResult(content="Todo bien.")
    provider = FakeProvider(
        [
            [FakeStreamChunk(type="tool_call", tool_call=FakeToolCall("c1", "mi_tool", {}))],
            [FakeStreamChunk(type="text", text="Listo")],
        ]
    )
    registry = ToolRegistry()
    registry.register(FakeTool(name="mi_tool", result=resultado_tool))
    agent = Agent(FakeLLMRouter(provider), registry)

    await _collect(agent, ctx=_ctx(), persona=PersonaConfig(), history=[], user_text="x", flags={})

    tool_message = provider.received_requests[1].messages[-1]
    assert "is_error" not in tool_message.content[0]
