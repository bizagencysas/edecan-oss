"""P0 OAuth awareness — contrato de honestidad (routing, persona, registry).

Verifica constantes y texto de producción sin mockear `ToolResult`.
La ejecución de tools vive en `packages/toolkit/tests/test_oauth_honesty_contract.py`.
El thin registry VPS se prueba en `test_bot_registry.py`.
"""

from __future__ import annotations

from edecan_core.bot_persona import (
    BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES,
    bot_turn_instructions,
)
from edecan_core.capability_routing import select_tool_specs
from edecan_schemas import ToolSpec


def _spec(name: str) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=f"Capacidad {name}",
        input_schema={"type": "object", "properties": {}},
    )


LARGE_CATALOG = [
    _spec("buscar_correo"),
    _spec("enviar_correo"),
    _spec("consultar_documentos"),
    _spec("leer_archivo"),
    _spec("editar_pdf"),
    _spec("crear_documento"),
    _spec("analizar_imagen"),
    _spec("crear_recordatorio"),
    _spec("listar_recordatorios"),
    _spec("configurar_credencial"),
    _spec("buscar_web"),
    _spec("hora_actual"),
    _spec("calculadora"),
    _spec("buscar_skills"),
    _spec("instalar_skill"),
    _spec("listar_skills"),
    _spec("usar_skill"),
    _spec("acceder_codigo_local"),
    _spec("diagnosticar_autorreparacion_local"),
    _spec("reparar_con_skill_local"),
    _spec("gestionar_autorreparacion_local"),
    _spec("delegar_al_ide"),
    _spec("crear_factura"),
    _spec("crear_artefactos"),
    _spec("generar_contenido"),
    _spec("publicar_social"),
    _spec("crear_contenido_social"),
    _spec("crear_post_linkedin"),
    _spec("configurar_perfil_social"),
    _spec("estado_conectores_sociales"),
    _spec("listar_borradores_sociales"),
    _spec("generar_imagen"),
    _spec("usar_computadora"),
    _spec("buscar_hoteles"),
    _spec("buscar_vuelos"),
    _spec("preguntar_al_usuario"),
    _spec("avisar_avance"),
    _spec("delegar_mision"),
    _spec("enviar_mensaje_bot"),
    _spec("listar_bots"),
]


def test_community_manager_tool_names_incluyen_estado_oauth() -> None:
    assert "estado_conectores_sociales" in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES
    assert "publicar_social" in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES
    assert "listar_borradores_sociales" in BOT_CHAT_COMMUNITY_MANAGER_TOOL_NAMES


def test_routing_publicar_incluye_estado_oauth() -> None:
    names = {
        spec.name
        for spec in select_tool_specs(LARGE_CATALOG, "Publica esto ya en LinkedIn.")
    }
    assert "publicar_social" in names
    assert "estado_conectores_sociales" in names


def test_persona_prohibe_inventar_conexion_oauth() -> None:
    worker = {"name": "cm-1", "display_name": "Community", "purpose": "Redes."}
    text = bot_turn_instructions(worker, language="es")
    assert "PROHIBIDO decir «ya estás conectado»" in text
    assert "estado_conectores_sociales" in text
    assert "Perfil → Conectores" in text
    assert "sin presión" in text.lower() or "sin prisa" in text.lower()
