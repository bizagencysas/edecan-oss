"""Registro defensivo del handler v6 en `edecan_worker.handlers`
(`ARCHITECTURE.md` §15, dueño WP-V6-01).

Mismo criterio que `test_v2_handlers_registry.py`: ejercita
`_register_defensive` directamente (en vez de recargar el paquete con
distintos `sys.modules` monkeypatcheados) para no depender de si
`process_meeting.py` ya aterrizó de verdad en disco -- WP-V6-05 lo agrega en
paralelo (ARCHITECTURE.md §10.1) y esta suite no debe volverse intermitente
según qué WPs ya corrieron. El caso "el módulo SÍ existe" se prueba reusando
un módulo real de v1 (`ingest_file`) como doble: `_register_defensive` no le
da ningún trato especial al nombre `process_meeting`, así que probar el
mecanismo con cualquier submódulo real es equivalente y determinista.
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
_JOB_TYPE_V6 = "process_meeting"


def test_job_type_v6_pinned_esta_en_job_types():
    assert _JOB_TYPE_V6 in JOB_TYPES


def test_job_type_v6_conserva_su_posicion_de_job_types():
    # ARCHITECTURE.md §15: 12º job type, agregado al final de los 11 de
    # v1+v2+v5.
    assert JOB_TYPES[11] == _JOB_TYPE_V6


def test_handlers_conserva_los_7_v1_intactos():
    for job_type in _JOB_TYPES_V1:
        assert job_type in HANDLERS, job_type
        assert inspect.iscoroutinefunction(HANDLERS[job_type]), job_type


def test_handlers_nunca_registra_un_job_type_fuera_de_job_types():
    assert set(HANDLERS) <= set(JOB_TYPES)


def test_handler_v6_ausente_hoy_o_coroutina_valida_si_ya_aterrizo():
    # A la fecha de este WP el handler `process_meeting` todavía no existe en
    # disco -- pero si YA aterrizó en paralelo (WP-V6-05), debe ser una
    # coroutina válida como cualquier otro handler.
    if _JOB_TYPE_V6 in HANDLERS:
        assert inspect.iscoroutinefunction(HANDLERS[_JOB_TYPE_V6])


def test_register_defensive_tolera_modulo_ausente(caplog: pytest.LogCaptureFixture) -> None:
    handlers: dict[str, Handler] = {}

    with caplog.at_level(logging.WARNING, logger="edecan_worker.handlers"):
        _register_defensive(handlers, _JOB_TYPE_V6, "definitely_does_not_exist_v6_handler")

    assert handlers == {}
    assert "no disponible todavía" in caplog.text
    assert _JOB_TYPE_V6 in caplog.text


def test_register_defensive_registra_cuando_el_modulo_existe() -> None:
    handlers: dict[str, Handler] = {}

    _register_defensive(handlers, _JOB_TYPE_V6, "ingest_file")

    assert handlers[_JOB_TYPE_V6] is ingest_file.handle
