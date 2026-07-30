"""Fixtures/config compartida de `apps/local/tests/`.

Registra el marker `integration` localmente (vía `pytest_configure`), mismo
patrón que `packages/db/tests/conftest.py`/`apps/api/tests/conftest.py`, en
vez de tocar el `[tool.pytest.ini_options]` de la raíz del monorepo (que
pertenece a otro paquete de trabajo). A diferencia de esos dos, aquí
"integration" cubre dos cosas distintas según el módulo (ver el docstring de
cada test): un `uvicorn.Server` + cliente `aioboto3` reales contra
`edecan_local.objectstore` (sin dependencias externas, solo más lento que el
resto), o un Postgres embebido real vía `pgserver` (`edecan_local.pg`). El
`importorskip` de integración conserva un skip claro en arquitecturas sin un
wheel publicado de `pgserver`.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: usa recursos reales (un socket real, o Postgres "
        "embebido vía pgserver) en vez de fakes -- más lento que el resto; "
        "los que necesitan 'pgserver' se saltan solos si la plataforma no "
        "tiene un wheel publicado.",
    )
