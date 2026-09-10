"""Regression tests for BOTS-02/H4 policy in headless persistent-agent runs."""

from __future__ import annotations

from types import SimpleNamespace

from edecan_core.bot_harness import (
    MCP_OPERATION_READ,
    mcp_grant_token,
    mcp_tool_definition_version,
)
from edecan_worker.handlers.run_persistent_agent import _headless_approved_tool_calls


def _mcp_tool(name: str, *, server: str = "demo", remote_name: str = "buscar") -> SimpleNamespace:
    version = mcp_tool_definition_version(
        name=remote_name, description="", input_schema={"type": "object"}, server_name=server
    )
    return SimpleNamespace(name=name, definition_version=version, input_schema={"type": "object"})


def test_h4_grant_inyectado_en_wake_headless() -> None:
    """H4: un grant MCP del dueño (approval_policy.mcp_grants) produce su token
    versionado en el set aprobado del run headless, junto a los nombres sandbox."""
    tool = _mcp_tool("mcp_demo_buscar")
    approval_policy = {
        "mcp_grants": [
            {
                "tool_name": "mcp_demo_buscar",
                "operation": "read",
                "definition_version": tool.definition_version,
            }
        ]
    }

    approved = _headless_approved_tool_calls(
        companion=None,
        local_mode=False,
        mcp_tool_names=["mcp_demo_buscar"],
        extra_tools=[tool],
        approval_policy=approval_policy,
    )

    # Sandbox/code sigue pre-aprobado como en el chat.
    assert "acceder_codigo_local" in approved
    assert "avisar_avance" in approved
    # El token MCP versionado y atado a la operación SÍ está (H4).
    assert (
        mcp_grant_token(
            tool_name="mcp_demo_buscar",
            operation=MCP_OPERATION_READ,
            definition_version=tool.definition_version,
        )
        in approved
    )
    # El NOMBRE suelto nunca alcanza (BOTS-06).
    assert "mcp_demo_buscar" not in approved


def test_h4_sin_grant_no_inyecta_token_mcp() -> None:
    """Sin grant del dueño, una tool MCP no recibe token de pre-aprobación."""
    tool = _mcp_tool("mcp_demo_buscar")

    approved = _headless_approved_tool_calls(
        companion=None,
        local_mode=False,
        mcp_tool_names=["mcp_demo_buscar"],
        extra_tools=[tool],
        approval_policy={},
    )

    assert (
        mcp_grant_token(
            tool_name="mcp_demo_buscar",
            operation=MCP_OPERATION_READ,
            definition_version=tool.definition_version,
        )
        not in approved
    )