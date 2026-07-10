"""`edecan_api.routers.conversations` × MCP bring-your-own (`ARCHITECTURE.md`
§15, WP-V6-07): `POST .../messages` pasa `extra_tools` (`get_mcp_tools_for_tenant`,
`edecan_api.deps`) a `Agent.run_turn`.

Mismo patrón que `test_conversations.py`: `monkeypatch.setattr(conversations_module,
"Agent", ScriptedAgent)` sobre el símbolo ya importado dentro del propio
router — acá `ScriptedAgent.run_turn` además CAPTURA el `extra_tools` que
recibió, para poder verificar qué le llegó sin tener que ejercitar
`edecan_mcp`/Postgres reales. `conversations_module.get_mcp_tools_for_tenant`
(también el símbolo ya importado en el router, no `edecan_api.deps` directo)
se sustituye por un doble controlado por cada test — no hace falta
`edecan_mcp` instalado ni ninguna llamada de red para esta suite.
"""

from __future__ import annotations

import uuid
from typing import Any

from conftest import auth_headers
from edecan_core.tools.base import Tool, ToolContext, ToolResult


async def _create_conversation(client: Any, headers: dict[str, str]) -> str:
    response = await client.post("/v1/conversations", json={}, headers=headers)
    assert response.status_code == 201
    return response.json()["id"]


class _FakeMCPTool(Tool):
    name = "mcp_acme_buscar"
    description = "[MCP:Acme] Tool remota de prueba."
    input_schema = {"type": "object", "properties": {}}
    dangerous = True
    requires_flags = frozenset({"tools.mcp"})

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        # No se invoca en los tests que solo verifican visibilidad/specs —
        # los que sí ejecutan la tool usan `_FakeMCPToolQueRegistra` (abajo).
        return ToolResult(content="no debería ejecutarse en estos tests")


class _ScriptedAgent:
    """Doble de `edecan_core.agent.Agent`: captura `extra_tools` en la
    instancia del MÓDULO (una lista compartida `capturas`, ver cada test) y
    emite un turno mínimo `text_delta` + `done`."""

    capturas: list[list[Any] | None] = []

    def __init__(self, llm_router: Any, registry: Any) -> None:
        self.llm_router = llm_router
        self.registry = registry

    async def run_turn(
        self, *, ctx, persona, history, user_text, flags, extra_tools=None
    ):
        type(self).capturas.append(extra_tools)
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done", "usage": {}}


async def _post_message(client: Any, headers: dict[str, str], conversation_id: str) -> Any:
    return await client.post(
        f"/v1/conversations/{conversation_id}/messages",
        json={"text": "hola"},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Visible con flag on / invisible con flag off
# ---------------------------------------------------------------------------


async def test_extra_tool_mcp_visible_con_flag_on(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    _ScriptedAgent.capturas = []
    monkeypatch.setattr(conversations_module, "Agent", _ScriptedAgent)

    fake_tool = _FakeMCPTool()

    async def _fake_get_mcp_tools(request: Any, current_user: Any) -> list[Any]:
        return [fake_tool]

    monkeypatch.setattr(conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools)

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    response = await _post_message(client, headers, conversation_id)

    assert response.status_code == 200
    assert _ScriptedAgent.capturas == [[fake_tool]]


async def test_extra_tool_mcp_invisible_con_flag_off(client, monkeypatch) -> None:
    """`get_mcp_tools_for_tenant` real ya devuelve `[]` cuando el tenant no
    tiene el flag `tools.mcp` (ver `edecan_api.deps`) — acá se simula
    exactamente ese resultado ([]"), sin necesitar `edecan_mcp` instalado."""
    import edecan_api.routers.conversations as conversations_module

    _ScriptedAgent.capturas = []
    monkeypatch.setattr(conversations_module, "Agent", _ScriptedAgent)

    async def _fake_get_mcp_tools_vacio(request: Any, current_user: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools_vacio
    )

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_basic")
    conversation_id = await _create_conversation(client, headers)

    response = await _post_message(client, headers, conversation_id)

    assert response.status_code == 200
    assert _ScriptedAgent.capturas == [[]]


# ---------------------------------------------------------------------------
# El chat sobrevive si el builder de tools MCP lanza — ver
# `conversations_module._extra_mcp_tools_or_empty` (segunda capa de defensa,
# redundante a propósito con el fail-open interno de `get_mcp_tools_for_tenant`
# real): incluso si el símbolo importado en el router lanzara, el turno sigue
# funcionando en vez de devolver un 500.
# ---------------------------------------------------------------------------


async def test_chat_sobrevive_si_get_mcp_tools_for_tenant_lanza(client, monkeypatch) -> None:
    import edecan_api.routers.conversations as conversations_module

    _ScriptedAgent.capturas = []
    monkeypatch.setattr(conversations_module, "Agent", _ScriptedAgent)

    async def _fake_get_mcp_tools_que_revienta(request: Any, current_user: Any) -> list[Any]:
        raise RuntimeError("boom: un servidor MCP mal configurado")

    monkeypatch.setattr(
        conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools_que_revienta
    )

    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    response = await _post_message(client, headers, conversation_id)

    # El turno NO se cae con un 500 — sigue, sin las tools MCP de esta vuelta.
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert _ScriptedAgent.capturas == [[]]
    assert "event: message.done" in response.text


async def test_confirm_resuelve_tool_mcp_que_no_esta_en_el_registry_compartido(
    client, monkeypatch, fake_redis
) -> None:
    """`POST .../confirm` NUNCA vuelve a llamar a `Agent.run_turn` (ver el
    docstring del router) — resuelve el `name` pendiente contra el registry
    compartido primero y, si no está ahí (típico de una tool `mcp_*`), contra
    `_extra_mcp_tools_or_empty`. Este test ejercita ESE segundo camino de
    punta a punta: confirmación pendiente en Redis (lo que deja
    `_stream_agent_events` cuando el turno original se detiene en
    `confirmation_required`) → `POST .../confirm` la ejecuta DIRECTO.

    `fake_redis` es el MISMO fixture de `conftest.py` que ya usa la `app` vía
    `dependency_overrides[get_redis]` (pytest cachea la instancia del
    fixture por test, sin importar cuántos consumidores la piden)."""
    import edecan_api.routers.conversations as conversations_module

    ejecuciones: list[dict[str, Any]] = []

    class _FakeMCPToolQueRegistra(_FakeMCPTool):
        async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
            ejecuciones.append(args)
            return ToolResult(content="ejecutada de verdad")

    fake_tool = _FakeMCPToolQueRegistra()

    async def _fake_get_mcp_tools(request: Any, current_user: Any) -> list[Any]:
        return [fake_tool]

    monkeypatch.setattr(conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools)

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call-mcp-1",
        name="mcp_acme_buscar",
        args={"q": "hola"},
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "call-mcp-1", "approved": True},
        headers=headers,
    )

    assert response.status_code == 200
    assert "event: tool.end" in response.text
    assert ejecuciones == [{"q": "hola"}]


async def test_confirm_404_si_ni_el_registry_ni_las_extra_tools_la_tienen(
    client, monkeypatch, fake_redis
) -> None:
    """Si `get_mcp_tools_for_tenant` no la trae tampoco (p. ej. el tenant
    desconectó el servidor MCP entre que pidió la acción y confirmó), sigue
    el mismo `409` que ya cubre `test_conversations.py` para cualquier tool
    que dejó de existir — no un `404`/`500` nuevo."""
    import edecan_api.routers.conversations as conversations_module

    async def _fake_get_mcp_tools_vacio(request: Any, current_user: Any) -> list[Any]:
        return []

    monkeypatch.setattr(
        conversations_module, "get_mcp_tools_for_tenant", _fake_get_mcp_tools_vacio
    )

    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_pro")
    conversation_id = await _create_conversation(client, headers)

    await conversations_module._store_pending_confirmation(
        fake_redis,
        tenant_id=tenant_id,
        conversation_id=uuid.UUID(conversation_id),
        tool_call_id="call-mcp-1",
        name="mcp_ya_no_existe",
        args={},
    )

    response = await client.post(
        f"/v1/conversations/{conversation_id}/confirm",
        json={"tool_call_id": "call-mcp-1", "approved": True},
        headers=headers,
    )

    assert response.status_code == 409
