"""Tests de `edecan_automations.describe` — puro, sin IO, sin fakes de sesión.

Además de comprobar el texto, `test_la_hora_descrita_es_la_hora_en_que_de
_verdad_corre` cruza la frase contra `engine.compute_next_run`: es la única
manera de que un cambio futuro en el anclaje/zona del motor no deje esta
traducción mintiendo en silencio.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from edecan_automations.describe import describe_rrule
from edecan_automations.engine import compute_next_run

# ---------------------------------------------------------------------------
# Diaria a una hora fija
# ---------------------------------------------------------------------------


def test_diaria_a_una_hora() -> None:
    assert describe_rrule("FREQ=DAILY;BYHOUR=4;BYMINUTE=0") == "Todos los días a las 4:00 UTC"


def test_diaria_con_minuto_no_redondo() -> None:
    assert describe_rrule("FREQ=DAILY;BYHOUR=14;BYMINUTE=30") == "Todos los días a las 14:30 UTC"


def test_diaria_dos_veces_al_dia() -> None:
    assert (
        describe_rrule("FREQ=DAILY;BYHOUR=8,20;BYMINUTE=0")
        == "Todos los días a las 8:00 y 20:00 UTC"
    )


def test_cada_n_dias() -> None:
    assert (
        describe_rrule("FREQ=DAILY;INTERVAL=3;BYHOUR=9;BYMINUTE=0") == "Cada 3 días a las 9:00 UTC"
    )


def test_diaria_sin_byminute_acota_la_hora_en_vez_de_inventar_el_minuto() -> None:
    # `FREQ=DAILY;BYHOUR=9` NO corre a las 9:00: el minuto lo hereda del alta
    # (ver docstring del módulo). Decir "a las 9:00" sería falso.
    assert describe_rrule("FREQ=DAILY;BYHOUR=9") == "Todos los días entre las 9:00 y las 9:59 UTC"


def test_diaria_sin_hora_no_afirma_ninguna() -> None:
    assert describe_rrule("FREQ=DAILY") == "Todos los días"


# ---------------------------------------------------------------------------
# Cada N minutos / horas
# ---------------------------------------------------------------------------


def test_cada_n_minutos() -> None:
    assert describe_rrule("FREQ=MINUTELY;INTERVAL=15") == "Cada 15 minutos"


def test_cada_minuto_sin_interval() -> None:
    assert describe_rrule("FREQ=MINUTELY") == "Cada minuto"


def test_cada_hora() -> None:
    assert describe_rrule("FREQ=HOURLY") == "Cada hora"


def test_cada_hora_en_punto() -> None:
    assert describe_rrule("FREQ=HOURLY;BYMINUTE=0") == "Cada hora en punto"


def test_cada_n_horas_al_minuto() -> None:
    assert describe_rrule("FREQ=HOURLY;INTERVAL=6;BYMINUTE=30") == "Cada 6 horas al minuto 30"


# ---------------------------------------------------------------------------
# Semanal
# ---------------------------------------------------------------------------


def test_semanal_un_dia() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0")
        == "Todos los lunes a las 9:00 UTC"
    )


def test_semanal_varios_dias() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=MO,WE,FR;BYHOUR=8;BYMINUTE=0")
        == "Los lunes, miércoles y viernes a las 8:00 UTC"
    )


def test_semanal_ordena_los_dias_por_semana_no_por_como_vienen() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=FR,MO;BYHOUR=8;BYMINUTE=0")
        == "Los lunes y viernes a las 8:00 UTC"
    )


def test_semanal_dia_con_tilde_en_plural() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=SA;BYHOUR=10;BYMINUTE=0")
        == "Todos los sábados a las 10:00 UTC"
    )


def test_semanal_lunes_a_viernes() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR;BYHOUR=7;BYMINUTE=0")
        == "De lunes a viernes a las 7:00 UTC"
    )


def test_cada_n_semanas() -> None:
    assert (
        describe_rrule("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO;BYHOUR=9;BYMINUTE=0")
        == "Cada 2 semanas, los lunes a las 9:00 UTC"
    )


def test_semanal_sin_byday_no_adivina_el_dia() -> None:
    # El día de la semana lo heredaría del alta, igual que el minuto.
    assert describe_rrule("FREQ=WEEKLY;BYHOUR=9;BYMINUTE=0") is None


# ---------------------------------------------------------------------------
# Mensual
# ---------------------------------------------------------------------------


def test_mensual() -> None:
    assert (
        describe_rrule("FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0")
        == "El día 1 de cada mes a las 9:00 UTC"
    )


def test_cada_n_meses() -> None:
    assert (
        describe_rrule("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=15;BYHOUR=9;BYMINUTE=0")
        == "El día 15 cada 3 meses a las 9:00 UTC"
    )


@pytest.mark.parametrize("dia", [29, 30, 31])
def test_mensual_dia_que_no_tienen_todos_los_meses_no_dice_de_cada_mes(dia: int) -> None:
    assert (
        describe_rrule(f"FREQ=MONTHLY;BYMONTHDAY={dia};BYHOUR=9;BYMINUTE=0")
        == f"El día {dia} de los meses que lo tienen a las 9:00 UTC"
    )


def test_mensual_dia_31_el_motor_confirma_que_hay_meses_sin_corrida() -> None:
    """La razón de ser de "de los meses que lo tienen": `BYMONTHDAY=31` NO corre en
    septiembre ni en noviembre — RFC 5545 salta el mes entero en vez de correr el 30."""
    ancla = datetime(2026, 7, 29, 15, 39, 45, tzinfo=UTC)
    rrule = "FREQ=MONTHLY;BYMONTHDAY=31;BYHOUR=9;BYMINUTE=0"

    corridas: list[datetime] = []
    cursor = ancla
    for _ in range(4):
        siguiente = compute_next_run(rrule, after=cursor, anchor=ancla)
        assert siguiente is not None
        corridas.append(siguiente)
        cursor = siguiente

    assert [(c.year, c.month) for c in corridas] == [
        (2026, 7),
        (2026, 8),
        (2026, 10),  # septiembre NO tiene 31: se salta entero
        (2026, 12),  # noviembre tampoco
    ]
    assert "de cada mes" not in (describe_rrule(rrule) or "")


def test_mensual_dia_31_con_interval_no_se_traduce() -> None:
    # "El día 31 cada 3 meses" sigue prometiendo una cadencia que la regla no
    # cumple, y no hay frase corta que lo diga bien: mejor la regla cruda.
    assert describe_rrule("FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=31;BYHOUR=9;BYMINUTE=0") is None


# ---------------------------------------------------------------------------
# Lo que NO se puede traducir: `None`, nunca una aproximación
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rrule",
    [
        # `UNTIL`/`COUNT`: "todos los días" sería mentira, esto se acaba.
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0;UNTIL=20260101T000000Z",
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0;COUNT=5",
        # "el último viernes de cada mes" y compañía.
        "FREQ=MONTHLY;BYDAY=FR;BYSETPOS=-1",
        "FREQ=MONTHLY;BYDAY=2FR;BYHOUR=9;BYMINUTE=0",
        "FREQ=MONTHLY;BYMONTHDAY=-1;BYHOUR=9;BYMINUTE=0",
        # Frecuencias que este módulo no cubre.
        "FREQ=YEARLY;BYMONTH=1;BYMONTHDAY=1",
        "FREQ=SECONDLY;INTERVAL=30",
        # `BY*` que no aplican a esa `FREQ` (o que cambian el sentido).
        "FREQ=MINUTELY;INTERVAL=15;BYHOUR=9",
        "FREQ=DAILY;BYDAY=MO;BYHOUR=9;BYMINUTE=0",
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0,30",
        "FREQ=DAILY;BYHOUR=8,20",
        "FREQ=DAILY;BYMINUTE=0",
        "FREQ=MONTHLY;BYMONTHDAY=1,15;BYHOUR=9;BYMINUTE=0",
        "FREQ=WEEKLY;BYDAY=XX;BYHOUR=9;BYMINUTE=0",
        # Más de una línea de contenido: la regla ya no se explica sola.
        "DTSTART:20260101T090000Z\nRRULE:FREQ=DAILY",
        "FREQ=DAILY;BYHOUR=9;BYMINUTE=0;WKST=SU",
        # Basura, vacíos y valores fuera de rango.
        "",
        "   ",
        "ESTO NO ES UNA RRULE",
        "FREQ=DAILY;BYHOUR=99;BYMINUTE=0",
        "FREQ=DAILY;INTERVAL=0;BYHOUR=9;BYMINUTE=0",
        "FREQ=DAILY;INTERVAL=dos;BYHOUR=9;BYMINUTE=0",
    ],
)
def test_devuelve_none_en_vez_de_una_traduccion_inventada(rrule: str) -> None:
    assert describe_rrule(rrule) is None


# ---------------------------------------------------------------------------
# Tolerancias de forma
# ---------------------------------------------------------------------------


def test_acepta_el_prefijo_rrule_de_rfc_5545() -> None:
    assert describe_rrule("RRULE:FREQ=MINUTELY;INTERVAL=15") == "Cada 15 minutos"


def test_acepta_minusculas_y_espacios() -> None:
    assert describe_rrule("  freq=daily; byhour=4; byminute=0 ") == "Todos los días a las 4:00 UTC"


# ---------------------------------------------------------------------------
# La frase contra la realidad del motor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rrule", "hora_esperada", "minuto_esperado"),
    [
        ("FREQ=DAILY;BYHOUR=4;BYMINUTE=0", 4, 0),
        ("FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0", 9, 0),
        ("FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0", 9, 0),
    ],
)
def test_la_hora_descrita_es_la_hora_en_que_de_verdad_corre(
    rrule: str, hora_esperada: int, minuto_esperado: int
) -> None:
    """La frase dice "a las H:MM UTC" — `compute_next_run` debe coincidir, en
    UTC. Si alguien cambia la zona/ancla del motor, este test cae antes de que
    las pantallas empiecen a mostrar una hora que no es."""
    # Ancla con minuto/hora "sucios" a propósito: si la frase dependiera del
    # momento del alta en lugar de la regla, acá se notaría.
    ancla = datetime(2026, 7, 29, 15, 39, 45, tzinfo=UTC)
    siguiente = compute_next_run(rrule, after=ancla)

    assert siguiente is not None
    # La frase rotula la hora como UTC: si el motor dejara de devolver UTC,
    # el rótulo pasaría a ser falso aunque los números siguieran cuadrando.
    assert siguiente.utcoffset() == timedelta(0)
    assert (siguiente.hour, siguiente.minute) == (hora_esperada, minuto_esperado)
    assert f"a las {hora_esperada}:{minuto_esperado:02d} UTC" in (describe_rrule(rrule) or "")


def test_sin_byminute_el_motor_confirma_que_el_minuto_no_es_cero() -> None:
    """Prueba la razón de ser de la frase "entre las 9:00 y las 9:59": con
    `BYHOUR` pero sin `BYMINUTE`, el minuto real sale del alta."""
    ancla = datetime(2026, 7, 29, 15, 39, 45, tzinfo=UTC)
    siguiente = compute_next_run("FREQ=DAILY;BYHOUR=9", after=ancla)

    assert siguiente is not None
    assert siguiente.hour == 9
    assert siguiente.minute == 39  # NO 0 — de ahí que no digamos "a las 9:00"
    assert describe_rrule("FREQ=DAILY;BYHOUR=9") == "Todos los días entre las 9:00 y las 9:59 UTC"


# ---------------------------------------------------------------------------
# El rótulo de zona (`timezone`, columna `automations.timezone`)
# ---------------------------------------------------------------------------


def test_describe_rotula_la_zona_de_la_fila() -> None:
    assert (
        describe_rrule("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", timezone="America/Bogota")
        == "Todos los días a las 9:00 America/Bogota"
    )
    assert (
        describe_rrule("FREQ=WEEKLY;BYDAY=MO;BYHOUR=7;BYMINUTE=30", timezone="America/Bogota")
        == "Todos los lunes a las 7:30 America/Bogota"
    )
    assert (
        describe_rrule("FREQ=DAILY;BYHOUR=9", timezone="America/Bogota")
        == "Todos los días entre las 9:00 y las 9:59 America/Bogota"
    )


def test_describe_sin_timezone_sigue_diciendo_utc() -> None:
    # Compatibilidad: una fila sin huso (todas las de hoy) corre en UTC, así
    # que su frase no cambia ni un carácter.
    frase = "Todos los días a las 4:00 UTC"
    assert describe_rrule("FREQ=DAILY;BYHOUR=4;BYMINUTE=0") == frase
    assert describe_rrule("FREQ=DAILY;BYHOUR=4;BYMINUTE=0", timezone=None) == frase
    assert describe_rrule("FREQ=DAILY;BYHOUR=4;BYMINUTE=0", timezone="UTC") == frase


def test_describe_zona_invalida_rotula_utc_igual_que_corre_el_motor() -> None:
    # La frase y el motor no pueden discrepar: si `compute_next_run` cae a UTC
    # ante un huso roto, el rótulo tiene que decir UTC — decir "America/Bogotá"
    # sería la hora correcta con la zona equivocada, la mentira exacta que
    # `describe` existe para no cometer.
    assert (
        describe_rrule("FREQ=DAILY;BYHOUR=9;BYMINUTE=0", timezone="America/Bogotá")
        == "Todos los días a las 9:00 UTC"
    )


def test_la_hora_descrita_con_zona_es_la_hora_local_en_que_de_verdad_corre() -> None:
    """Gemelo de `test_la_hora_descrita_es_la_hora_en_que_de_verdad_corre`,
    pero con huso: la frase promete "9:00 America/Bogota" y el motor debe
    disparar a las 14:00 UTC, que ES esa hora local."""
    rrule = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    ancla = datetime(2026, 7, 29, 15, 39, 45, tzinfo=UTC)
    siguiente = compute_next_run(rrule, after=ancla, timezone="America/Bogota")

    assert siguiente is not None
    assert siguiente.utcoffset() == timedelta(0)  # el retorno sigue siendo UTC
    assert siguiente.astimezone(ZoneInfo("America/Bogota")).hour == 9
    assert describe_rrule(rrule, timezone="America/Bogota") == (
        "Todos los días a las 9:00 America/Bogota"
    )
