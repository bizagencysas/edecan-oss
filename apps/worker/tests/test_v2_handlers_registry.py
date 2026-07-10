"""Registro defensivo de handlers v2 en `edecan_worker.handlers`
(ROADMAP_V2.md §7.3/§7.6, dueño WP-V2-01).

Ejercita `_register_defensive` directamente (en vez de recargar el paquete
con distintos `sys.modules` monkeypatcheados) para no depender de si
`run_mission.py`/`run_automation.py`/`automation_scan.py` ya aterrizaron de
verdad en disco -- WP-V2-06/WP-V2-07 los agregan en paralelo
(ARCHITECTURE.md §10.1) y esta suite no debe volverse intermitente según qué
WPs ya corrieron. El caso "el módulo SÍ existe" se prueba reusando un módulo
real de v1 (`ingest_file`) como doble: `_register_defensive` no le da ningún
trato especial a los 3 nombres v2, así que probar el mecanismo con cualquier
submódulo real es equivalente y determinista.
"""

from __future__ import annotations

import inspect
import logging

import pytest
from edecan_schemas import JOB_TYPES
from edecan_worker.handlers import HANDLERS, Handler, _register_defensive, ingest_file

_JOB_TYPES_V1: tuple[str, ...] = (
    "ingest_file",
    "sync_connector",
    "send_reminder",
    "send_reminder_scan",
    "run_campaign_step",
    "generate_content",
    "memory_consolidate",
)
_JOB_TYPES_V2: tuple[str, ...] = ("run_mission", "run_automation", "automation_scan")


def test_job_types_v2_pinned_estan_en_job_types():
    assert set(_JOB_TYPES_V2) <= set(JOB_TYPES)


def test_handlers_conserva_los_7_v1_intactos():
    for job_type in _JOB_TYPES_V1:
        assert job_type in HANDLERS, job_type
        assert inspect.iscoroutinefunction(HANDLERS[job_type]), job_type


def test_handlers_nunca_registra_un_job_type_fuera_de_job_types():
    assert set(HANDLERS) <= set(JOB_TYPES)


def test_handlers_v2_ausentes_hoy_o_coroutinas_validas_si_ya_aterrizaron():
    # A la fecha de este WP ninguno de los 3 handlers v2 existe todavía en
    # disco -- pero si alguno YA aterrizó en paralelo, debe ser una
    # coroutina válida como cualquier otro handler.
    for job_type in _JOB_TYPES_V2:
        if job_type in HANDLERS:
            assert inspect.iscoroutinefunction(HANDLERS[job_type]), job_type


def test_register_defensive_tolera_modulo_ausente(caplog: pytest.LogCaptureFixture) -> None:
    handlers: dict[str, Handler] = {}

    with caplog.at_level(logging.WARNING, logger="edecan_worker.handlers"):
        _register_defensive(handlers, "run_mission", "definitely_does_not_exist_v2_handler")

    assert handlers == {}
    assert "no disponible todavía" in caplog.text
    assert "run_mission" in caplog.text


def test_register_defensive_es_independiente_por_llamada(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handlers: dict[str, Handler] = {}

    with caplog.at_level(logging.WARNING, logger="edecan_worker.handlers"):
        _register_defensive(handlers, "run_automation", "definitely_does_not_exist_v2_handler")
        _register_defensive(handlers, "automation_scan", "also_does_not_exist_v2_handler")

    assert handlers == {}
    assert caplog.text.count("no disponible todavía") == 2


def test_register_defensive_registra_cuando_el_modulo_existe() -> None:
    handlers: dict[str, Handler] = {}

    _register_defensive(handlers, "un_tipo_de_prueba", "ingest_file")

    assert handlers["un_tipo_de_prueba"] is ingest_file.handle
