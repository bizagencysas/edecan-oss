"""`edecan_skills` — marketplace de "Agent Skills" (estándar abierto que indexa skills.sh,
`DIRECCION_ACTUAL.md` "Confirmado: agregar Ollama + integrar el marketplace de skills.sh").

Un "Agent Skill" es un repo/carpeta con `SKILL.md` (frontmatter YAML `name`/`description`/
opcional `version`/`license`/metadata; el cuerpo markdown son instrucciones para el agente).
Este paquete implementa el mismo mecanismo de instalación que `npx skills add <owner/repo>`
(lectura directa de `raw.githubusercontent.com`, API pública oficial de GitHub) más una
búsqueda best-effort contra la API de skills.sh — ver `docs/skills.md`.

- `client.SkillsIndexClient` — búsqueda best-effort en el índice de skills.sh.
- `installer` — `parse_source`/`fetch_skill`/`parse_skill_md`/`install_from_source`:
  parseo de la fuente, descarga y parseo del `SKILL.md`, sin tocar la base de datos.
- `store` — acceso a la tabla `skills` (SQL parametrizado, nunca `edecan_db.models`).
- `tools` — las 5 herramientas del agente (`get_all_tools()`, entry point `edecan.tools`).

`get_all_tools()` es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")` (`ARCHITECTURE.md` §10.7),
declarado en `pyproject.toml` como `[project.entry-points."edecan.tools"]`.
"""

from __future__ import annotations

from edecan_core import Tool

from .client import SkillHit, SkillsIndexClient
from .installer import (
    FuenteInvalidaError,
    InstalledSkill,
    SkillDemasiadoGrandeError,
    SkillFile,
    SkillNoEncontradaError,
    fetch_skill,
    install_from_source,
    parse_skill_md,
    parse_source,
)
from .tools import (
    BuscarSkillsTool,
    DesinstalarSkillTool,
    InstalarSkillTool,
    ListarSkillsTool,
    UsarSkillTool,
)

__all__ = [
    "BuscarSkillsTool",
    "DesinstalarSkillTool",
    "FuenteInvalidaError",
    "InstalarSkillTool",
    "InstalledSkill",
    "ListarSkillsTool",
    "SkillDemasiadoGrandeError",
    "SkillFile",
    "SkillHit",
    "SkillNoEncontradaError",
    "SkillsIndexClient",
    "UsarSkillTool",
    "fetch_skill",
    "get_all_tools",
    "install_from_source",
    "parse_skill_md",
    "parse_source",
]


def get_all_tools() -> list[Tool]:
    """Instancia las 5 herramientas del marketplace (nombres exactos, ver `tools.py`)."""
    return [
        BuscarSkillsTool(),
        InstalarSkillTool(),
        ListarSkillsTool(),
        UsarSkillTool(),
        DesinstalarSkillTool(),
    ]
