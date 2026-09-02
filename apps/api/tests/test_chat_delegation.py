"""Delegación NL del chat principal al workforce (paridad con voz)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from edecan_api.chat_delegation import prepare_chat_delegation, route_voice_intent


@pytest.mark.asyncio
async def test_prepare_chat_delegation_sin_patron_devuelve_texto_original() -> None:
    ctx = SimpleNamespace(tenant_id=uuid4(), user_id=uuid4(), session=None)
    outcome = await prepare_chat_delegation(ctx, "hola, ¿cómo estás?")
    assert outcome.delegated is False
    assert outcome.user_text == "hola, ¿cómo estás?"
    assert outcome.initial_prefix == ""


@pytest.mark.asyncio
async def test_prepare_chat_delegation_encola_mision(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = SimpleNamespace(tenant_id=uuid4(), user_id=uuid4(), session=None)

    async def fake_run(_ctx, _args):
        from edecan_core.tools import ToolResult

        return ToolResult(content="ok", data={"mission_id": str(uuid4())})

    monkeypatch.setattr(
        "edecan_agents.tools.DelegarMisionTool.run",
        AsyncMock(side_effect=fake_run),
    )

    text = "pon a María a investigar la competencia"
    assert route_voice_intent(text) is not None
    outcome = await prepare_chat_delegation(ctx, text)
    assert outcome.delegated is True
    assert "María" in outcome.initial_prefix or "investigar" in outcome.initial_prefix
