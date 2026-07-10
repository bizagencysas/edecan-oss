"""`JobEnvelope` y los tipos de job pinned que consume `apps/worker` (§10.5, §10.11).

Los 3 tipos `run_mission`/`run_automation`/`automation_scan` son nuevos de v2
(ROADMAP_V2.md §7.3, dueño WP-V2-01). A diferencia de los 7 de v1, sus
handlers NO aterrizan en este work package: `edecan_worker.handlers` los
registra de forma defensiva (solo si el submódulo del WP dueño — WP-V2-06
para `run_mission`, WP-V2-07 para los otros dos— ya existe, ver
ARCHITECTURE.md §10.1 y ese módulo). Que un `job_type` esté en `JOB_TYPES`
valida el envelope (`enqueue`/`JobEnvelope`); que tenga handler registrado es
un invariante aparte, verificado en `apps/worker/tests/test_v2_handlers_registry.py`.

`generate_podcast` (el 11º, agregado al final) es de v5 (`ARCHITECTURE.md`
§14, dueño WP-V5-01) — mismo criterio que los 3 de v2: su handler
(`apps/worker/edecan_worker/handlers/generate_podcast.py`) lo aporta WP-V5-11
en paralelo y se registra igual de forma defensiva vía
`edecan_worker.handlers._register_defensive`.

`process_meeting` (el 12º, agregado al final) es de v6 (`ARCHITECTURE.md`
§15, dueño WP-V6-01) — mismo criterio que `generate_podcast`: su handler
(`apps/worker/edecan_worker/handlers/process_meeting.py`) lo aporta WP-V6-05
en paralelo y se registra igual de forma defensiva vía
`edecan_worker.handlers._register_defensive`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

JOB_TYPES: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
    "run_mission",
    "run_automation",
    "automation_scan",
    # --- v5 (ARCHITECTURE.md §14, dueño WP-V5-01) ---------------------------
    "generate_podcast",
    # --- v6 (ARCHITECTURE.md §15, dueño WP-V6-01) ---------------------------
    # handler lo aporta WP-V6-05
    "process_meeting",
)


class JobEnvelope(BaseModel):
    """Mensaje que viaja por `SQS_QUEUE_URL` y que consume `edecan_worker`."""

    job_id: UUID
    tenant_id: UUID | None
    type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    attempt: int = 0

    @field_validator("type")
    @classmethod
    def _tipo_valido(cls, value: str) -> str:
        if value not in JOB_TYPES:
            raise ValueError(f"type inválido: {value!r}. Debe ser uno de {JOB_TYPES}")
        return value
