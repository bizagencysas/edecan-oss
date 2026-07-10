"""`dispatch por tipo`: `HANDLERS` cubre los 7 `JOB_TYPES` de v1 (§10.5, §10.11).

Desde WP-V2-01 (ROADMAP_V2.md §7.3), `edecan_schemas.JOB_TYPES` creció a 10
tipos (+`run_mission`, `run_automation`, `automation_scan`), pero sus
handlers viven en paquetes de trabajo v2 que aterrizan por separado y de
forma defensiva (`edecan_worker.handlers._register_defensive`, ver su
docstring) — por eso este archivo compara `HANDLERS` contra el subconjunto
`>=` de los 7 tipos ORIGINALES de v1 (que SIEMPRE deben tener handler, sin
excepción) en vez de la igualdad exacta con `JOB_TYPES` completo. La
cobertura específica de v2 (tolera módulos ausentes, nunca registra un
`job_type` fuera de `JOB_TYPES`) vive en `test_v2_handlers_registry.py`.
"""

from __future__ import annotations

import inspect

from edecan_schemas import JOB_TYPES
from edecan_worker.handlers import HANDLERS

_JOB_TYPES_V1: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
)


def test_handlers_cubre_todos_los_job_types_v1() -> None:
    assert set(_JOB_TYPES_V1) <= set(HANDLERS.keys())
    # Ningún handler registrado corresponde a un job_type no declarado.
    assert set(HANDLERS.keys()) <= set(JOB_TYPES)


def test_handlers_son_coroutinas() -> None:
    for job_type, handler in HANDLERS.items():
        assert inspect.iscoroutinefunction(handler), f"{job_type} no es una función async"
