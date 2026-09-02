from __future__ import annotations

from edecan_core.capability_routing import (
    _ACTOR_TO_CONSULT_TOOL_NAMES,
    _FAMILIES,
    build_capability_guidance,
    build_slash_command_guidance,
    select_tool_specs,
)
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
    _spec("leer_archivo", "Abre y lee cualquier archivo adjunto."),
    _spec("editar_pdf", "Edita un PDF sin destruir el original."),
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
    _spec("crear_coleccion_visual", "Crea carruseles, campañas y presentaciones visuales."),
    _spec("crear_diseno_visual", "Crea un diseño visual HTML con vista previa segura."),
    _spec("obtener_diseno_visual", "Recupera el HTML actual de un diseño visual."),
    _spec("refinar_diseno_visual", "Refina un diseño visual como versión nueva."),
    _spec("historial_diseno_visual", "Lista versiones de un diseño visual."),
    _spec("exportar_diseno_visual", "Exporta un diseño como HTML, PNG o PDF."),
    _spec("crear_pdf", "Crea un PDF."),
    _spec("crear_presentacion", "Crea PowerPoint."),
    _spec("generar_contenido", "Redacta texto."),
    _spec("publicar_social", "Publica contenido en una red conectada."),
    _spec("crear_contenido_social", "Crea posts e imágenes para redes."),
    _spec("crear_post_linkedin", "Escribe un post de LinkedIn de principio a fin."),
    _spec("capturar_senal_editorial", "Guarda una señal editorial sin escribir un post."),
    _spec("configurar_perfil_social", "Configura la estrategia personal para redes."),
    _spec("generar_imagen", "Genera una imagen original."),
    _spec("usar_estudio_creativo", "Usa Studio para trabajos creativos locales."),
    _spec("usar_estudio_creativo_premium", "Usa Studio para imagen, video y producto."),
    _spec("ver_estudio_creativo", "Muestra las capacidades de Studio."),
    _spec("ver_proyectos_creativos", "Abre proyectos creativos editables."),
    _spec("crear_editar_proyecto_creativo", "Crea o edita un proyecto creativo."),
    _spec("administrar_proyecto_creativo", "Organiza un proyecto creativo."),
    _spec("usar_computadora", "Opera mouse y teclado con confirmación."),
    _spec("probar_notificaciones_push", "Envía una prueba push al teléfono actual."),
    _spec("buscar_hoteles", "Busca hoteles reales."),
    _spec("buscar_vuelos", "Busca vuelos reales."),
    _spec("preparar_reserva", "Prepara un borrador de reserva."),
    _spec("estado_vuelo", "Consulta el estado de un vuelo."),
    _spec("rastrear_paquete", "Rastrea un paquete."),
]


def test_linkedin_permite_consultar_y_cambiar_estrategia_personal():
    names = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            "Quiero cambiar cómo piensas y escribes mis posts de LinkedIn.",
        )
    }
    assert "configurar_perfil_social" in names
    assert "crear_contenido_social" in names


def test_pedir_contenido_sin_decir_post_ofrece_la_tool_que_investiga_sola():
    """Sin la palabra "post" ni "linkedin", el emparejamiento léxico del final no alcanza
    `crear_post_linkedin` y el catálogo se quedaba solo con `crear_contenido_social`, que
    obliga al modelo a escribir el texto sin fuente verificada. La familia la cubre igual."""
    names = {
        spec.name
        for spec in select_tool_specs(ALL_SPECS, "Necesito contenido para mi perfil profesional.")
    }
    assert "crear_post_linkedin" in names


def test_peticion_push_expone_la_prueba_movil_sin_confundirla_con_skills():
    names = {
        spec.name
        for spec in select_tool_specs(
            ALL_SPECS,
            "Envíame una notificación push de prueba a mi iOS.",
        )
    }
    assert "probar_notificaciones_push" in names


def test_slash_fix_expone_autorreparacion_y_contexto_explicito():
    names = {spec.name for spec in select_tool_specs(ALL_SPECS, "/fix el creador de PDF")}
    assert {
        "acceder_codigo_local",
        "diagnosticar_autorreparacion_local",
        "gestionar_autorreparacion_local",
    } <= names
    assert "Diagnostica primero" in build_slash_command_guidance(
        "/fix el creador de PDF", language="es"
    )


def test_slash_changes_es_solo_lectura():
    names = {spec.name for spec in select_tool_specs(ALL_SPECS, "/changes")}
    assert "acceder_codigo_local" in names
    assert "diagnosticar_autorreparacion_local" in names
    assert "gestionar_autorreparacion_local" not in names
    assert "solo lectura" in build_slash_command_guidance("/changes", language="es")


def test_slash_clear_es_solo_un_guardarrail_de_texto_sin_tools_de_autorreparacion():
    """`/clear` de verdad lo intercepta el cliente ANTES de llegar al modelo
    (`POST /{conversation_id}/clear`, sin turno). Esta rama es el guardarraíl
    para un cliente viejo/de terceros que igual lo manda como texto: no debe
    activar ninguna familia (ni la de autorreparación, que comparte prefijo
    de comandos con /fix y /oss) y la guía debe dejar claro que NO se reinició
    nada -- lo contrario sería el mismo bug que /clear existe para arreglar.
    """
    names = {spec.name for spec in select_tool_specs(ALL_SPECS, "/clear")}
    assert "acceder_codigo_local" not in names
    assert "diagnosticar_autorreparacion_local" not in names
    assert "gestionar_autorreparacion_local" not in names

    guidance_es = build_slash_command_guidance("/clear", language="es")
    assert "no se reinició" in guidance_es
    assert "versión más nueva de la app" in guidance_es

    guidance_en = build_slash_command_guidance("/clear", language="en")
    assert "no context was reset" in guidance_en
    assert "app version" in guidance_en


def test_slash_clear_no_rompe_fix_oss_ni_changes():
    """Añadir `/clear` a `_slash_command` es aditivo: los otros tres comandos
    locales siguen resolviendo exactamente lo mismo que antes."""
    assert build_slash_command_guidance("/fix algo", language="es") != ""
    assert "acceder_codigo_local" in {
        spec.name for spec in select_tool_specs(ALL_SPECS, "/fix algo")
    }

    assert "solo lectura" in build_slash_command_guidance("/changes", language="es")

    names_oss = {spec.name for spec in select_tool_specs(ALL_SPECS, "/oss arregla el bug")}
    assert {
        "acceder_codigo_local",
        "diagnosticar_autorreparacion_local",
        "gestionar_autorreparacion_local",
    } <= names_oss


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
        "leer_archivo",
        "crear_recordatorio",
        "configurar_credencial",
    } <= names
    assert "crear_factura" not in names
    assert "crear_documento" not in names
    assert "registrar_salud" not in names
    assert "acceder_codigo_local" not in names
    assert "preparar_pago" not in names
    assert len(names) < len(ALL_SPECS)


def test_pdf_adjunto_ofrece_lectura_y_edicion_reversible():
    selected = select_tool_specs(
        ALL_SPECS,
        "Lee este PDF adjunto, corrige el texto y entrégame la versión editada.",
    )
    names = {spec.name for spec in selected}
    assert {"leer_archivo", "editar_pdf"} <= names


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


def test_guidance_maximiza_capacidades_y_distingue_catalogo_de_tools_operativas():
    guidance = build_capability_guidance(
        selected_specs=[_spec("crear_recordatorio")],
        all_specs=[_spec("crear_recordatorio"), _spec("crear_factura")],
        language="es",
    )

    assert "nunca le pidas escoger un módulo" in guidance
    assert guidance.index("primero diagnostica") < guidance.index("herramientas existentes")
    assert 'No respondas "no puedo"' in guidance
    assert "camino de habilitación concreto" in guidance
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
    assert "concrete enablement path" in guidance
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


def test_landing_visual_usa_design_studio_versionado_en_vez_del_creator_generico() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Crea una landing visual para mi taller y muéstrame una vista previa.",
    )
    names = {spec.name for spec in selected}
    assert {
        "crear_coleccion_visual",
        "crear_diseno_visual",
        "obtener_diseno_visual",
        "refinar_diseno_visual",
        "historial_diseno_visual",
        "exportar_diseno_visual",
    } <= names
    assert "crear_artefactos" not in names


def test_carrusel_visual_expone_coleccion_y_edicion_versionada() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Crea un carrusel visual de 5 lienzos y luego deja que pueda pedir cambios.",
    )
    names = {spec.name for spec in selected}
    assert {
        "crear_coleccion_visual",
        "obtener_diseno_visual",
        "refinar_diseno_visual",
        "historial_diseno_visual",
    } <= names


def test_refinamiento_corto_hereda_la_intencion_de_design_studio() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Haz el título más grande.",
        recent_user_texts=["Crea una landing visual para mi taller."],
    )
    names = {spec.name for spec in selected}
    assert {"obtener_diseno_visual", "refinar_diseno_visual"} <= names


def test_video_publicitario_es_alcanzable_desde_lenguaje_normal() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Créame un video publicitario para este producto y dame dos versiones.",
    )
    names = {spec.name for spec in selected}
    assert {
        "usar_estudio_creativo_premium",
        "ver_estudio_creativo",
    } <= names


def test_imagen_y_edicion_de_foto_exponen_studio_sin_ocultar_vision() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Edita esta foto, mejora el producto y genera otra imagen para el post.",
    )
    names = {spec.name for spec in selected}
    assert {
        "usar_estudio_creativo_premium",
        "analizar_imagen",
        "generar_imagen",
    } <= names


def test_crear_y_publicar_conserva_creator_y_gate_externo() -> None:
    selected = select_tool_specs(ALL_SPECS, "Crea un post y publícalo en X.")
    names = {spec.name for spec in selected}
    assert {"crear_artefactos", "publicar_social", "configurar_credencial"} <= names


def test_publicar_a_secas_ofrece_consultar_que_red_esta_conectada() -> None:
    # Bug real: "publícalo" sin "post"/"social"/"linkedin" en la misma frase solo
    # activaba la tool que actúa (`publicar_social`), nunca la que consulta qué red ya
    # está conectada (`configurar_perfil_social`, accion='ver') -- el modelo publicaba a
    # ciegas o se quedaba sin poder verificar antes. Antes se colaba por casualidad
    # solo cuando el mensaje también traía una palabra de la familia de estudio social.
    selected = select_tool_specs(
        ALL_SPECS, "Publica esto ya, la gente lo está esperando desde hace rato."
    )
    names = {spec.name for spec in selected}
    assert {"publicar_social", "configurar_perfil_social"} <= names


def test_invariante_actuar_requiere_consultar_en_la_misma_familia() -> None:
    """Cada familia bien armada ofrece, junto a una tool que actúa sobre un recurso ya
    existente, la tool que lo consulta (ver regla en el docstring del módulo y en
    `_ACTOR_TO_CONSULT_TOOL_NAMES`). Este test no prueba casos sueltos: recorre TODA
    `_FAMILIES` y falla en cuanto alguien añada o edite una familia con una tool de
    `_ACTOR_TO_CONSULT_TOOL_NAMES` pero sin su(s) consulta(s) correspondiente(s) --
    incluida una familia nueva que todavía no existe hoy.
    """
    for keywords, tool_names in _FAMILIES:
        for actor, required_consults in _ACTOR_TO_CONSULT_TOOL_NAMES.items():
            if actor not in tool_names:
                continue
            faltantes = required_consults - tool_names
            assert not faltantes, (
                f"la familia con keywords {sorted(keywords)!r} ofrece '{actor}' pero le "
                f"falta consultar {sorted(faltantes)!r} en la misma familia"
            )


def test_mapa_actuar_consultar_no_referencia_tools_ausentes_de_las_familias() -> None:
    """Sanity del propio mapa: si se borra o renombra una tool de `_FAMILIES` sin
    actualizar `_ACTOR_TO_CONSULT_TOOL_NAMES`, este test lo detecta en vez de dejar
    entradas muertas que ya no protegen nada."""
    todos_los_nombres = {name for _, tool_names in _FAMILIES for name in tool_names}
    for actor, required_consults in _ACTOR_TO_CONSULT_TOOL_NAMES.items():
        assert actor in todos_los_nombres, f"'{actor}' ya no está en ninguna familia"
        for consult in required_consults:
            assert consult in todos_los_nombres, f"'{consult}' ya no está en ninguna familia"


def test_reservar_hotel_usa_busqueda_nativa_no_la_mac() -> None:
    names = {
        spec.name
        for spec in select_tool_specs(ALL_SPECS, "Resérvame un hotel en Medellín el viernes.")
    }
    assert {"buscar_hoteles", "preparar_reserva"} <= names
    assert "usar_computadora" not in names
    assert "buscar_web" not in names


def test_abrir_una_app_siempre_ofrece_control_del_mac() -> None:
    names = {spec.name for spec in select_tool_specs(ALL_SPECS, "Abre Notes y Calendar.")}
    assert "usar_computadora" in names


def test_linkedin_crea_paquete_multimedia_y_publica_por_conector_oficial() -> None:
    selected = select_tool_specs(
        ALL_SPECS,
        "Crea un post de LinkedIn con su propia imagen y publícalo.",
    )
    names = {spec.name for spec in selected}

    assert {
        "crear_contenido_social",
        "generar_imagen",
        "usar_estudio_creativo_premium",
    } <= names
    assert "crear_artefactos" not in names
    assert "publicar_social" in names
    assert "usar_computadora" not in names
    assert "configurar_credencial" in names
