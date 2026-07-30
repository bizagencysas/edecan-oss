"""Job `run_campaign_step`: delega en `edecan_premium.campaigns.handle` si el
paquete comercial está instalado; si no, termina sin error — el core
self-host no trae campañas de voz/SMS (ARCHITECTURE.md §6, §10.10, §10.11).

`edecan_premium.campaigns.handle(env, deps)` procesa hasta 10
`campaign_targets` por paso y se re-encola a sí mismo si quedan pendientes
(ARCHITECTURE.md §10.10) — toda esa lógica vive en `premium/`, fuera del
alcance de este paquete.
"""

from __future__ import annotations

import logging

from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps

logger = logging.getLogger(__name__)


async def handle(env: JobEnvelope, deps: Deps) -> None:
    try:
        from edecan_premium import campaigns
    except ImportError:
        logger.info(
            "premium no instalado: job run_campaign_step ignorado (job_id=%s tenant_id=%s)",
            env.job_id,
            env.tenant_id,
        )
        return

    await campaigns.handle(env, deps)
