"""`edecan_core.action_ledger` — Action Ledger y registro de efectos (§63-71).

Cubre dos capas por separado:

1. Las funciones puras del ledger (`record_action_effect` en memoria,
   `last_reversible_action`, `undo_last_action`, `describe_last_actions`) con
   dobles locales.
2. El hook en `Agent._execute_resolved_calls`: una tool que declara
   `Tool.inverse` deja un `ActionEffect` tras una ejecución exitosa; una tool
   sin `inverse` (o que falla) no deja nada. Sin red ni `edecan_llm`
   (`edecan_core` no depende de él — ver `llm_types.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from edecan_core.action_ledger import (
    describe_last_actions,
    last_reversible_action,
    record_action_effect,
    undo_last_action,
)
from edecan_core.agent import Agent, _public_execution_explanation
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import PersonaConfig

# --------------------------------------------------------------------------
# Dobles locales (mismos contratos que `test_agent.py`, sin importar `edecan_llm`)
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


def tool_call_chunk(call_id: str, name: str, arguments: dict[str, Any]) -> FakeStreamChunk:
    return FakeStreamChunk(type="tool_call", tool_call=FakeToolCall(call_id, name, arguments))


def usage_chunk() -> FakeStreamChunk:
    return FakeStreamChunk(type="usage", usage=FakeUsage())


class FakeProvider:
    def __init__(self, responses: list[list[FakeStreamChunk]]) -> None:
        self._responses = list(responses)

    async def stream(self, req: Any):
        script = self._responses.pop(0) if self._responses else []
        for chunk in script:
            yield chunk


class FakeLLMRouter:
    def resolve(self, alias: str, tenant_flags: dict) -> tuple[FakeProvider, str]:
        return self._provider, "fake-model-1"

    def __init__(self, provider: FakeProvider) -> None:
        self._provider = provider


class FakeTool(Tool):
    def __init__(self, *, name: str, inverse: str | None = None, is_error: bool = False) -> None:
        self.name = name
        self.description = f"Herramienta falsa {name}."
        self.input_schema = {"type": "object", "properties": {}}
        self.inverse = inverse
        self._is_error = is_error
        self.calls: list[dict[str, Any]] = []

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        self.calls.append(args)
        if self._is_error:
            return ToolResult(content="Error: falló", is_error=True)
        return ToolResult(content="hecho")


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


class FakeSession:
    """Sesión fake que captura `execute` para verificar la persistencia."""

    def __init__(self) -> None:
        self.executed: list[tuple[Any, dict[str, Any]]] = []

    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        self.executed.append((statement, params))


class BrokenSession:
    async def execute(self, statement: Any, params: dict[str, Any]) -> None:
        raise RuntimeError("base caída")


# --------------------------------------------------------------------------
# Ledger en memoria (sin sesión)
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_y_last_reversible_action():
    tenant_id, user_id = uuid4(), uuid4()
    effect = await record_action_effect(
        tenant_id=tenant_id,
        user_id=user_id,
        tool_name="editar_archivo",
        target=None,
        inverse_op={"description": "restaurar desde backup"},
        reversible=True,
    )
    assert effect.tool_name == "editar_archivo"
    assert effect.reversible is True

    ultima = last_reversible_action(tenant_id, user_id)
    assert ultima is not None
    assert ultima.tool_name == "editar_archivo"


@pytest.mark.asyncio
async def test_last_reversible_action_ignora_no_reversibles_y_otros_usuarios():
    tenant_id, user_a, user_b = uuid4(), uuid4(), uuid4()
    await record_action_effect(tenant_id, user_a, "crear_memoria", None, {}, reversible=False)
    assert last_reversible_action(tenant_id, user_a) is None

    await record_action_effect(tenant_id, user_a, "enviar_correo", None, {}, reversible=True)
    assert last_reversible_action(tenant_id, user_b) is None
    assert last_reversible_action(tenant_id, user_a).tool_name == "enviar_correo"


@pytest.mark.asyncio
async def test_undo_last_action_consume_y_devuelve_inverse_op():
    tenant_id, user_id = uuid4(), uuid4()
    await record_action_effect(
        tenant_id,
        user_id,
        "editar_archivo",
        None,
        {"description": "restaurar desde backup", "file_id": "abc"},
        reversible=True,
    )

    op = undo_last_action(tenant_id, user_id)
    assert op == {"description": "restaurar desde backup", "file_id": "abc"}
    # Consumida: ya no hay nada que deshacer.
    assert undo_last_action(tenant_id, user_id) == {}
    assert last_reversible_action(tenant_id, user_id) is None


@pytest.mark.asyncio
async def test_describe_last_actions_humaniza_y_limita():
    tenant_id, user_id = uuid4(), uuid4()
    await record_action_effect(tenant_id, user_id, "crear_memoria", None, {}, reversible=True)
    await record_action_effect(
        tenant_id, user_id, "editar_archivo", "reporte.pdf", {}, reversible=True
    )
    await record_action_effect(tenant_id, user_id, "leer_web", None, {}, reversible=False)

    resumenes = describe_last_actions(tenant_id, user_id, limit=5)
    assert resumenes == ["editar archivo reporte.pdf", "crear memoria"]

    acotado = describe_last_actions(tenant_id, user_id, limit=1)
    assert acotado == ["editar archivo reporte.pdf"]


def test_explicacion_publica_usa_evidencia_y_redacta_razonamiento_privado():
    explanation = _public_execution_explanation(
        [
            {
                "type": "tool_end",
                "name": "buscar_web",
                "result_preview": "<thinking>secreto</thinking> fuente oficial encontrada",
            }
        ]
    )

    assert explanation is not None
    assert "buscar_web" in explanation
    assert "fuente oficial encontrada" in explanation
    assert "secreto" not in explanation
    assert "<thinking>" not in explanation


# --------------------------------------------------------------------------
# Persistencia (con sesión) — best-effort, nunca lanza
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_record_persiste_con_sesion():
    session = FakeSession()
    tenant_id, user_id = uuid4(), uuid4()
    await record_action_effect(
        tenant_id,
        user_id,
        "editar_archivo",
        "reporte.pdf",
        {"description": "restaurar"},
        reversible=True,
        session=session,
    )
    assert len(session.executed) == 1
    _, params = session.executed[0]
    assert params["tool_name"] == "editar_archivo"
    assert params["target"] == "reporte.pdf"
    assert params["reversible"] is True


@pytest.mark.asyncio
async def test_record_no_lanza_con_sesion_rota():
    tenant_id, user_id = uuid4(), uuid4()
    effect = await record_action_effect(
        tenant_id,
        user_id,
        "crear_memoria",
        None,
        {},
        reversible=True,
        session=BrokenSession(),
    )
    # La persistencia falló, pero el registro en memoria siguió vivo.
    assert effect.tool_name == "crear_memoria"
    assert last_reversible_action(tenant_id, user_id).tool_name == "crear_memoria"


# --------------------------------------------------------------------------
# Hook en el loop del agente
# --------------------------------------------------------------------------


async def _collect(agent: Agent, **kwargs: Any) -> list[Any]:
    return [event async for event in agent.run_turn(**kwargs)]


@pytest.mark.asyncio
async def test_agent_registra_efecto_cuando_la_tool_declara_inverse():
    tool = FakeTool(name="editar_archivo", inverse="restaurar el archivo desde el backup")
    registry = ToolRegistry()
    registry.register(tool)
    provider = FakeProvider(
        [
            [tool_call_chunk("call_1", "editar_archivo", {"path": "a.txt"}), usage_chunk()],
            [FakeStreamChunk(type="text", text="Listo"), usage_chunk()],
        ]
    )
    ctx = _ctx()

    await _collect(
        Agent(FakeLLMRouter(provider), registry),
        ctx=ctx,
        persona=PersonaConfig(),
        history=[],
        user_text="usa la tool",
        flags={},
    )

    assert tool.calls == [{"path": "a.txt"}]
    ultima = last_reversible_action(ctx.tenant_id, ctx.user_id)
    assert ultima is not None
    assert ultima.tool_name == "editar_archivo"
    assert ultima.reversible is True
    assert ultima.inverse_op["description"] == "restaurar el archivo desde el backup"


@pytest.mark.asyncio
async def test_agent_no_registra_tool_sin_inverse_ni_tool_que_falla():
    sin_inverse = FakeTool(name="leer_web")
    falla = FakeTool(name="editar_archivo", inverse="restaurar", is_error=True)
    registry = ToolRegistry()
    registry.register(sin_inverse)
    registry.register(falla)
    ctx = _ctx()

    # Tool sin `inverse`.
    provider_a = FakeProvider(
        [
            [tool_call_chunk("call_1", "leer_web", {}), usage_chunk()],
            [FakeStreamChunk(type="text", text="Ok"), usage_chunk()],
        ]
    )
    await _collect(
        Agent(FakeLLMRouter(provider_a), registry),
        ctx=ctx,
        persona=PersonaConfig(),
        history=[],
        user_text="usa la tool",
        flags={},
    )
    assert last_reversible_action(ctx.tenant_id, ctx.user_id) is None

    # Tool con `inverse` pero que devuelve error.
    provider_b = FakeProvider(
        [
            [tool_call_chunk("call_2", "editar_archivo", {}), usage_chunk()],
            [FakeStreamChunk(type="text", text="Falló"), usage_chunk()],
        ]
    )
    await _collect(
        Agent(FakeLLMRouter(provider_b), registry),
        ctx=ctx,
        persona=PersonaConfig(),
        history=[],
        user_text="usa la tool",
        flags={},
    )
    assert last_reversible_action(ctx.tenant_id, ctx.user_id) is None
