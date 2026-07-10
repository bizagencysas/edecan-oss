from __future__ import annotations

from uuid import uuid4

import pytest
from edecan_schemas.queue import JOB_TYPES, JobEnvelope
from pydantic import ValidationError


def test_job_types_pinned():
    # Los 7 de v1 + los 3 de v2 (ROADMAP_V2.md §7.3, WP-V2-01) — la cobertura
    # exhaustiva de esta tupla vive en test_v2_contracts.py::
    # test_job_types_v1_intactos_y_v2_agregados_al_final; aquí solo se
    # verifica que los 7 tipos originales de v1 siguen intactos y en orden.
    assert JOB_TYPES[:7] == (
        "ingest_file",
        "sync_connector",
        "send_reminder",
        "send_reminder_scan",
        "run_campaign_step",
        "generate_content",
        "memory_consolidate",
    )


def test_job_envelope_valido():
    env = JobEnvelope(
        job_id=uuid4(), tenant_id=uuid4(), type="ingest_file", payload={"file_id": "f1"}
    )
    assert env.attempt == 0
    assert env.payload == {"file_id": "f1"}


def test_job_envelope_tenant_id_none_para_jobs_globales():
    env = JobEnvelope(job_id=uuid4(), tenant_id=None, type="send_reminder_scan", payload={})
    assert env.tenant_id is None


def test_job_envelope_tipo_invalido_falla():
    with pytest.raises(ValidationError):
        JobEnvelope(job_id=uuid4(), tenant_id=None, type="borrar_todo", payload={})


def test_job_types_incluye_generate_podcast_v5():
    # ARCHITECTURE.md §14 (dueño WP-V5-01): 11º job type, agregado al final
    # de los 10 de v1+v2 — la cobertura exhaustiva de la tupla completa vive
    # en test_v2_contracts.py::test_job_types_v1_y_v2_intactos_mas_v5_al_final.
    assert "generate_podcast" in JOB_TYPES


def test_job_envelope_generate_podcast_valido():
    env = JobEnvelope(
        job_id=uuid4(), tenant_id=uuid4(), type="generate_podcast", payload={"guion": "..."}
    )
    assert env.type == "generate_podcast"


def test_job_types_incluye_process_meeting_v6():
    # ARCHITECTURE.md §15 (dueño WP-V6-01): 12º job type, agregado al final
    # de los 11 de v1+v2+v5 — la cobertura exhaustiva de la tupla completa
    # vive en test_v2_contracts.py::test_job_types_v1_y_v2_intactos_mas_v5_al_final.
    assert JOB_TYPES[-1] == "process_meeting"
    assert "process_meeting" in JOB_TYPES


def test_job_envelope_process_meeting_valido():
    env = JobEnvelope(
        job_id=uuid4(),
        tenant_id=uuid4(),
        type="process_meeting",
        payload={"meeting_id": str(uuid4())},
    )
    assert env.type == "process_meeting"
