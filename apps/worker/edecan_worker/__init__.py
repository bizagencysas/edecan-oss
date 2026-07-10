"""`edecan_worker` — consumidor de jobs asíncronos de Edecán (ARCHITECTURE.md §10.11).

Lee mensajes de `SQS_QUEUE_URL` (`JobEnvelope`, ver `edecan_schemas.queue`) y
ejecuta el handler registrado en `edecan_worker.handlers.HANDLERS` según
`type`. Se conecta a Postgres como *owner* (bypassa Row-Level Security — ver
ARCHITECTURE.md §2) por lo que cada handler filtra manualmente por el
`tenant_id` del job en cada consulta (`edecan_worker.repo`).

Punto de entrada de proceso: `python -m edecan_worker.main` (`make worker`).
"""

from __future__ import annotations

__version__ = "0.1.0"
