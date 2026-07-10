# edecan_agents

Ecosistema de agentes de Edecán: perfiles de sub-agente (`profiles.py`), el
`Orchestrator` que planifica y ejecuta misiones multi-paso (`orchestrator.py`),
el envoltorio de `ToolRegistry` que restringe qué herramientas ve cada
sub-agente (`registry_view.py`) y la herramienta `delegar_mision` (`tools.py`)
que el agente principal usa para crear una misión.

Ver `ROADMAP_V2.md` §7.9 y `docs/agentes.md` para el diseño completo.

Tests offline (fakes locales, sin importar `edecan_core`/`edecan_db` — ver
`ARCHITECTURE.md` §10.1): `pytest` desde este directorio o desde la raíz del
workspace (`make test`).
