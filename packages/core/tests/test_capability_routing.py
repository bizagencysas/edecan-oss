from __future__ import annotations

from edecan_core.capability_routing import build_capability_guidance, select_tool_specs
from edecan_schemas import ToolSpec


def _spec(name: str, description: str | None = None) -> ToolSpec:
    return ToolSpec(
        name=name,
        description=description or f"Capacidad {name}",
        input_schema={"type": "object", "properties": {}},
    )


ALL_SPECS = [
    _spec("buscar_correo", "Busca correos en Gmail u Outlook."),
    _spec("enviar_correo", "Envía un correo real."),
    _spec("consultar_documentos", "Revisa documentos ya subidos."),
    _spec("crear_documento", "Crea un documento nuevo."),
    _spec("analizar_imagen", "Revisa una imagen ya subida."),
    _spec("crear_recordatorio", "Crea un recordatorio."),
    _spec("listar_recordatorios", "Lista recordatorios pendientes."),
    _spec("configurar_credencial", "Conecta una credencial propia."),
    _spec("buscar_web"),
    _spec("hora_actual"),
    _spec("calculadora"),
    _spec("buscar_skills"),
    _spec("instalar_skill"),
    _spec("listar_skills"),
    _spec("usar_skill"),
    _spec("acceder_codigo_local", "Modifica el repositorio local de Edecán."),
    _spec("diagnosticar_autorreparacion_local", "Diagnostica la instalación sin cambiarla."),
    _spec("reparar_con_skill_local", "Repara usando una skill local."),
    _spec("gestionar_autorreparacion_local", "Repara el núcleo local."),
    _spec("crear_factura"),
    _spec("registrar_salud"),
    _spec("preparar_pago", "Prepara un borrador de pago."),
    _spec("crear_artefactos", "Crea archivos y proyectos reales con manifest."),
    _spec("crear_pdf", "Crea un PDF."),
    _spec("crear_presentacion", "Crea PowerPoint."),
    _spec("generar_contenido", "Redacta texto."),
    _spec("publicar_social", "Publica contenido en una red conectada."),
]


def test_frase_compuesta_selecciona_correo_documento_y_recordatorio_sin_modulos_ajenos():
    selected = select_tool_specs(
        ALL_SPECS,
        "Organiza mis pendientes, responde este correo, revisa el documento "
        "y recuérdame pagar mañana.",
    )
    names = {spec.name for spec in selected}

    assert {
        "buscar_correo",
        "enviar_correo",
        "consultar_documentos",
        "crear_recordatorio",
        "configurar_credencial",
    } <= names
    assert "crear_factura" not in names
    assert "crear_documento" not in names
    assert "registrar_salud" not in names
    assert "acceder_codigo_local" not in names
    assert "preparar_pago" not in names
    assert len(names) < len(ALL_SPECS)


def test_autorreparacion_explicita_habilita_codigo_local_y_escalera_de_skills():
    selected = select_tool_specs(
        ALL_SPECS,
        "Te mandé a hacer esto y dijiste que no podías. Por favor, haz que se pueda.",
    )
    names = {spec.name for spec in selected}

    assert {
        "acceder_codigo_local",
        "diagnosticar_autorreparacion_local",
        "reparar_con_skill_local",
        "gestionar_autorreparacion_local",
    } <= names
    assert {"buscar_skills", "instalar_skill", "usar_skill"} <= names
    assert "enviar_correo" not in names


def test_un_fallo_generico_no_autoriza_editar_codigo():
    selected = select_tool_specs(ALL_SPECS, "Falló el correo, vuelve a intentarlo.")
    names = {spec.name for spec in selected}
    assert "enviar_correo" in names
    assert {
        "acceder_codigo_local",
        "diagnosticar_autorreparacion_local",
        "reparar_con_skill_local",
        "gestionar_autorreparacion_local",
    }.isdisjoint(names)


def test_turno_corto_hereda_intencion_reciente_sin_reabrir_todo_el_catalogo():
    selected = select_tool_specs(
        ALL_SPECS,
        "Sí, hazlo.",
        recent_user_texts=["Busca el correo de Ana y respóndele que llego mañana."],
    )
    names = {spec.name for spec in selected}
    assert {"buscar_correo", "enviar_correo"} <= names
    assert "registrar_salud" not in names


def test_peticion_nueva_larga_no_hereda_familias_de_un_turno_anterior():
    selected = select_tool_specs(
        ALL_SPECS,
        "Quiero revisar este documento adjunto y entender claramente sus puntos principales.",
        recent_user_texts=["Busca el correo de Ana y respóndele que llego mañana."],
    )
    names = {spec.name for spec in selected}
    assert "consultar_documentos" in names
    assert "buscar_correo" not in names
    assert "enviar_correo" not in names


def test_tool_mcp_futura_es_alcanzable_por_nombre_sin_tabla_central():
    specs = [*ALL_SPECS, _spec("notion_buscar_paginas", "Busca páginas del workspace.")]
    selected = select_tool_specs(specs, "Busca en Notion la página del lanzamiento.")
    assert "notion_buscar_paginas" in {spec.name for spec in selected}


def test_guidance_no_promete_cualquier_cosa_y_distingue_catalogo_de_tools_operativas():
    guidance = build_capability_guidance(
        selected_specs=[_spec("crear_recordatorio")],
        all_specs=[_spec("crear_recordatorio"), _spec("crear_factura")],
        language="es",
    )

    assert "nunca le pidas escoger un módulo" in guidance
    assert guidance.index("primero diagnostica") < guidance.index("herramientas existentes")
    assert 'No respondas "no puedo"' in guidance
    assert 'Tampoco prometas que puedes hacer "cualquier cosa"' in guidance
    assert "Herramientas operativas seleccionadas para este turno: crear_recordatorio" in guidance
    assert "crear_factura" in guidance
    assert "Solo puedes ejecutar" in guidance


def test_guidance_english_preserva_los_mismos_limites():
    guidance = build_capability_guidance(
        selected_specs=[_spec("buscar_web")],
        all_specs=[_spec("buscar_web")],
        language="en",
    )
    assert "never ask them to choose a module" in guidance
    assert 'Do not promise "anything"' in guidance
    assert "official gate be the only confirmation" in guidance


def test_creacion_compuesta_usa_un_solo_creator_con_manifest() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Crea un post, Word, PDF, PowerPoint, página web y una app completa.",
    )
    names = {spec.name for spec in selected}
    assert "crear_artefactos" in names
    assert {
        "crear_documento",
        "crear_pdf",
        "crear_presentacion",
        "generar_contenido",
    }.isdisjoint(names)
    assert "publicar_social" not in names


def test_crear_y_publicar_conserva_creator_y_gate_externo() -> None:
    selected = select_tool_specs(ALL_SPECS, "Crea un post y publícalo en X.")
    names = {spec.name for spec in selected}
    assert {"crear_artefactos", "publicar_social", "configurar_credencial"} <= names
