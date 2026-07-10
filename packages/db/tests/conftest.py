"""Fixtures/config compartida de `packages/db/tests/`.

Registra el marker `integration` localmente (vía `pytest_configure`) en vez
de tocar el `[tool.pytest.ini_options]` de la raíz del monorepo, que
pertenece a otro paquete de trabajo.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "integration: requiere una base de datos Postgres real y alcanzable "
        "(ver DATABASE_URL); se salta automáticamente si no hay una.",
    )
