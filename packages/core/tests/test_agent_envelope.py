"""BOTS-03 — helpers puros del envelope inter-agente (edecan_core.agent_envelope)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from edecan_core.agent_envelope import apply_envelope_restrictions, envelope_expired
from edecan_core.tools.base import Tool, ToolContext, ToolResult
from edecan_core.tools.registry import ToolRegistry


class _ToolStub(Tool):
    def __init__(self, name: str, *, dangerous: bool = False) -> None:
        self.name = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}
        self.dangerous = dangerous

    async def run(self, ctx: ToolContext, args: dict) -> ToolResult:
        return ToolResult(content="")


def _registry(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.register(_ToolStub(name))
    return registry


def test_apply_envelope_restrictions_restringe_a_allowed_tools() -> None:
    registry = _registry("leer_archivo", "escribir_archivo", "navegar_web")
    envelope = {"allowed_tools": ["leer_archivo", "navegar_web"]}

    restringido = apply_envelope_restrictions(registry, envelope)

    names = {tool.name for tool in restringido.all()}
    assert names == {"leer_archivo", "navegar_web"}
    # La tool fuera de allowed_tools queda inaccesible.
    assert restringido.get("escribir_archivo") is None


def test_apply_envelope_restrictions_sin_allowed_tools_devuelve_el_mismo_registry() -> None:
    registry = _registry("leer_archivo")

    resultado = apply_envelope_restrictions(registry, {"deadline": None})

    assert resultado is registry


def test_apply_envelope_restrictions_lista_vacia_no_restringe() -> None:
    """Envelope legacy (allowed_tools vacío) no debe bloquear al receptor."""
    registry = _registry("leer_archivo", "escribir_archivo")

    resultado = apply_envelope_restrictions(registry, {"allowed_tools": []})

    assert resultado is registry


def test_apply_envelope_restrictions_no_amplia() -> None:
    """La intersección jamás añade una tool que el receptor no tenía (BOTS-03)."""
    registry = _registry("leer_archivo")

    restringido = apply_envelope_restrictions(
        registry, {"allowed_tools": ["leer_archivo", "usar_computadora"]}
    )

    assert {tool.name for tool in restringido.all()} == {"leer_archivo"}


def test_envelope_expired_deadline_pasado() -> None:
    now = datetime.now(UTC)
    envelope = {"deadline": (now - timedelta(minutes=5)).isoformat()}

    assert envelope_expired(envelope, now=now) is True


def test_envelope_expired_deadline_futuro() -> None:
    now = datetime.now(UTC)
    envelope = {"deadline": (now + timedelta(minutes=5)).isoformat()}

    assert envelope_expired(envelope, now=now) is False


def test_envelope_expired_sin_deadline() -> None:
    assert envelope_expired({"allowed_tools": ["leer_archivo"]}, now=datetime.now(UTC)) is False
    assert envelope_expired({"deadline": None}, now=datetime.now(UTC)) is False


def test_envelope_expired_acepta_datetime() -> None:
    now = datetime.now(UTC)
    envelope = {"deadline": now - timedelta(seconds=1)}

    assert envelope_expired(envelope, now=now) is True


def test_envelope_expired_deadline_corrupto_falla_abierto() -> None:
    """Un deadline corrupto falla abierto (no bloquea), pero no rompe el helper."""
    assert envelope_expired({"deadline": "no-es-fecha"}, now=datetime.now(UTC)) is False