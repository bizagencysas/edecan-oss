"""`edecan_agents` — ecosistema de agentes: perfiles, `Orchestrator`
multi-agente y la herramienta `delegar_mision` (`ROADMAP_V2.md` §7.9,
dueño WP-V2-06).

`get_all_tools()` es el entry point `edecan.tools` que consume
`edecan_core.tools.registry.ToolRegistry.load_entry_points` (ARCHITECTURE.md
§10.7), declarado en `pyproject.toml` como
`[project.entry-points."edecan.tools"]`.
"""

from __future__ import annotations

from .orchestrator import DEFAULT_MAX_STEPS, FALLBACK_AGENT_KEY, Mission, Orchestrator, RunDeps
from .profiles import IMPLEMENTED_AGENT_KEYS, PROFILES, AgentProfile
from .registry_view import RestrictedRegistry
from .tools import DelegarMisionTool, get_all_tools

__all__ = [
    "DEFAULT_MAX_STEPS",
    "FALLBACK_AGENT_KEY",
    "IMPLEMENTED_AGENT_KEYS",
    "PROFILES",
    "AgentProfile",
    "DelegarMisionTool",
    "Mission",
    "Orchestrator",
    "RestrictedRegistry",
    "RunDeps",
    "get_all_tools",
]
