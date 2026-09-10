"""Bot chat: MCP dinámico vía extra_tools + invalidación de caché."""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from edecan_core.tools.base import Tool, ToolContext, ToolResult


class _McpToolStub(Tool):
    name = "mcp_demo_ping"
    description = "demo"
    input_schema = {"type": "object", "properties": {}}

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        return ToolResult(content="pong")


@pytest.mark.asyncio
async def test_stream_worker_turn_pasa_extra_tools_mcp(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las tools MCP no viven en el registry estático: deben llegar por extra_tools."""
    from types import SimpleNamespace

    import edecan_api.bot_turn_service as servicio
    import edecan_api.deps as deps
    import edecan_api.routers.conversations as conversaciones
    import edecan_api.routers.perfil as perfil
    from edecan_api.deps import CurrentUser, TenantCtx

    capturado: dict[str, Any] = {}

    class _Agent:
        def __init__(self, *_args, **_kwargs):
            pass

        async def run_turn(self, **kwargs):
            capturado.update(kwargs)
            yield {"type": "done", "usage": {}}

    class _RepoFalso:
        async def list_messages(self, **_kwargs):
            return []

    async def _stream_vacio(**kwargs):
        async for _evento in kwargs.get("events", ()):
            pass
        if False:
            yield None

    async def _get_repo(_session):
        return _RepoFalso()

    monkeypatch.setattr(servicio, "persist_chat_message", AsyncMock())
    monkeypatch.setattr(servicio, "persona_from_worker", lambda _w: object())
    monkeypatch.setattr(servicio, "companion_para", lambda _t: None)
    monkeypatch.setattr(servicio, "build_worker_registry", lambda *_a, **_k: object())
    monkeypatch.setattr(servicio, "load_unified_session", AsyncMock(return_value=None))
    monkeypatch.setattr(deps, "get_repo", _get_repo)
    monkeypatch.setattr(deps, "get_vault", AsyncMock(return_value=object()))
    monkeypatch.setattr(deps, "get_llm_router", AsyncMock(return_value=object()))
    monkeypatch.setattr(deps, "get_redis", lambda _s: None)
    monkeypatch.setattr(
        deps,
        "get_mcp_tools_for_tenant",
        AsyncMock(return_value=[_McpToolStub()]),
    )
    monkeypatch.setattr(conversaciones, "_agent_for_request", lambda *_a, **_k: _Agent())
    monkeypatch.setattr(conversaciones, "_build_ctx", lambda **_k: SimpleNamespace(extras={}))
    monkeypatch.setattr(conversaciones, "_tools_con_pregunta_pendiente", lambda _r: [])
    monkeypatch.setattr(
        conversaciones,
        "_unified_session_for",
        lambda **_k: SimpleNamespace(user_id=None, visual_memory=None, touch=lambda **t: None),
    )
    monkeypatch.setattr(conversaciones, "_stream_agent_events", _stream_vacio)
    monkeypatch.setattr(conversaciones, "get_tool_registry", lambda _r: object())
    monkeypatch.setattr(perfil, "profile_context_for", AsyncMock(return_value=""))

    tenant_id = uuid.uuid4()
    user = CurrentUser(
        user_id=uuid.uuid4(),
        tenant=TenantCtx(tenant_id=tenant_id, plan_key="hosted_basic", flags={}),
    )
    worker = {
        "id": str(uuid.uuid4()),
        "tenant_id": str(tenant_id),
        "display_name": "BotAlpha",
        "tools": [],
        # BOTS-02: sin `autonomy_level` el turno cae a `ask` (solo lectura,
        # fail-closed) y `mcp_demo_ping` — sin operación clasificable — se
        # filtraría. Este test verifica el delivery MCP por extra_tools, no la
        # matriz de autonomía: se declara `full` para que la tool pase.
        "autonomy_level": "full",
    }
    settings = SimpleNamespace(
        BOT_CONTEXT_MAX_MESSAGES=50,
        BOT_CONTEXT_RECENT_MESSAGES=20,
        BOT_CONTEXT_MAX_CHARS=8000,
        EDECAN_LOCAL_MODE=False,
    )

    async for _ in servicio.stream_worker_turn(
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())),
        session=object(),
        user=user,
        settings=settings,
        worker=worker,
        user_text="hola",
        conversation_id=uuid.uuid4(),
    ):
        pass

    extra = capturado.get("extra_tools") or []
    assert any(getattr(t, "name", None) == "mcp_demo_ping" for t in extra)


def test_invalidate_mcp_tools_cache_borra_entrada() -> None:
    from edecan_api.deps import invalidate_mcp_tools_cache

    tenant_id = uuid.uuid4()
    request = MagicMock()
    request.app.state.mcp_tools_cache = {tenant_id: (999999.0, ["x"])}
    invalidate_mcp_tools_cache(request, tenant_id)
    assert tenant_id not in request.app.state.mcp_tools_cache


def test_social_draft_no_es_dangerous_publicar_si() -> None:
    from edecan_creative.social import CrearContenidoSocialTool
    from edecan_toolkit.contenido import GenerarContenidoTool, PublicarSocialTool

    assert not GenerarContenidoTool().dangerous
    assert not CrearContenidoSocialTool().dangerous
    assert PublicarSocialTool().dangerous
