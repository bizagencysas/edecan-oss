"""`ToolRegistry` — registro de herramientas del agente (ARCHITECTURE.md §10.7).

Filtra `specs()` por los flags de plan del tenant y descubre herramientas de
otros paquetes (`edecan_toolkit`, `premium/`) vía el grupo de entry points
`"edecan.tools"`. Las políticas de cada integración se aplican en la propia
tool o conector; el registro no veta capacidades por palabras en su nombre.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import sys
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

from edecan_schemas import ToolSpec

from .base import Tool, confirmaciones_desactivadas

logger = logging.getLogger(__name__)

DEFAULT_ENTRY_POINT_GROUP = "edecan.tools"

_NOMBRE_PLUGIN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MODULO_PLUGIN_PREFIJO = "edecan_plugins"

class ToolRegistry:
    """Registro en memoria de las `Tool` disponibles para el agente."""

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._plugins_huella: tuple[Any, ...] | None = None
        self._plugin_names: set[str] = set()

    def register(self, tool: Tool) -> None:
        """Registra `tool` (sobreescribe si ya había una con el mismo `name`).

        Si el despliegue apagó las confirmaciones (`EDECAN_SIN_CONFIRMACIONES`),
        el gate se levanta AQUÍ y no en cada herramienta. Este es el único embudo
        por el que pasan todas -- las del núcleo, las de entry point de otros
        paquetes y las que adapta `edecan_mcp` --, así que apagarlo en un solo
        punto no deja ninguna afuera. Ir tool por tool sí las dejaría: bastaría
        que un paquete nuevo declarara `dangerous = True` para reintroducir el
        freno sin que nadie lo pidiera.

        Se hace por INSTANCIA, no tocando la clase: `Agent.run_turn` lee
        `tool.dangerous` del objeto que sale de este registro, y dos registros
        distintos en el mismo proceso (uno normal y uno restringido, ver
        `edecan_agents.registry_view`) deben poder tener criterios distintos.
        """
        # Most concrete tools use class metadata and inherit ``Tool.__init__``.
        # A few legacy adapters define their own constructor without calling
        # ``super()``; initialise the base metadata here before this registry
        # mutates effective authorisation so they preserve the same invariant.
        if "_intrinsically_dangerous" not in vars(tool):
            Tool.__init__(tool)
        if confirmaciones_desactivadas():
            tool.dangerous = False
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        """Devuelve la `Tool` registrada con `name`, o `None` si no existe."""
        return self._tools.get(name)

    def load_plugin_dir(self, path: str | Path | None, *, tenant_id: Any | None = None) -> int:
        """Carga tools escritas por los PROPIOS BOTS (plugins permanentes).

        Escanea `path` (p. ej. `/opt/edecan/data/plugins/<tenant_id>`): cada
        `*.py` con nombre válido se importa y debe exponer
        `get_all_tools() -> list[Tool]` (mismo contrato que los entry points).
        Un plugin que no compila o no cumple el contrato se OMITE con warning
        — jamás tumba el registro ni el proceso. Re-ejecuta solo si el
        directorio cambió (cache por mtime): así `get_tool_registry` puede
        llamarlo en cada request sin costo.

        Los plugins SOBREESCRIBEN tools del mismo nombre (el dueño pidió
        poder editar su propio código: el plugin más nuevo manda).

        `tenant_id` (opcional) SOLO da nombre al namespace del módulo
        (`edecan_plugins.<tenant_id>.<stem>` en vez de
        `edecan_plugins.<stem>`), para que dos tenants con un plugin del MISMO
        nombre de archivo no colisionen en `sys.modules` (BOTS-14). NO
        selecciona el directorio: el llamador ya pasó el subdirectorio del
        tenant (la API lo hace en `InstallationToolRegistry.load_plugin_dir`,
        los workers vía `registry_para_tenant`). Sin `tenant_id` se conserva el
        namespace histórico, sin cambios de comportamiento para los llamadores
        que aún no lo pasan.
        """
        if not path:
            return 0
        directorio = Path(path)
        if not directorio.is_dir():
            return 0
        # Huella = (nombre, mtime, tamaño) de CADA .py: una edición in-place
        # de un archivo NO toca el mtime del directorio — con solo el mtime
        # del dir, el cambio nunca se cargaría (bug real encontrado en la
        # auditoría del primer diseño).
        try:
            huella = tuple(
                sorted(
                    (archivo.name, archivo.stat().st_mtime_ns, archivo.stat().st_size)
                    for archivo in directorio.glob("*.py")
                )
            )
        except OSError:
            return 0
        if huella == self._plugins_huella:
            return 0
        self._plugins_huella = huella
        # Quitar las tools de plugins de la carga ANTERIOR: si un plugin se
        # BORRÓ del directorio, su tool no debe quedar fantasma en el
        # registro (bug encontrado en la auditoría: al limpiar el dir, la
        # tool seguía viva). Después se re-registra la base, para que la
        # tool del sistema que un plugin sombreaba VUELVA.
        for nombre in list(self._plugin_names):
            self._tools.pop(nombre, None)
        self._plugin_names.clear()
        try:
            self.load_entry_points(group=DEFAULT_ENTRY_POINT_GROUP)
        except Exception:  # noqa: BLE001 - si los entry points fallan, seguimos con lo que había
            logger.warning("re-registro de entry points falló al refrescar plugins", exc_info=True)
        cargadas = 0
        for archivo in sorted(directorio.glob("*.py")):
            if not _NOMBRE_PLUGIN.fullmatch(archivo.stem):
                logger.warning("plugin ignorado por nombre inválido: %s", archivo.name)
                continue
            try:
                if tenant_id is None:
                    nombre_modulo = f"{_MODULO_PLUGIN_PREFIJO}.{archivo.stem}"
                else:
                    nombre_modulo = f"{_MODULO_PLUGIN_PREFIJO}.{tenant_id}.{archivo.stem}"
                spec = importlib.util.spec_from_file_location(nombre_modulo, archivo)
                if spec is None or spec.loader is None:
                    logger.warning("plugin sin spec válida: %s", archivo.name)
                    continue
                modulo = importlib.util.module_from_spec(spec)
                sys.modules[nombre_modulo] = modulo
                spec.loader.exec_module(modulo)
                fabrica = getattr(modulo, "get_all_tools", None)
                if not callable(fabrica):
                    logger.warning("plugin sin get_all_tools(): %s", archivo.name)
                    continue
                for tool in fabrica():
                    if not isinstance(tool, Tool) or not tool.name:
                        logger.warning(
                            "plugin %s expone un elemento sin nombre válido", archivo.name
                        )
                        continue
                    self.register(tool)
                    self._plugin_names.add(tool.name)
                    cargadas += 1
                    logger.info("plugin cargado: %s (desde %s)", tool.name, archivo.name)
            except Exception:  # noqa: BLE001 - un plugin roto se omite, nunca tumba el registro
                logger.warning("plugin falló y se omite: %s", archivo.name, exc_info=True)
        return cargadas

    def all(self) -> list[Tool]:
        """Todas las `Tool` registradas (los objetos, no solo specs) — para
        construir registros derivados (p. ej. el del bot con acceso total)."""
        return list(self._tools.values())

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


def registry_para_tenant(root: str | Path | None, tenant_id: Any) -> ToolRegistry:
    """Registro listo para un tenant: entry points + sus propios plugins.

    Factoría común de registro de plugins por tenant que API, misiones,
    automatizaciones y runs persistentes usan IGUAL (BOTS-14): construye un
    `ToolRegistry` nuevo, carga los entry points `edecan.tools` (tools del
    núcleo y de otros paquetes) y luego carga SOLO el subdirectorio de este
    tenant (`<root>/<tenant_id>`) — sin recursión global, jamás los plugins de
    otros tenants. El `tenant_id` además da nombre al namespace del módulo
    (`edecan_plugins.<tenant_id>.<stem>`), así dos tenants con un plugin del
    MISMO nombre de archivo no colisionan en `sys.modules`.

    Los plugins del tenant SOBREESCRIBEN una tool del mismo nombre (el dueño
    quiere editar su propio código); la invalidación por huella
    (mtime/tamaño) y el borrado sin fantasmas los conserva `load_plugin_dir`.
    """
    registry = ToolRegistry()
    registry.load_entry_points(group=DEFAULT_ENTRY_POINT_GROUP)
    subdir = Path(root) / str(tenant_id) if root else None
    registry.load_plugin_dir(subdir, tenant_id=tenant_id)
    return registry


def _flags_satisfechos(requires_flags: frozenset[str], flags: dict[str, Any]) -> bool:
    return all(bool(flags.get(flag_name)) for flag_name in requires_flags)
