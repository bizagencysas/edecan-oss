"""Nombres y schemas de las herramientas del toolkit (`ARCHITECTURE.md` §10.14)."""

from __future__ import annotations

from edecan_toolkit import get_all_tools

NOMBRES_PINNED = [
    "crear_recordatorio",
    "listar_recordatorios",
    "agenda_eventos",
    "crear_evento",
    "buscar_correo",
    "enviar_correo",
    "buscar_contactos",
    "gestionar_contacto",
    "registrar_transaccion",
    "resumen_finanzas",
    "consultar_documentos",
    "buscar_web",
    "generar_contenido",
    "publicar_social",
    "usar_computadora",
    "hora_actual",
    "calculadora",
    "configurar_credencial",
    "acceder_codigo_local",
    "diagnosticar_autorreparacion_local",
    "gestionar_autorreparacion_local",
]


def test_get_all_tools_devuelve_las_herramientas_con_los_nombres_pinned():
    nombres = [tool.name for tool in get_all_tools()]
    assert nombres == NOMBRES_PINNED
    assert len(nombres) == 21
    assert len(set(nombres)) == 21  # sin duplicados


def test_cada_tool_tiene_name_description_e_input_schema_validos():
    for tool in get_all_tools():
        assert isinstance(tool.name, str) and tool.name
        assert isinstance(tool.description, str) and tool.description
        assert isinstance(tool.input_schema, dict)
        assert tool.input_schema.get("type") == "object"
        assert isinstance(tool.input_schema.get("properties"), dict)
        assert "linkedin" not in tool.name.lower()
        assert "linkedin" not in tool.description.lower()


def test_solo_las_tools_dangerous_esperadas_lo_son():
    peligrosas = {tool.name for tool in get_all_tools() if tool.dangerous}
    assert peligrosas == {
        "enviar_correo",
        "publicar_social",
        "usar_computadora",
        "configurar_credencial",
        "acceder_codigo_local",
        "gestionar_autorreparacion_local",
    }


def test_flags_requeridos_pinned():
    por_nombre = {tool.name: tool for tool in get_all_tools()}
    assert por_nombre["publicar_social"].requires_flags == frozenset({"connectors.social"})
    assert por_nombre["usar_computadora"].requires_flags == frozenset({"companion"})
    sin_flags = set(NOMBRES_PINNED) - {"publicar_social", "usar_computadora"}
    for nombre in sin_flags:
        assert por_nombre[nombre].requires_flags == frozenset()


def test_required_de_cada_schema_son_propiedades_declaradas():
    for tool in get_all_tools():
        propiedades = set(tool.input_schema.get("properties", {}))
        requeridos = set(tool.input_schema.get("required", []))
        assert requeridos <= propiedades, f"{tool.name}: 'required' con claves no declaradas"
