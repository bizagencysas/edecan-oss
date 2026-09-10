"""Registry de tools para chats 1:1 de bots persistentes (Grok Bot).

Centraliza qué herramientas entran en el registro del bot según companion,
modo local y la lista declarada en `worker.tools`. Las tools MCP dinámicas
(`mcp_*`) NO viven en el entry point estático: llegan por `extra_tools` en
cada turno (ver `get_mcp_tools_for_tenant` / `Deps.mcp_tools_para`).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Mapping
from typing import Any

from edecan_core.bot_persona import (
    BOT_CHAT_AUTOMATIONS_TOOL_NAMES,
    BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES,
    BOT_CHAT_DOCUMENT_TOOL_NAMES,
    BOT_CHAT_SOCIAL_TOOL_NAMES,
)
from edecan_core.companion_access import companion_para
from edecan_core.tools import ToolRegistry

logger = logging.getLogger(__name__)

# Chat 1:1: misión/equipo solo si el worker las declara explícitamente.
_MISSION_TOOLS_OPT_IN: frozenset[str] = frozenset({"delegar_mision", "encargar_a_equipo"})

# Ingeniería y entrega en el chat (VPS sin Mac incluido). Pueden ser `dangerous`:
# el turno bot usa aprobación durable (`pending_approvals` + worker_id).
BOT_CHAT_ENGINEERING_TOOL_NAMES: tuple[str, ...] = (
    "acceder_codigo_local",
    "navegar_web",
    "navegar_web_interactivo",
    "extraer_datos_web",
    "buscar_web",
    "generar_contenido",
    "crear_contenido_social",
    "crear_documento",
    "crear_pdf",
    "crear_artefactos",
    "delegar_al_ide",
)

# Publicación/envío externo real: solo estas exigen aprobación durable al enviar.
BOT_CHAT_EXTERNAL_SEND_TOOL_NAMES: tuple[str, ...] = (
    "publicar_social",
    "enviar_mensaje_personal",
    "enviar_correo",
    "enviar_mensaje",
)

# Requieren companion o modo local (Mac presente).
BOT_CHAT_COMPANION_TOOL_NAMES: tuple[str, ...] = (
    "usar_computadora",
    "leer_mensajes_personales",
)


def _ide_alcanzable(*, companion: Any | None) -> bool:
    if bool(os.environ.get("LOCAL_DESKTOP_CAPABILITY", "").strip()):
        return True
    return companion is not None


def build_worker_registry(
    full_registry: ToolRegistry, worker: Mapping[str, Any], *, local_mode: bool = False
) -> ToolRegistry:
    """Arma el registro de tools del bot para un turno de chat o wake headless."""

    tenant_id = worker.get("tenant_id")
    companion = companion_para(tenant_id) if tenant_id is not None else None
    registry = ToolRegistry()
    declared_tools = {str(n) for n in (worker.get("tools") or [])}

    def _registrar_todo() -> None:
        for tool in full_registry.all():
            if tool.name == "delegar_al_ide" and not _ide_alcanzable(companion=companion):
                continue
            if tool.name in _MISSION_TOOLS_OPT_IN and tool.name not in declared_tools:
                continue
            registry.register(tool)

    def _registrar_por_nombre(nombre: str) -> None:
        if nombre == "delegar_al_ide" and not _ide_alcanzable(companion=companion):
            return
        tool = full_registry.get(nombre)
        if tool is not None:
            registry.register(tool)

    if companion is not None:
        _registrar_todo()
        logger.info(
            "bot registry: %s COMPLETA con companion (%d tools)",
            str(worker.get("id") or "")[:8],
            len(registry.all()),
        )
        return registry

    if local_mode:
        _registrar_todo()
        logger.info(
            "bot registry: %s COMPLETA local-mode (%d tools)",
            str(worker.get("id") or "")[:8],
            len(registry.all()),
        )
        return registry

    for tool_name in worker.get("tools") or []:
        tool = full_registry.get(str(tool_name))
        if tool is None or tool.dangerous:
            continue
        registry.register(tool)

    for nombre in BOT_CHAT_SOCIAL_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    for nombre in BOT_CHAT_DOCUMENT_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    for nombre in BOT_CHAT_ENGINEERING_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    for nombre in BOT_CHAT_EXTERNAL_SEND_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    for nombre in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    for nombre in BOT_CHAT_AUTOMATIONS_TOOL_NAMES:
        _registrar_por_nombre(nombre)

    logger.info(
        "bot registry: %s thin-path (%d tools)",
        str(worker.get("id") or "")[:8],
        len(registry.all()),
    )
    return registry


def bot_chat_preapproved_tool_calls(
    *,
    companion: Any | None,
    local_mode: bool = False,
    mcp_tool_names: Iterable[str] = (),
) -> set[str]:
    """Tools de box/Mac/MCP/skills pre-aprobadas en chat bot."""
    from edecan_core.bot_harness import bot_preapproved_tool_calls

    return bot_preapproved_tool_calls(
        companion_present=companion is not None,
        local_mode=local_mode,
        mcp_tool_names=mcp_tool_names,
    )
