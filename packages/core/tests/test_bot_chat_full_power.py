"""Edecán Bots = Grok Bot full: skills + MCP meta always in bot chat registry set."""

from edecan_core.bot_persona import (
    BOT_CHAT_AUTOMATIONS_TOOL_NAMES,
    BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES,
    BOT_CHAT_REMOTE_ALWAYS_TOOL_NAMES,
    BOT_CHAT_SOCIAL_TOOL_NAMES,
)


def test_bots_siempre_llevan_skills_y_mcp_meta() -> None:
    nombres = set(BOT_CHAT_SOCIAL_TOOL_NAMES)
    for requerido in (
        "avisar_avance",
        "buscar_skills",
        "listar_skills",
        "usar_skill",
        "listar_mcp",
        "conectar_mcp",
        "desconectar_mcp",
    ):
        assert requerido in nombres


def test_community_manager_tools_in_remote_always() -> None:
    remote = set(BOT_CHAT_REMOTE_ALWAYS_TOOL_NAMES)
    for nombre in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES:
        assert nombre in remote
    assert "publicar_social" in remote
    assert "crear_contenido_social" in remote
    assert "estado_conectores_sociales" in remote


def test_automations_tools_in_remote_always() -> None:
    remote = set(BOT_CHAT_REMOTE_ALWAYS_TOOL_NAMES)
    for nombre in BOT_CHAT_AUTOMATIONS_TOOL_NAMES:
        assert nombre in remote
    assert "gestionar_automatizacion" in remote
