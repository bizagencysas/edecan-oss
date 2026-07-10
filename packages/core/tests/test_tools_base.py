"""`Tool`/`ToolContext`/`ToolResult` — firmas EXACTAS de ARCHITECTURE.md §10.7."""

from __future__ import annotations

from uuid import uuid4

import pytest
from edecan_core.tools.base import Tool, ToolContext, ToolResult


class _EchoTool(Tool):
    name = "echo"
    description = "Repite el argumento 'texto' que recibe."
    input_schema = {"type": "object", "properties": {"texto": {"type": "string"}}}

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content=str(args.get("texto", "")))


def _ctx(**extras: object) -> ToolContext:
    return ToolContext(
        tenant_id=uuid4(),
        user_id=uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras=dict(extras),
    )


def test_tool_result_defaults():
    result = ToolResult(content="ok")
    assert result.data is None
    assert result.requires_confirmation is False


def test_tool_result_con_data_y_confirmacion():
    result = ToolResult(content="ok", data={"x": 1}, requires_confirmation=True)
    assert result.data == {"x": 1}
    assert result.requires_confirmation is True


def test_tool_context_expone_los_campos_pinned():
    tenant_id, user_id = uuid4(), uuid4()
    ctx = ToolContext(
        tenant_id=tenant_id,
        user_id=user_id,
        session="sesion-falsa",
        settings="settings-falso",
        llm="llm-falso",
        vault="vault-falso",
        extras={"clave": "valor"},
    )
    assert ctx.tenant_id == tenant_id
    assert ctx.user_id == user_id
    assert ctx.session == "sesion-falsa"
    assert ctx.settings == "settings-falso"
    assert ctx.llm == "llm-falso"
    assert ctx.vault == "vault-falso"
    assert ctx.extras == {"clave": "valor"}


def test_tool_defaults_no_dangerous_sin_flags():
    tool = _EchoTool()
    assert tool.dangerous is False
    assert tool.requires_flags == frozenset()


def test_tool_no_se_puede_instanciar_sin_implementar_run():
    with pytest.raises(TypeError):

        class _Incompleta(Tool):
            name = "incompleta"
            description = "no implementa run"
            input_schema = {}

        _Incompleta()  # type: ignore[abstract]


@pytest.mark.asyncio
async def test_tool_run_recibe_ctx_y_args():
    tool = _EchoTool()
    ctx = _ctx()
    result = await tool.run(ctx, {"texto": "hola"})
    assert result.content == "hola"
