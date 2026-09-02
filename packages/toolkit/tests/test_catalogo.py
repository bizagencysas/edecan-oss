"""Nombres y schemas de las herramientas del toolkit (`ARCHITECTURE.md` §10.14)."""

from __future__ import annotations

from edecan_toolkit import get_all_tools

NOMBRES_PINNED = [
    "crear_recordatorio",
    "listar_recordatorios",
    "guardar_memoria",
    "cambiar_rutina_gym",
    "agenda_eventos",
    "crear_evento",
    "buscar_correo",
    "enviar_correo",
    "buscar_contactos",
    "gestionar_contacto",
    "leer_mensajes_personales",
    "enviar_mensaje_personal",
    "registrar_transaccion",
    "resumen_finanzas",
    "consultar_documentos",
    "buscar_web",
    "generar_contenido",
    "publicar_social",
    "preguntar_al_usuario",
    "probar_notificaciones_push",
    "usar_computadora",
    "hora_actual",
    "calculadora",
    "configurar_credencial",
    "acceder_codigo_local",
    "delegar_al_ide",
    "diagnosticar_autorreparacion_local",
    "gestionar_autorreparacion_local",
    "auditar_seguridad_proyecto",
    "ejecutar_pentestgpt_autorizado",
    "crear_artefactos",
]


def test_get_all_tools_devuelve_las_herramientas_con_los_nombres_pinned():
    nombres = [tool.name for tool in get_all_tools()]
    assert nombres == NOMBRES_PINNED
    assert len(nombres) == 31
    assert len(set(nombres)) == 31  # sin duplicados


def test_cada_tool_tiene_name_description_e_input_schema_validos():
    for tool in get_all_tools():
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.input_schema.get("properties"), dict)


def test_solo_las_tools_dangerous_esperadas_lo_son():
    peligrosas = {tool.name for tool in get_all_tools() if tool.dangerous}
    assert peligrosas == {
        "enviar_correo",
        "enviar_mensaje_personal",
        "publicar_social",
        "usar_computadora",
        "configurar_credencial",
        "acceder_codigo_local",
        "delegar_al_ide",
        "gestionar_autorreparacion_local",
        "ejecutar_pentestgpt_autorizado",
    }


def test_flags_requeridos_pinned():
    por_nombre = {tool.name: tool for tool in get_all_tools()}
    assert por_nombre["cambiar_rutina_gym"].requires_flags == frozenset({"gym"})
    assert por_nombre["publicar_social"].requires_flags == frozenset({"connectors.social"})
    assert por_nombre["usar_computadora"].requires_flags == frozenset({"companion"})
    assert por_nombre["leer_mensajes_personales"].requires_flags == frozenset({"companion"})
    assert por_nombre["enviar_mensaje_personal"].requires_flags == frozenset({"companion"})
    assert por_nombre["probar_notificaciones_push"].requires_flags == frozenset(
        {"notifications.push"}
    )
    sin_flags = set(NOMBRES_PINNED) - {
        "cambiar_rutina_gym",
        "publicar_social",
        "usar_computadora",
        "leer_mensajes_personales",
        "enviar_mensaje_personal",
        "probar_notificaciones_push",
    }
    for nombre in sin_flags:
        assert por_nombre[nombre].requires_flags == frozenset()


def test_required_de_cada_schema_son_propiedades_declaradas():
    for tool in get_all_tools():
        propiedades = set(tool.input_schema.get("properties", {}))
        requeridos = set(tool.input_schema.get("required", []))
        assert requeridos <= propiedades, f"{tool.name}: 'required' con claves no declaradas"
