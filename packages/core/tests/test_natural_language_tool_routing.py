"""P0: routing por frases naturales en español (sin mocks del contrato)."""

from __future__ import annotations

from edecan_core.capability_routing import build_capability_guidance, select_tool_specs
from edecan_schemas import ToolSpec


def _spec(name: str, description: str | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description or f"Capacidad {name}",
        input_schema={"type": "object", "properties": {}},
    )


# Catálogo grande (>12) para activar el router; incluye familias sociales y código.
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
    _spec("acceder_codigo_local", "Repo local: leer, git, shell."),
    _spec("diagnosticar_autorreparacion_local"),
    _spec("reparar_con_skill_local"),
    _spec("gestionar_autorreparacion_local"),
    _spec("delegar_al_ide", "IDE opencode para ingeniería."),
    _spec("crear_factura"),
    _spec("crear_artefactos"),
    _spec("generar_contenido", "Redacta borradores de posts."),
    _spec("publicar_social", "Publica en redes conectadas."),
    _spec("crear_contenido_social"),
    _spec("crear_post_linkedin"),
    _spec("configurar_perfil_social"),
    _spec("estado_conectores_sociales"),
    _spec("generar_imagen"),
    _spec("usar_computadora"),
    _spec("buscar_hoteles"),
    _spec("buscar_vuelos"),
    _spec("preguntar_al_usuario", "Widget de opciones tapables."),
    _spec("avisar_avance"),
    _spec("delegar_mision"),
    _spec("enviar_mensaje_bot"),
    _spec("listar_bots"),
]


def _names_for(phrase: str) -> set[str]:
    return {spec.name for spec in select_tool_specs(LARGE_CATALOG, phrase)}


def test_arregla_el_login_expone_familia_codigo_y_autorreparacion():
    names = _names_for("arregla el login")
    assert "acceder_codigo_local" in names
    assert "delegar_al_ide" in names
    assert {
        "diagnosticar_autorreparacion_local",
        "gestionar_autorreparacion_local",
    } & names


def test_sube_un_post_expone_estado_oauth_redaccion_y_publicacion():
    names = _names_for("sube un post de esto")
    assert "generar_contenido" in names
    assert "publicar_social" in names
    assert "estado_conectores_sociales" in names


def test_mira_que_hay_en_el_repo_incluye_codigo_local():
    names = _names_for("mira qué hay en el repo")
    assert "acceder_codigo_local" in names


def test_guidance_playbook_intent_tool_avance_y_prohibe_menu():
    guidance = build_capability_guidance(
        selected_specs=[_spec("acceder_codigo_local")],
        all_specs=LARGE_CATALOG,
        language="es",
    )
    assert "leer intención" in guidance.lower() or "leer intención →" in guidance
    assert "`avisar_avance`" in guidance
    assert "arregla el login" in guidance
    assert "sube/publica un post" in guidance or "sube/publica" in guidance
    assert "mira qué hay en el repo" in guidance
    assert "preguntar_al_usuario" in guidance
    assert "NUNCA vuelques el" in guidance and "catálogo completo" in guidance
    assert "nunca le pidas escoger un módulo" in guidance


def test_guidance_ambiguedad_widget_no_catalogo_dump():
    guidance = build_capability_guidance(
        selected_specs=[_spec("preguntar_al_usuario"), _spec("publicar_social")],
        all_specs=LARGE_CATALOG,
        language="es",
    )
    assert "2 a 4 opciones" in guidance or "2-4 opciones" in guidance
    assert "dime qué tool usar" in guidance.lower() or "elige herramienta" in guidance.lower()
    assert "Nunca uses el catálogo como menú" in guidance
