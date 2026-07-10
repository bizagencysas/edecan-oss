"""Tests de `edecan_automations.engine` — puro, sin IO, sin fakes de sesión."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from edecan_automations.engine import compute_next_run, validate_accion, validate_trigger

# ---------------------------------------------------------------------------
# validate_trigger
# ---------------------------------------------------------------------------


def test_validate_trigger_acepta_schedule_valido() -> None:
    validate_trigger({"kind": "schedule", "rrule": "FREQ=DAILY;BYHOUR=9"})


def test_validate_trigger_acepta_webhook_valido() -> None:
    validate_trigger({"kind": "webhook", "hook_secret": "un-secreto-largo"})


def test_validate_trigger_rechaza_kind_desconocido() -> None:
    with pytest.raises(ValueError, match="trigger inválido"):
        validate_trigger({"kind": "cron", "rrule": "FREQ=DAILY"})


def test_validate_trigger_rechaza_schedule_sin_rrule() -> None:
    with pytest.raises(ValueError, match="trigger inválido"):
        validate_trigger({"kind": "schedule"})


def test_validate_trigger_rechaza_webhook_sin_secreto() -> None:
    with pytest.raises(ValueError, match="trigger inválido"):
        validate_trigger({"kind": "webhook"})


def test_validate_trigger_rechaza_rrule_sintacticamente_invalida() -> None:
    with pytest.raises(ValueError, match="rrule inválida"):
        validate_trigger({"kind": "schedule", "rrule": "ESTO NO ES UNA RRULE"})


def test_validate_trigger_rechaza_no_dict() -> None:
    with pytest.raises(ValueError):
        validate_trigger({})


# ---------------------------------------------------------------------------
# validate_accion
# ---------------------------------------------------------------------------


def test_validate_accion_acepta_agent_instruction_valida() -> None:
    validate_accion({"kind": "agent_instruction", "instruccion": "Resume mis correos de hoy."})


def test_validate_accion_acepta_sin_kind_explicito_por_default() -> None:
    # `AgentInstructionAccion.kind` tiene default "agent_instruction"
    # (edecan_schemas.automations) — un dict sin "kind" también es válido.
    validate_accion({"instruccion": "Resume mis correos de hoy."})


def test_validate_accion_rechaza_kind_desconocido() -> None:
    with pytest.raises(ValueError, match="accion inválida"):
        validate_accion({"kind": "http_call", "instruccion": "algo"})


def test_validate_accion_rechaza_instruccion_faltante() -> None:
    with pytest.raises(ValueError, match="accion inválida"):
        validate_accion({"kind": "agent_instruction"})


def test_validate_accion_rechaza_instruccion_vacia_tras_recortar() -> None:
    with pytest.raises(ValueError, match="no puede estar vacía"):
        validate_accion({"kind": "agent_instruction", "instruccion": "   "})


# ---------------------------------------------------------------------------
# compute_next_run — determinista, con dtstart fijo
# ---------------------------------------------------------------------------


def test_compute_next_run_daily_desde_dtstart_fijo() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=DAILY", after=after)
    assert siguiente == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def test_compute_next_run_weekly_con_interval() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=WEEKLY;INTERVAL=2", after=after)
    assert siguiente == datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def test_compute_next_run_respeta_dtstart_embebido_en_la_rrule() -> None:
    # Si la rrule ya trae su propio DTSTART, ese ancla el patrón (aquí,
    # siempre a las 14:00) en vez del `after` que pasa el caller.
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    siguiente = compute_next_run("DTSTART:20260101T140000Z\nRRULE:FREQ=DAILY", after=after)
    assert siguiente == datetime(2026, 1, 1, 14, 0, tzinfo=UTC)


def test_compute_next_run_acepta_after_naive_como_utc() -> None:
    after_naive = datetime(2026, 1, 1, 9, 0)  # sin tzinfo
    siguiente = compute_next_run("FREQ=DAILY", after=after_naive)
    assert siguiente == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    assert siguiente is not None
    assert siguiente.tzinfo is not None


def test_compute_next_run_none_si_until_ya_paso() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=DAILY;UNTIL=20260101T100000Z", after=after)
    assert siguiente is None


def test_compute_next_run_ultima_ocurrencia_dentro_de_until() -> None:
    after = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=DAILY;UNTIL=20260103T090000Z", after=after)
    assert siguiente == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)


def test_compute_next_run_rrule_invalida_lanza_value_error() -> None:
    with pytest.raises(ValueError, match="rrule inválida"):
        compute_next_run("ESTO NO ES UNA RRULE", after=datetime(2026, 1, 1, tzinfo=UTC))


# ---------------------------------------------------------------------------
# compute_next_run — `anchor` (fase estable a través de recómputos
# repetidos, patrón real de `handlers/automation_scan.py`)
# ---------------------------------------------------------------------------


def test_compute_next_run_sin_anchor_hereda_minuto_segundo_de_after() -> None:
    # `BYHOUR=9` sin `BYMINUTE`/`BYSECOND` explícitos: RFC 5545 los hereda de
    # `dtstart`. Sin `anchor`, `dtstart` es el propio `after` -> el
    # minuto:segundo de creación queda "pegado" a la primera ocurrencia (esto
    # es correcto/esperado para el primer cómputo, ej. `tools.py::_crear`).
    creado = datetime(2026, 1, 1, 14, 32, 7, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=DAILY;BYHOUR=9", after=creado)
    assert siguiente == datetime(2026, 1, 2, 9, 32, 7, tzinfo=UTC)


def test_compute_next_run_recomputo_repetido_sin_anchor_deriva_el_minuto_segundo() -> None:
    # Reproduce el BUG: si cada recómputo reusa el `after` volátil del
    # sondeo como ancla (sin pasar `anchor`), la fase deriva de un ciclo a
    # otro apenas hay jitter entre el `next_run_at` calculado y el momento
    # real en que corre el siguiente sondeo.
    anchor = datetime(2026, 1, 1, 14, 32, 7, tzinfo=UTC)
    fases: list[tuple[int, int]] = []
    for jitter_segundos in (0, 7, 13, 22):
        siguiente = compute_next_run("FREQ=DAILY;BYHOUR=9", after=anchor)
        assert siguiente is not None
        fases.append((siguiente.minute, siguiente.second))
        anchor = siguiente + timedelta(seconds=jitter_segundos)
    assert len(set(fases)) > 1  # la fase SÍ cambió entre ciclos: esto es el bug


def test_compute_next_run_recomputo_repetido_con_anchor_no_deriva() -> None:
    # Mismo escenario que el test anterior, pero pasando `anchor` (el
    # `next_run_at` ya persistido) en cada recómputo, como hace el fix de
    # `handlers/automation_scan.py`: la fase queda fija para siempre, sin
    # importar el jitter entre el sondeo y la ocurrencia calculada.
    anchor = compute_next_run(
        "FREQ=DAILY;BYHOUR=9", after=datetime(2026, 1, 1, 14, 32, 7, tzinfo=UTC)
    )
    assert anchor is not None
    fase_esperada = (anchor.minute, anchor.second)

    for jitter_segundos in (0, 7, 13, 22):
        sondeo = anchor + timedelta(seconds=jitter_segundos)
        siguiente = compute_next_run("FREQ=DAILY;BYHOUR=9", after=sondeo, anchor=anchor)
        assert siguiente is not None
        assert (siguiente.minute, siguiente.second) == fase_esperada
        assert siguiente == anchor + timedelta(days=1)
        anchor = siguiente


def test_compute_next_run_anchor_no_afecta_rrule_con_dtstart_embebido() -> None:
    # Si la rrule ya trae su propio DTSTART, ese manda siempre (ver
    # `test_compute_next_run_respeta_dtstart_embebido_en_la_rrule`) —
    # `anchor` no debe poder pisarlo.
    after = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    anchor = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
    siguiente = compute_next_run(
        "DTSTART:20260101T140000Z\nRRULE:FREQ=DAILY", after=after, anchor=anchor
    )
    assert siguiente == datetime(2026, 1, 2, 14, 0, tzinfo=UTC)
