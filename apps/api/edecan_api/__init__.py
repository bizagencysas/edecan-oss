"""edecan_api — API HTTP de Edecán (FastAPI).

Ver `ARCHITECTURE.md` §10.12 para el contrato completo de rutas. La app se
construye con `create_app()` en `edecan_api.main`; `edecan_api.main:app` es la
instancia que sirve `uvicorn` (`make api`).
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version


def _installed_version() -> str:
    """Return the package metadata version without duplicating pyproject.toml."""

    try:
        return version("edecan-api")
    except PackageNotFoundError:
        # Source-only imports outside the managed workspace should remain
        # usable, but must never claim a stale release number.
        return "0+unknown"


__version__ = _installed_version()
