"""`Agent.run_turn` — loop de tool-use (ARCHITECTURE.md §9, §10.7).

`FakeLLMRouter`/`FakeProvider` están *scripteados*: cada llamada a
`.stream()` consume la siguiente respuesta de un guion (p. ej. 1ª respuesta
`tool_use`, 2ª respuesta de puro texto) — así se ejercita el loop completo
sin tocar red ni importar `edecan_llm` (`edecan_core` no depende de él, ver
`llm_types.py`).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import edecan_core.agent as agent_module
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
    ToolProgressEvent,
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


class SlowFakeTool(FakeTool):
    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls.append(args)
        await asyncio.sleep(0.04)
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


@pytest.mark.asyncio
async def test_cambiar_modelo_no_cambia_las_capacidades_ofrecidas():
    """Las herramientas pertenecen a Edecán, no al proveedor/modelo."""

    registry = ToolRegistry()
    registry.register(FakeTool(name="buscar_web"))
    registry.register(FakeTool(name="crear_documento"))
    provider_a = FakeProvider([[text_chunk("A")]])
    provider_b = FakeProvider([[text_chunk("B")]])

    for provider, model in ((provider_a, "modelo-local"), (provider_b, "modelo-nube")):
        await _collect(
            Agent(FakeLLMRouter(provider, model=model), registry),
            ctx=_ctx(),
            persona=_persona(),
            history=[],
            user_text="Busca información y crea un documento.",
            flags={},
        )

    tools_a = {tool.name for tool in provider_a.received_requests[0].tools}
    tools_b = {tool.name for tool in provider_b.received_requests[0].tools}
    assert tools_a == tools_b == {"buscar_web", "crear_documento"}
    assert provider_a.received_requests[0].model == "modelo-local"
    assert provider_b.received_requests[0].model == "modelo-nube"


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
async def test_frase_natural_compuesta_puede_encadenar_varias_capacidades():
    provider = FakeProvider(
        [
            [
                tool_call_chunk("call_mail", "buscar_correo", {"query": "Ana"}),
                tool_call_chunk(
                    "call_reminder",
                    "crear_recordatorio",
                    {"texto": "Pagar", "cuando": "mañana"},
                ),
            ],
            [text_chunk("Revisé el correo y dejé el recordatorio listo.")],
        ]
    )
    mail = FakeTool(name="buscar_correo")
    reminder = FakeTool(name="crear_recordatorio")
    unrelated = FakeTool(name="registrar_salud")
    registry = ToolRegistry()
    for tool in (mail, reminder, unrelated):
        registry.register(tool)
    # El selector solo entra en juego para catálogos grandes; registries
    # restringidos de hasta 12 tools se conservan completos a propósito.
    for index in range(12):
        registry.register(FakeTool(name=f"irrelevante_{index}"))

    events = await _collect(
        Agent(FakeLLMRouter(provider), registry),
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="Revisa el correo de Ana y recuérdame pagar mañana.",
        flags={},
    )

    assert mail.calls == [{"query": "Ana"}]
    assert reminder.calls == [{"texto": "Pagar", "cuando": "mañana"}]
    assert unrelated.calls == []
    assert [event.name for event in events if isinstance(event, ToolStartEvent)] == [
        "buscar_correo",
        "crear_recordatorio",
    ]
    request = provider.received_requests[0]
    assert {spec.name for spec in request.tools} == {"buscar_correo", "crear_recordatorio"}
    assert "nunca le pidas escoger un módulo" in request.system
    assert "registrar_salud" in request.system  # catálogo para descubrimiento honesto


@pytest.mark.asyncio
async def test_modelo_no_puede_invocar_tool_del_catalogo_que_no_fue_seleccionada():
    provider = FakeProvider(
        [
            [tool_call_chunk("call_injected", "registrar_salud", {"tipo": "agua"})],
            [text_chunk("No ejecuté esa acción.")],
        ]
    )
    health = FakeTool(name="registrar_salud")
    registry = ToolRegistry()
    registry.register(health)
    for index in range(12):
        registry.register(FakeTool(name=f"irrelevante_{index}"))

    await _collect(
        Agent(FakeLLMRouter(provider), registry),
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="¿Qué hora es?",
        flags={},
    )

    assert health.calls == []
    assert provider.received_requests[0].tools == []
    tool_result = provider.received_requests[1].messages[-1].content[0]["content"]
    assert "herramienta desconocida" in tool_result


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
async def test_tool_lenta_emite_progreso_publico_hasta_terminar(monkeypatch):
    monkeypatch.setattr(agent_module, "TOOL_PROGRESS_INTERVAL_SECONDS", 0.01)
    provider = FakeProvider(
        [
            [tool_call_chunk("call_larga", "construir_app", {})],
            [text_chunk("Aplicación lista"), usage_chunk()],
        ]
    )
    registry = ToolRegistry()
    registry.register(SlowFakeTool(name="construir_app"))

    events = await _collect(
        Agent(FakeLLMRouter(provider), registry),
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="Construye la app",
        flags={},
    )

    progress = [event for event in events if isinstance(event, ToolProgressEvent)]
    assert progress
    assert all(event.tool_call_id == "call_larga" for event in progress)
    assert all(event.name == "construir_app" for event in progress)
    assert isinstance(events[0], ToolStartEvent)
    assert any(isinstance(event, ToolEndEvent) for event in events)
    assert isinstance(events[-1], DoneEvent)


@pytest.mark.asyncio
async def test_tool_end_expone_solo_mission_id_valido_para_continuidad_del_chat():
    mission_id = uuid4()
    provider = FakeProvider(
        [
            [tool_call_chunk("mission-call", "delegar_mision", {"objetivo": "Crear la app"})],
            [text_chunk("La misión quedó en curso."), usage_chunk()],
        ]
    )
    registry = ToolRegistry()
    registry.register(
        FakeTool(
            name="delegar_mision",
            result=ToolResult(
                content="Misión creada",
                data={"mission_id": str(mission_id), "internal": "no se expone"},
            ),
        )
    )

    events = await _collect(
        Agent(FakeLLMRouter(provider), registry),
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="Delega una misión para crear la app",
        flags={},
    )

    end = next(event for event in events if isinstance(event, ToolEndEvent))
    assert end.mission_id == mission_id
    assert not hasattr(end, "internal")


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


@pytest.mark.asyncio
async def test_resume_turn_ejecuta_lote_compuesto_una_vez_y_continua_el_llm():
    provider = FakeProvider(
        [
            [
                text_chunk("Voy a hacerlo. "),
                tool_call_chunk("mail_1", "enviar_correo", {"to": "ana@example.com"}),
                tool_call_chunk("doc_1", "revisar_documento", {"id": "doc-7"}),
                tool_call_chunk("rem_1", "crear_recordatorio", {"texto": "Pagar mañana"}),
                usage_chunk(8, 3),
            ],
            [
                text_chunk("Correo enviado, documento revisado y recordatorio creado."),
                usage_chunk(4, 7),
            ],
        ]
    )
    mail = FakeTool(name="enviar_correo", dangerous=True)
    document = FakeTool(name="revisar_documento")
    reminder = FakeTool(name="crear_recordatorio")
    registry = ToolRegistry()
    for tool in (mail, document, reminder):
        registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    first_events = await _collect(
        agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="Envía el correo, revisa el documento y recuérdame pagar mañana.",
        flags={},
    )
    confirmation = next(
        event for event in first_events if isinstance(event, ConfirmationRequiredEvent)
    )
    assert confirmation.pending_turn is not None
    assert mail.calls == document.calls == reminder.calls == []

    resumed = [
        event
        async for event in agent.resume_turn(
            ctx=_ctx(),
            pending=confirmation.pending_turn,
            approved_tool_call_id="mail_1",
            flags={},
        )
    ]

    assert mail.calls == [{"to": "ana@example.com"}]
    assert document.calls == [{"id": "doc-7"}]
    assert reminder.calls == [{"texto": "Pagar mañana"}]
    assert [event.name for event in resumed if isinstance(event, ToolStartEvent)] == [
        "enviar_correo",
        "revisar_documento",
        "crear_recordatorio",
    ]
    assert isinstance(resumed[-1], DoneEvent)
    assert resumed[-1].usage == {"input_tokens": 12, "output_tokens": 10}
    second_request = provider.received_requests[-1]
    assert [message.role for message in second_request.messages][-2:] == ["assistant", "tool"]


@pytest.mark.asyncio
async def test_resume_turn_preflight_pide_cada_dangerous_antes_de_ejecutar_el_lote():
    provider = FakeProvider(
        [
            [
                tool_call_chunk("mail_1", "enviar_correo", {}),
                tool_call_chunk("pay_1", "preparar_pago", {}),
                tool_call_chunk("safe_1", "crear_recordatorio", {}),
            ],
            [text_chunk("Listo")],
        ]
    )
    mail = FakeTool(name="enviar_correo", dangerous=True)
    payment = FakeTool(name="preparar_pago", dangerous=True)
    reminder = FakeTool(name="crear_recordatorio")
    registry = ToolRegistry()
    for tool in (mail, payment, reminder):
        registry.register(tool)
    agent = Agent(FakeLLMRouter(provider), registry)

    first = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="haz las tres", flags={}
    )
    pending = first[0].pending_turn
    assert pending is not None
    second = [
        event
        async for event in agent.resume_turn(
            ctx=_ctx(), pending=pending, approved_tool_call_id="mail_1", flags={}
        )
    ]
    assert len(second) == 1
    assert isinstance(second[0], ConfirmationRequiredEvent)
    assert second[0].tool_call_id == "pay_1"
    assert mail.calls == payment.calls == reminder.calls == []

    final_pending = second[0].pending_turn
    assert final_pending is not None
    final = [
        event
        async for event in agent.resume_turn(
            ctx=_ctx(), pending=final_pending, approved_tool_call_id="pay_1", flags={}
        )
    ]
    assert mail.calls == payment.calls == reminder.calls == [{}]
    assert isinstance(final[-1], DoneEvent)


@pytest.mark.asyncio
async def test_resume_turn_falla_cerrado_si_tool_desaparece_o_pierde_su_flag():
    provider = FakeProvider([[tool_call_chunk("call_1", "premium_danger", {})]])
    tool = FakeTool(
        name="premium_danger", dangerous=True, requires_flags=frozenset({"tools.premium"})
    )
    registry = ToolRegistry()
    registry.register(tool)
    first_agent = Agent(FakeLLMRouter(provider), registry)
    first = await _collect(
        first_agent,
        ctx=_ctx(),
        persona=_persona(),
        history=[],
        user_text="hazlo",
        flags={"tools.premium": True},
    )
    pending = first[0].pending_turn
    assert pending is not None

    downgraded = [
        event
        async for event in first_agent.resume_turn(
            ctx=_ctx(),
            pending=pending,
            approved_tool_call_id="call_1",
            flags={"tools.premium": False},
        )
    ]
    assert len(downgraded) == 1 and isinstance(downgraded[0], ErrorEvent)
    assert tool.calls == []

    removed_agent = Agent(FakeLLMRouter(FakeProvider([])), ToolRegistry())
    removed = [
        event
        async for event in removed_agent.resume_turn(
            ctx=_ctx(),
            pending=pending,
            approved_tool_call_id="call_1",
            flags={"tools.premium": True},
        )
    ]
    assert len(removed) == 1 and isinstance(removed[0], ErrorEvent)
    assert tool.calls == []


@pytest.mark.asyncio
async def test_resume_turn_puede_suspender_una_future_dangerous_sin_repetir_la_primera():
    provider = FakeProvider(
        [
            [tool_call_chunk("mail_1", "enviar_correo", {})],
            [tool_call_chunk("pay_2", "preparar_pago", {"monto": 20})],
        ]
    )
    mail = FakeTool(name="enviar_correo", dangerous=True)
    payment = FakeTool(name="preparar_pago", dangerous=True)
    registry = ToolRegistry()
    registry.register(mail)
    registry.register(payment)
    agent = Agent(FakeLLMRouter(provider), registry)

    first = await _collect(
        agent, ctx=_ctx(), persona=_persona(), history=[], user_text="envía y paga", flags={}
    )
    pending = first[0].pending_turn
    assert pending is not None
    resumed = [
        event
        async for event in agent.resume_turn(
            ctx=_ctx(), pending=pending, approved_tool_call_id="mail_1", flags={}
        )
    ]

    assert mail.calls == [{}]
    assert payment.calls == []
    next_confirmation = resumed[-1]
    assert isinstance(next_confirmation, ConfirmationRequiredEvent)
    assert next_confirmation.tool_call_id == "pay_2"
    assert next_confirmation.pending_turn is not None
    assert next_confirmation.pending_turn.iteration == 1


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
