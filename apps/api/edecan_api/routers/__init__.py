"""Routers de `edecan_api`, uno por área funcional (ARCHITECTURE.md §10.12).

`edecan_api.main.create_app()` importa cada módulo y monta su `router`
(`APIRouter`). Cada router fija su propio `prefix` (`/v1/...`) — no hay un
prefix global adicional.
"""

from __future__ import annotations
