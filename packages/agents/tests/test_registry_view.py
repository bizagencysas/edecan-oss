"""`edecan_agents.registry_view.RestrictedRegistry` — filtra `get`/`specs` a
`allowed_tools ∩ tools visibles` (defensa en profundidad); "visibles" excluye
`dangerous=True` salvo que `permite_dangerous_con_confirmacion=True`
(WP-V4-05)."""

from __future__ import annotations

import pytest
from edecan_agents.registry_view import RestrictedRegistry


@pytest.fixture
def wrapped(make_tool, make_tool_registry):
    return make_tool_registry(
        [
            make_tool("buscar_web"),
            make_tool("consultar_documentos"),
            make_tool("enviar_correo", dangerous=True),
            make_tool("generar_contenido", requires_flags=frozenset({"models.premium"})),
        ]
    )


def test_get_devuelve_la_tool_si_esta_en_allowed_tools_y_no_es_dangerous(wrapped):
    registry = RestrictedRegistry(wrapped, frozenset({"buscar_web", "consultar_documentos"}))
    tool = registry.get("buscar_web")
    assert tool is not None
    assert tool.name == "buscar_web"


def test_get_devuelve_none_si_la_tool_no_esta_en_allowed_tools(wrapped):
    registry = RestrictedRegistry(wrapped, frozenset({"buscar_web"}))
    assert registry.get("consultar_documentos") is None


def test_get_devuelve_none_para_una_tool_dangerous_aunque_este_en_allowed_tools(wrapped):
    # Defensa en profundidad: aunque `allowed_tools` la incluyera por error,
    # `RestrictedRegistry` la sigue bloqueando porque es `dangerous=True` y
    # `permite_dangerous_con_confirmacion` NO se pasó (default `False`) —
    # comportamiento IDÉNTICO al de antes de que existiera ese parámetro.
    registry = RestrictedRegistry(wrapped, frozenset({"enviar_correo"}))
    assert registry.get("enviar_correo") is None


def test_permite_dangerous_con_confirmacion_false_es_el_default_explicito(wrapped):
    # Pasar el kwarg explícito en `False` debe comportarse igual que omitirlo.
    registry = RestrictedRegistry(
        wrapped, frozenset({"enviar_correo"}), permite_dangerous_con_confirmacion=False
    )
    assert registry.get("enviar_correo") is None


def test_get_devuelve_la_tool_dangerous_si_esta_en_allowed_tools_y_el_flag_esta_activo(wrapped):
    # WP-V4-05: con el flag en `True`, una tool `dangerous` que SÍ está en
    # `allowed_tools` deja de ocultarse — el sub-agente puede pedirla (el
    # gate real, "nunca se ejecuta sin aprobación humana", lo sigue
    # imponiendo `Agent.run_turn`, no esta clase).
    registry = RestrictedRegistry(
        wrapped, frozenset({"enviar_correo"}), permite_dangerous_con_confirmacion=True
    )
    tool = registry.get("enviar_correo")
    assert tool is not None
    assert tool.name == "enviar_correo"
    assert tool.dangerous is True


def test_get_sigue_devolviendo_none_para_dangerous_fuera_de_allowed_tools_pese_al_flag(wrapped):
    # El flag nunca "regala" una tool fuera de `allowed_tools` del perfil.
    registry = RestrictedRegistry(
        wrapped, frozenset({"buscar_web"}), permite_dangerous_con_confirmacion=True
    )
    assert registry.get("enviar_correo") is None


def test_get_con_flag_activo_sigue_devolviendo_none_si_el_registro_no_conoce_la_tool(wrapped):
    registry = RestrictedRegistry(
        wrapped, frozenset({"navegar_web"}), permite_dangerous_con_confirmacion=True
    )
    assert registry.get("navegar_web") is None


def test_get_devuelve_none_si_el_registro_envuelto_no_conoce_la_tool(wrapped):
    # p. ej. el paquete que trae "navegar_web" (edecan_browser) todavía no aterrizó.
    registry = RestrictedRegistry(wrapped, frozenset({"navegar_web"}))
    assert registry.get("navegar_web") is None


def test_specs_solo_incluye_tools_permitidas_y_no_dangerous(wrapped):
    registry = RestrictedRegistry(
        wrapped, frozenset({"buscar_web", "consultar_documentos", "enviar_correo"})
    )
    nombres = {spec.name for spec in registry.specs({})}
    assert nombres == {"buscar_web", "consultar_documentos"}


def test_specs_respeta_requires_flags_del_registro_envuelto(wrapped):
    registry = RestrictedRegistry(wrapped, frozenset({"buscar_web", "generar_contenido"}))
    sin_flag = {spec.name for spec in registry.specs({})}
    assert sin_flag == {"buscar_web"}

    con_flag = {spec.name for spec in registry.specs({"models.premium": True})}
    assert con_flag == {"buscar_web", "generar_contenido"}


def test_specs_vacio_si_allowed_tools_vacio(wrapped):
    registry = RestrictedRegistry(wrapped, frozenset())
    assert registry.specs({}) == []
    assert registry.get("buscar_web") is None


def test_specs_incluye_dangerous_si_esta_en_allowed_tools_y_el_flag_esta_activo(wrapped):
    registry = RestrictedRegistry(
        wrapped,
        frozenset({"buscar_web", "enviar_correo"}),
        permite_dangerous_con_confirmacion=True,
    )
    nombres = {spec.name for spec in registry.specs({})}
    assert nombres == {"buscar_web", "enviar_correo"}


def test_specs_no_incluye_dangerous_con_el_flag_activo_si_no_esta_en_allowed_tools(wrapped):
    # El flag activo no cambia qué CONJUNTO de tools ve el sub-agente, solo
    # si las `dangerous` DE ESE CONJUNTO quedan visibles u ocultas.
    registry = RestrictedRegistry(
        wrapped, frozenset({"buscar_web"}), permite_dangerous_con_confirmacion=True
    )
    nombres = {spec.name for spec in registry.specs({})}
    assert nombres == {"buscar_web"}
