"""Tests de `edecan_automations.engine` — puro, sin IO, sin fakes de sesión."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import pytest
from edecan_automations.engine import (
    compute_next_run,
    normalize_timezone,
    validate_accion,
    validate_trigger,
)

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


def test_validate_accion_rechaza_sin_kind_explicito() -> None:
    # `AccionDef` es una unión discriminada por `kind` (ver docstring de
    # `edecan_schemas.automations`): a diferencia de antes de que existiera
    # la segunda variante (`create_linkedin_post`), un dict sin "kind" YA NO
    # cae al default de `AgentInstructionAccion.kind` -- Pydantic v2 exige el
    # tag explícito para elegir el miembro de la unión.
    with pytest.raises(ValueError, match="accion inválida"):
        validate_accion({"instruccion": "Resume mis correos de hoy."})


def test_validate_accion_acepta_create_linkedin_post_valida() -> None:
    # Segunda variante (paridad REFERENCIA, ver docstring de
    # `edecan_schemas.automations`): encola `create_linkedin_post` directo,
    # sin turno de agente. `destino`/`tema`/`seed_id` son todos opcionales.
    validate_accion({"kind": "create_linkedin_post", "destino": "organization"})
    validate_accion({"kind": "create_linkedin_post"})


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


# ---------------------------------------------------------------------------
# compute_next_run — `timezone` (la rrule se lee en hora LOCAL; el retorno
# sigue siendo UTC). Bogotá es UTC-5 todo el año (sin horario de verano), así
# que sus asserts son estables sin importar el mes.
# ---------------------------------------------------------------------------


def test_compute_next_run_byhour_9_en_bogota_da_las_14_utc() -> None:
    # EL caso del sembrado: el slot se llama "09:00" porque son las nueve de
    # la mañana del dueño. Sin `timezone` eso significaba las 9 UTC = las 4:00
    # a.m. en Bogotá, y los 3 posts de LinkedIn salían de madrugada.
    after = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)  # 15:00 en Bogotá
    siguiente = compute_next_run(
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", after=after, timezone="America/Bogota"
    )
    assert siguiente == datetime(2026, 1, 2, 14, 0, tzinfo=UTC)


def test_compute_next_run_con_timezone_devuelve_utc_tz_aware() -> None:
    # El contrato de salida no cambia con `timezone`: quien llama guarda esto
    # en `automations.next_run_at` y lo compara contra `now()` — devolver hora
    # local sería un bug de cinco horas, silencioso y difícil de ver.
    siguiente = compute_next_run(
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        after=datetime(2026, 1, 1, 20, 0, tzinfo=UTC),
        timezone="America/Bogota",
    )
    assert siguiente is not None
    assert siguiente.utcoffset() == timedelta(0)
    assert (siguiente.hour, siguiente.minute) == (14, 0)


def test_compute_next_run_los_5_slots_del_sembrado_caen_donde_dice_el_plan() -> None:
    # Paso 1 de `plan-referencia-2.0.local.md`: 9/11/13/15/17 de Bogotá deben caer
    # en 14/16/18/20/22 UTC — exactamente las horas que hoy están puestas A
    # MANO en las 5 filas del dueño. Este test es el que garantiza que un
    # re-sembrado reproduzca esa corrección en vez de deshacerla.
    after = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)  # 07:00 en Bogotá
    for hora_local, hora_utc in ((9, 14), (11, 16), (13, 18), (15, 20), (17, 22)):
        siguiente = compute_next_run(
            f"FREQ=DAILY;BYHOUR={hora_local};BYMINUTE=0",
            after=after,
            timezone="America/Bogota",
        )
        assert siguiente == datetime(2026, 1, 1, hora_utc, 0, tzinfo=UTC)


def test_compute_next_run_sin_timezone_es_identico_a_con_utc_explicito() -> None:
    # Regresión de compatibilidad: la firma nueva no puede mover NI UNA fila
    # de las que no declaran huso (todas las existentes, `server_default
    # 'UTC'`). Se cruzan las tres formas de decir "sin zona".
    after = datetime(2026, 1, 1, 14, 32, 7, tzinfo=UTC)
    for rrule in (
        "FREQ=DAILY",
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        "FREQ=DAILY;BYHOUR=9",  # minuto heredado del ancla
        "FREQ=WEEKLY;INTERVAL=2",
        "FREQ=MINUTELY;INTERVAL=15",
        "DTSTART:20260101T140000Z\nRRULE:FREQ=DAILY",
    ):
        sin_zona = compute_next_run(rrule, after=after)
        assert sin_zona == compute_next_run(rrule, after=after, timezone=None)
        assert sin_zona == compute_next_run(rrule, after=after, timezone="UTC")
        assert sin_zona == compute_next_run(rrule, after=after, timezone="  ")


def test_compute_next_run_timezone_invalida_cae_a_utc_y_loguea(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # Una zona rota NO puede tumbar el barrido: `automation_scan` recorre
    # todos los tenants en una pasada, así que una excepción acá dejaría sin
    # reprogramar también a los vecinos sanos.
    after = datetime(2026, 1, 1, 20, 0, tzinfo=UTC)
    with caplog.at_level(logging.WARNING, logger="edecan_automations.engine"):
        siguiente = compute_next_run(
            "FREQ=DAILY;BYHOUR=9;BYMINUTE=0", after=after, timezone="America/Bogotá"
        )
    assert siguiente == compute_next_run("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", after=after)
    assert any("America/Bogot" in r.getMessage() for r in caplog.records)


def test_compute_next_run_con_timezone_conserva_la_fase_al_recomputar() -> None:
    # Mismo invariante que `..._con_anchor_no_deriva`, pero con huso: el
    # `anchor` que vuelve de la base es UTC y se reinterpreta en la zona, así
    # que la hora LOCAL debe quedarse clavada corrida tras corrida.
    anchor = compute_next_run(
        "FREQ=DAILY;BYHOUR=9",
        after=datetime(2026, 1, 1, 20, 32, 7, tzinfo=UTC),
        timezone="America/Bogota",
    )
    assert anchor is not None
    assert anchor == datetime(2026, 1, 2, 14, 32, 7, tzinfo=UTC)

    for jitter_segundos in (0, 7, 13, 22):
        sondeo = anchor + timedelta(seconds=jitter_segundos)
        siguiente = compute_next_run(
            "FREQ=DAILY;BYHOUR=9", after=sondeo, anchor=anchor, timezone="America/Bogota"
        )
        assert siguiente == anchor + timedelta(days=1)
        anchor = siguiente


def test_compute_next_run_zona_con_dst_fija_la_hora_local_no_el_instante_utc() -> None:
    # Nueva York cambia la hora el 8-mar-2026 (2:00 -> 3:00 local). Una
    # recurrencia diaria a las 9:00 locales debe seguir siendo las 9:00 locales
    # después del cambio: el instante UTC se corre solo (14:00 -> 13:00), que
    # es justo lo que NO pasa evaluando todo en UTC.
    antes = compute_next_run(
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        after=datetime(2026, 3, 6, 20, 0, tzinfo=UTC),
        timezone="America/New_York",
    )
    despues = compute_next_run(
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0",
        after=datetime(2026, 3, 9, 20, 0, tzinfo=UTC),
        timezone="America/New_York",
    )
    assert antes == datetime(2026, 3, 7, 14, 0, tzinfo=UTC)  # EST, UTC-5
    assert despues == datetime(2026, 3, 10, 13, 0, tzinfo=UTC)  # EDT, UTC-4


# ---------------------------------------------------------------------------
# normalize_timezone
# ---------------------------------------------------------------------------


def test_normalize_timezone_acepta_iana_valida() -> None:
    assert normalize_timezone("America/Bogota") == "America/Bogota"
    assert normalize_timezone("  America/Bogota  ") == "America/Bogota"


def test_normalize_timezone_vacia_o_none_es_utc() -> None:
    assert normalize_timezone(None) == "UTC"
    assert normalize_timezone("") == "UTC"
    assert normalize_timezone("   ") == "UTC"
    assert normalize_timezone("utc") == "UTC"


def test_normalize_timezone_invalida_cae_a_utc_sin_lanzar() -> None:
    # Lo que guarda el sembrado tiene que ser lo que el motor va a interpretar
    # de verdad: si esto lanzara (o devolviera el nombre roto tal cual), la
    # fila diría "America/Bogotá" y el cron correría en UTC.
    assert normalize_timezone("America/Bogotá") == "UTC"
    assert normalize_timezone("no/existe") == "UTC"
    assert normalize_timezone("../../etc/passwd") == "UTC"
