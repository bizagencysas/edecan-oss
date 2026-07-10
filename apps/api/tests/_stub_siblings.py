"""Hace importable a `edecan_api` para los tests apuntando `sys.path` al
código fuente real de cada paquete hermano (`packages/<paquete>/`), sin
reimplementar nada.

Contexto (ARCHITECTURE.md §10.1): "Los tests NO importan paquetes hermanos:
usan stubs/fakes que implementen los contratos de esta sección. Importar
hermanos en código de producción sí está permitido". `edecan_api` (código de
producción) importa legítimamente `edecan_core`, `edecan_db`, `edecan_schemas`,
`edecan_llm`, `edecan_connectors` y `edecan_voice` — eso es correcto y no
cambia aquí: este módulo solo resuelve que esos paquetes sean *importables*
en un `pytest` corrido solo sobre `apps/api` (sin haber corrido `uv sync` a
nivel de todo el workspace), agregando su código fuente real a `sys.path`.

Los TESTS de este paquete (`test_*.py`) igual respetan la regla de no
importar hermanos: no llaman directamente a `edecan_core.agent.Agent` ni a
`edecan_db` — cuando necesitan control determinista sobre el turno del
agente, hacen `monkeypatch.setattr(conversations_module, "Agent", Fake)`
sobre el símbolo ya importado en `edecan_api.routers.conversations` (ver
`test_conversations.py`), y para todo lo demás usan `dependency_overrides`
con los fakes de `tests/api_fakes.py` (`FakeRepo`, `FakeRedis`) — nunca tocan
Postgres/Redis reales.

Se importa por su nombre al principio de `conftest.py`, antes de cualquier
`import edecan_api`.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PACKAGES_DIR = _REPO_ROOT / "packages"

for _pkg in ("schemas", "db", "llm", "connectors", "voice", "core", "toolkit"):
    _src = str(_PACKAGES_DIR / _pkg)
    if _src not in sys.path:
        sys.path.insert(0, _src)
