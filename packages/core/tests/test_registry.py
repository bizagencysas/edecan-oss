"""`ToolRegistry` — register/get/specs/load_entry_points (ARCHITECTURE.md §10.7)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry
from edecan_schemas import ToolSpec


class _FakeTool(Tool):
    def __init__(
        self,
        name: str = "hora_actual",
        description: str = "Devuelve la hora actual.",
        requires_flags: frozenset[str] = frozenset(),
        dangerous: bool = False,
    ) -> None:
        self.name = name
        self.description = description
        self.input_schema = {"type": "object", "properties": {}}
        self.requires_flags = requires_flags
        self.dangerous = dangerous

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content="ok")


def test_register_y_get():
    registry = ToolRegistry()
    tool = _FakeTool()
    registry.register(tool)
    assert registry.get("hora_actual") is tool
    assert len(registry) == 1
    assert "hora_actual" in registry


def test_get_de_herramienta_inexistente_devuelve_none():
    registry = ToolRegistry()
    assert registry.get("no_existe") is None


def test_register_sobreescribe_si_mismo_nombre():
    registry = ToolRegistry()
    registry.register(_FakeTool(description="versión 1"))
    registry.register(_FakeTool(description="versión 2"))
    assert len(registry) == 1
    assert registry.get("hora_actual").description == "versión 2"


def test_register_acepta_tools_de_plataformas_sujetas_a_politica_propia():
    registry = ToolRegistry()
    registry.register(
        _FakeTool(name="publicar_linkedin", description="Publica mediante una vía autorizada")
    )
    assert len(registry) == 1


def test_specs_devuelve_toolspec_de_edecan_schemas():
    registry = ToolRegistry()
    registry.register(_FakeTool())
    specs = registry.specs({})
    assert specs == [
        ToolSpec(
            name="hora_actual",
            description="Devuelve la hora actual.",
            input_schema={"type": "object", "properties": {}},
        )
    ]


def test_specs_filtra_por_requires_flags():
    registry = ToolRegistry()
    registry.register(_FakeTool(name="siempre", requires_flags=frozenset()))
    registry.register(_FakeTool(name="social", requires_flags=frozenset({"connectors.social"})))
    registry.register(
        _FakeTool(
            name="premium_social",
            requires_flags=frozenset({"connectors.social", "models.premium"}),
        )
    )

    nombres_sin_flags = {spec.name for spec in registry.specs({})}
    assert nombres_sin_flags == {"siempre"}

    nombres_con_social = {spec.name for spec in registry.specs({"connectors.social": True})}
    assert nombres_con_social == {"siempre", "social"}

    nombres_con_ambos = {
        spec.name for spec in registry.specs({"connectors.social": True, "models.premium": True})
    }
    assert nombres_con_ambos == {"siempre", "social", "premium_social"}


def test_specs_trata_flag_false_o_ausente_como_no_satisfecho():
    registry = ToolRegistry()
    registry.register(_FakeTool(name="social", requires_flags=frozenset({"connectors.social"})))
    assert registry.specs({"connectors.social": False}) == []
    assert registry.specs({}) == []


def test_load_entry_points_registra_las_tools_devueltas():
    registry = ToolRegistry()
    fake_tool = _FakeTool(name="desde_entry_point")

    fake_entry_point = SimpleNamespace(name="paquete_x", load=lambda: lambda: [fake_tool])

    target = "edecan_core.tools.registry.entry_points"
    with patch(target, return_value=[fake_entry_point]) as mocked:
        registry.load_entry_points(group="edecan.tools")

    mocked.assert_called_once_with(group="edecan.tools")
    assert registry.get("desde_entry_point") is fake_tool


def test_load_entry_points_usa_el_grupo_default():
    registry = ToolRegistry()
    with patch("edecan_core.tools.registry.entry_points", return_value=[]) as mocked:
        registry.load_entry_points()
    mocked.assert_called_once_with(group="edecan.tools")


def test_load_entry_points_permite_que_la_tool_aplique_su_politica():
    registry = ToolRegistry()
    tool_social = _FakeTool(name="publicar_linkedin", description="Requiere autorización")
    fake_entry_point = SimpleNamespace(name="paquete_social", load=lambda: lambda: [tool_social])

    with patch("edecan_core.tools.registry.entry_points", return_value=[fake_entry_point]):
        registry.load_entry_points()

    assert registry.get("publicar_linkedin") is tool_social


def test_sin_confirmaciones_apaga_el_gate_de_todas_las_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`EDECAN_SIN_CONFIRMACIONES=1` levanta el gate `dangerous` en el registro.

    Un asistente personal, con un solo usuario que acaba de pedir la acción con
    todas sus letras, no gana nada preguntándole lo que él mismo acaba de decir.
    Y un turno detenido esperando una aprobación que la app no alcanzó a pintar
    se ve exactamente igual que un cuelgue: ese fue el caso real que motivó el
    interruptor (`publicar_social` esperando una tarjeta que nunca apareció).

    Se prueba en el REGISTRO y no en una tool concreta a propósito: el valor del
    diseño está en que no se escape ninguna, incluidas las que se agreguen después.
    """
    monkeypatch.setenv("EDECAN_SIN_CONFIRMACIONES", "1")

    registry = ToolRegistry()
    registry.register(_FakeTool(name="publicar_social", dangerous=True))

    tool = registry.get("publicar_social")
    assert tool is not None
    assert tool.dangerous is False


def test_por_defecto_el_gate_dangerous_sigue_puesto(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin la variable, `dangerous` se respeta tal cual.

    Edecán es OSS: quien recién lo instala no espera que un modelo publique en su
    nombre sin un freno visible. Apagarlo es una decisión explícita del dueño del
    despliegue, no el default que se hereda por descuido."""
    monkeypatch.delenv("EDECAN_SIN_CONFIRMACIONES", raising=False)

    registry = ToolRegistry()
    registry.register(_FakeTool(name="publicar_social", dangerous=True))

    tool = registry.get("publicar_social")
    assert tool is not None
    assert tool.dangerous is True
