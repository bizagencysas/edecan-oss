"""edecan_api — API HTTP de Edecán (FastAPI).

Ver `ARCHITECTURE.md` §10.12 para el contrato completo de rutas. La app se
construye con `create_app()` en `edecan_api.main`; `edecan_api.main:app` es la
instancia que sirve `uvicorn` (`make api`).
"""

from __future__ import annotations

__version__ = "0.4.0"
