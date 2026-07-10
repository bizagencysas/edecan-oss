"""`ToolRegistry` — registro de herramientas del agente (ARCHITECTURE.md §10.7).

Rechaza cualquier `Tool` cuyo `name`/`description` mencione la red social
vetada (ARCHITECTURE.md §0.2), filtra `specs()` por los flags de plan del
tenant y descubre herramientas de otros paquetes (`edecan_toolkit`,
`premium/`) vía el grupo de entry points `"edecan.tools"`.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points
from typing import Any

from edecan_schemas import ToolSpec

from .base import Tool

logger = logging.getLogger(__name__)

DEFAULT_ENTRY_POINT_GROUP = "edecan.tools"

_FORBIDDEN_PLATFORM = "linkedin"


class ToolRegistry:
    """Registro en memoria de las `Tool` disponibles para el agente."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Registra `tool` (sobreescribe si ya había una con el mismo `name`).

        Lanza `ValueError` si "linkedin" (sin distinguir mayúsculas/minúsculas)
        aparece en `tool.name` o `tool.description` — guardrail permanente de
        ARCHITECTURE.md §0.2: esta plataforma está excluida en cualquier
        forma, y ninguna herramienta (core, toolkit o premium) puede
        integrarla ni mencionarla.
        """
        haystack = f"{tool.name} {tool.description}".lower()
        if _FORBIDDEN_PLATFORM in haystack:
            raise ValueError(
                f"Herramienta rechazada: '{tool.name}' menciona una plataforma no permitida "
                "(ver ARCHITECTURE.md §0.2)."
            )
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Devuelve la `Tool` registrada con `name`, o `None` si no existe."""
        return self._tools.get(name)

    def specs(self, flags: dict[str, Any]) -> list[ToolSpec]:
        """`ToolSpec` de las herramientas ofrecibles al modelo dado `flags`.

        Una `Tool` se incluye solo si TODOS sus `requires_flags` están
        presentes en `flags` con un valor "verdadero" (`True`, o cualquier
        valor válido no-cero/no-vacío — así también sirve para requerir un
        límite entero distinto de cero, no solo flags booleanos). Sin
        `requires_flags` (el default), la herramienta siempre se incluye.
        """
        return [
            ToolSpec(name=tool.name, description=tool.description, input_schema=tool.input_schema)
            for tool in self._tools.values()
            if _flags_satisfechos(tool.requires_flags, flags)
        ]

    def load_entry_points(self, group: str = DEFAULT_ENTRY_POINT_GROUP) -> None:
        """Descubre y registra todas las `Tool` expuestas por `group`.

        Cada entry point del grupo (declarado en el `pyproject.toml` de otro
        paquete, p. ej. `[project.entry-points."edecan.tools"]`) debe resolver
        a un callable sin argumentos que devuelva `list[Tool]` — p. ej.
        `edecan_toolkit.tools:get_all_tools` o
        `edecan_premium.tools:get_all_tools`.
        """
        for entry_point in entry_points(group=group):
            factory = entry_point.load()
            tools = factory()
            for tool in tools:
                self.register(tool)
            logger.info(
                "Cargadas %d herramienta(s) desde el entry point '%s' (%s)",
                len(tools),
                entry_point.name,
                group,
            )

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)


def _flags_satisfechos(requires_flags: frozenset[str], flags: dict[str, Any]) -> bool:
    return all(bool(flags.get(flag_name)) for flag_name in requires_flags)
