"""Registry compartido de bots 1:1 (Grok Bot full en chat)."""

from __future__ import annotations

import uuid

import pytest
from edecan_core.bot_persona import BOT_CHAT_SOCIAL_TOOL_NAMES
from edecan_core.bot_registry import (
    BOT_CHAT_ENGINEERING_TOOL_NAMES,
    BOT_CHAT_EXTERNAL_SEND_TOOL_NAMES,
    build_worker_registry,
)
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry


class _ToolStub(Tool):
    def __init__(
        self,
        name: str,
        *,
        dangerous: bool = False,
    ) -> None:
        self.name = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = dangerous

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content="")


def _full_registry() -> ToolRegistry:
    full = ToolRegistry()
    for nombre in (
        *BOT_CHAT_SOCIAL_TOOL_NAMES,
        *BOT_CHAT_ENGINEERING_TOOL_NAMES,
        *BOT_CHAT_EXTERNAL_SEND_TOOL_NAMES,
        "delegar_mision",
        "encargar_a_equipo",
        "usar_computadora",
        "leer_archivo",
        "avisar_avance",
    ):
        dangerous = nombre in {
            "acceder_codigo_local",
            "navegar_web_interactivo",
            "delegar_al_ide",
            "publicar_social",
            "enviar_mensaje_personal",
            "enviar_correo",
            "enviar_mensaje",
            "usar_computadora",
        }
        full.register(_ToolStub(nombre, dangerous=dangerous))
    return full


@pytest.mark.parametrize(
    "companion,local_mode",
    [
        (None, False),
        (object(), False),
        (None, True),
    ],
)
def test_build_worker_registry_siempre_incluye_avisar_avance(
    monkeypatch: pytest.MonkeyPatch, companion: object | None, local_mode: bool
) -> None:
    import edecan_core.bot_registry as reg

    monkeypatch.setattr(reg, "companion_para", lambda _t: companion)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    registry = build_worker_registry(_full_registry(), worker, local_mode=local_mode)
    assert "avisar_avance" in [t.name for t in registry.all()]


def test_build_worker_registry_thin_incluye_ingenieria_dangerous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    monkeypatch.delenv("LOCAL_DESKTOP_CAPABILITY", raising=False)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    registry = build_worker_registry(_full_registry(), worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    assert "acceder_codigo_local" in nombres
    assert "navegar_web_interactivo" in nombres
    assert "generar_contenido" in nombres
    assert "crear_contenido_social" in nombres
    assert "delegar_al_ide" not in nombres


def test_build_worker_registry_thin_incluye_external_send_dangerous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    registry = build_worker_registry(_full_registry(), worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    for nombre in BOT_CHAT_EXTERNAL_SEND_TOOL_NAMES:
        assert nombre in nombres


def test_build_worker_registry_thin_incluye_community_manager_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg
    from edecan_core.bot_persona import BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    full = _full_registry()
    for nombre in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES:
        if full.get(nombre) is None:
            full.register(_ToolStub(nombre, dangerous=nombre == "publicar_social"))
    registry = build_worker_registry(full, worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    for nombre in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES:
        assert nombre in nombres


def test_build_worker_registry_thin_incluye_lectura_adjuntos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg
    from edecan_core.bot_persona import BOT_CHAT_DOCUMENT_TOOL_NAMES

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    full = _full_registry()
    for nombre in BOT_CHAT_DOCUMENT_TOOL_NAMES:
        if full.get(nombre) is None:
            full.register(_ToolStub(nombre))
    registry = build_worker_registry(full, worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    for nombre in BOT_CHAT_DOCUMENT_TOOL_NAMES:
        assert nombre in nombres


def test_build_worker_registry_thin_incluye_mcp_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    import edecan_core.bot_registry as reg

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    registry = build_worker_registry(_full_registry(), worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    for nombre in ("conectar_mcp", "listar_mcp", "desconectar_mcp"):
        assert nombre in nombres


def test_build_worker_registry_thin_incluye_gestionar_automatizacion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg
    from edecan_core.bot_persona import BOT_CHAT_AUTOMATIONS_TOOL_NAMES

    monkeypatch.setattr(reg, "companion_para", lambda _t: None)
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    full = _full_registry()
    for nombre in BOT_CHAT_AUTOMATIONS_TOOL_NAMES:
        if full.get(nombre) is None:
            full.register(_ToolStub(nombre, dangerous=True))
    registry = build_worker_registry(full, worker, local_mode=False)
    nombres = {t.name for t in registry.all()}
    assert "gestionar_automatizacion" in nombres


def test_build_worker_registry_excluye_mision_por_defecto_con_companion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import edecan_core.bot_registry as reg

    monkeypatch.setattr(reg, "companion_para", lambda _t: object())
    worker = {"tenant_id": uuid.uuid4(), "tools": []}
    registry = build_worker_registry(_full_registry(), worker)
    nombres = {t.name for t in registry.all()}
    assert "delegar_mision" not in nombres
    assert "encargar_a_equipo" not in nombres
