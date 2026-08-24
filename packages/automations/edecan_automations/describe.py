"""Traducción de una `rrule` RFC 5545 a una frase que una persona entienda
("Todos los días a las 4:00 UTC", "Cada 15 minutos") — puro, sin IO, igual
que `engine.py`.

**Por qué vive en el servidor y no en cada cliente.** La frase incluye una
HORA, y una hora sin zona horaria es una mentira a medias. Quien sabe en qué
zona corre de verdad la regla es este backend — `engine.compute_next_run`
evalúa la `rrule` en el huso de la fila (`automations.timezone`, `"UTC"` si
no declara ninguno), así que `BYHOUR=4` significa las 04:00 **de ese huso**—;
ni la web ni el teléfono pueden deducirlo del JSON. Por eso
`routers/automations.py` `_public_automation` publica el texto ya armado en
`trigger.descripcion` y las dos superficies solo lo pintan: una sola
implementación, una sola tabla de verdad sobre la zona.

**Por qué se rotula la zona en vez de convertir a la hora del teléfono.**
Convertir una RECURRENCIA (no un instante) de zona es traicionero: "los
lunes a las 23:00 UTC" es domingo por la noche en América, así que el día
de la semana también se movería, y una regla como `FREQ=MINUTELY` ni
siquiera tiene hora que convertir. Se muestra la hora tal como corre, con
su zona explícita; el instante concreto en la hora del usuario ya lo dan
las dos superficies aparte, en "Próxima: ..." (`next_run_at`, que sí es un
instante absoluto y el cliente sí puede localizar sin ambigüedad).

**El rótulo lo pone quien llama, y tiene que ser el huso REAL de la fila.**
`describe_rrule(rrule)` sin `timezone` rotula `"UTC"` porque ese es el huso
en el que corre una fila que no declara ninguno — pero una automatización
sembrada con `BYHOUR=9` + `timezone="America/Bogota"` descrita sin pasarle
su huso diría "a las 9:00 UTC", que es la hora equivocada por cinco horas y
además ni siquiera es la que el dueño ve dispararse. Quien serializa la fila
DEBE pasarle su columna `timezone` (ver `_public_automation`).

**Cuándo devuelve `None`.** Solo se traduce lo que se puede afirmar con
certeza; ante la duda, `None` — y quien llama muestra algo honesto
("Agenda personalizada" + la regla cruda) en vez de una hora inventada.
Una hora equivocada es peor que una hora técnica. En concreto se rinde
ante `COUNT`/`UNTIL` (la frase diría "todos los días" de algo que se
acaba), `BYSETPOS`/`BYMONTH`/`BYWEEKNO`/`BYYEARDAY`/`WKST`, los `BYDAY`
con ordinal (`"2FR"`), `FREQ=YEARLY`/`SECONDLY` y cualquier `DTSTART`
embebido.

**El caso `BYHOUR` sin `BYMINUTE`.** RFC 5545 hereda de `dtstart` las
partes que la regla no fija, y el `dtstart` de Edecán es el momento en que
se creó la automatización (`engine.compute_next_run`, param `anchor`). Así
que `FREQ=DAILY;BYHOUR=9` creada a las 15:39 corre a las **9:39**, no a las
9:00 — decir "a las 9:00" sería falso. Como el minuto queda fijo pero es
imposible saberlo desde la regla sola, se acota la hora sin inventarla:
"entre las 9:00 y las 9:59 UTC".

**El caso `BYMONTHDAY` 29/30/31.** Mismo criterio: un mes que no tiene ese
día se SALTA (no corre el 28 ni el 30), así que "de cada mes" prometería
corridas que no existen — se dice "de los meses que lo tienen".
"""

from __future__ import annotations

from .engine import normalize_timezone

__all__ = ["describe_rrule", "SCHEDULE_TIMEZONE"]

SCHEDULE_TIMEZONE = "UTC"
"""Zona en la que `engine.compute_next_run` evalúa una `rrule` cuando la fila
no declara ninguna — el mismo `engine.DEFAULT_TIMEZONE` y el mismo
`server_default` de la columna `automations.timezone`. Se conserva el nombre
(y el valor) porque es el rótulo por default de este módulo; la zona de una
fila concreta entra por el parámetro `timezone` de `describe_rrule`."""

# Plural, que es como se usan siempre acá ("Todos los lunes", "Los lunes y
# jueves"): lunes..viernes son invariables, sábado/domingo no.
_DAY_NAMES: dict[str, str] = {
    "MO": "lunes",
    "TU": "martes",
    "WE": "miércoles",
    "TH": "jueves",
    "FR": "viernes",
    "SA": "sábados",
    "SU": "domingos",
}
_WEEK_ORDER: tuple[str, ...] = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")
_WORKWEEK: list[str] = ["MO", "TU", "WE", "TH", "FR"]

# Todo lo que NO esté acá hace que nos rindamos (ver docstring): la lista es
# de cosas que sabemos decir, no de cosas que sabemos ignorar.
_KNOWN_KEYS = frozenset({"FREQ", "INTERVAL", "BYHOUR", "BYMINUTE", "BYDAY", "BYMONTHDAY"})
_BY_KEYS = frozenset({"BYHOUR", "BYMINUTE", "BYDAY", "BYMONTHDAY"})


def _parse(rrule: str) -> dict[str, str] | None:
    """`"FREQ=DAILY;BYHOUR=9"` → `{"FREQ": "DAILY", "BYHOUR": "9"}`, o `None`
    si trae algo que este módulo no sabe traducir con certeza."""
    texto = rrule.strip()
    if not texto or "\n" in texto or "\r" in texto:
        return None

    # `rrulestr` acepta tanto `"FREQ=..."` pelado como el `"RRULE:FREQ=..."`
    # de RFC 5545. Cualquier OTRO `:` significa que hay más de una línea de
    # contenido (`DTSTART:`, `EXDATE:`) — ahí la recurrencia ya no se explica
    # sola desde estos pares clave=valor.
    if texto.upper().startswith("RRULE:"):
        texto = texto[len("RRULE:") :]
    if ":" in texto:
        return None

    partes: dict[str, str] = {}
    for trozo in texto.split(";"):
        trozo = trozo.strip()
        if not trozo:
            continue
        clave, sep, valor = trozo.partition("=")
        if not sep:
            return None
        clave = clave.strip().upper()
        if clave in partes:  # clave repetida: ambigua, no adivinamos cuál gana
            return None
        partes[clave] = valor.strip().upper()

    if not partes or not _KNOWN_KEYS.issuperset(partes):
        return None
    return partes


def _solo_admite(partes: dict[str, str], *permitidas: str) -> bool:
    """¿Los `BY*` presentes están todos dentro de `permitidas`? Cada `FREQ`
    entiende un juego distinto (`BYDAY` no significa nada en `MINUTELY`)."""
    return not (_BY_KEYS & partes.keys()) - set(permitidas)


def _lista_de_enteros(crudo: str, *, minimo: int, maximo: int) -> list[int] | None:
    valores: list[int] = []
    for pieza in crudo.split(","):
        pieza = pieza.strip()
        if not pieza.isdigit():  # descarta vacíos y negativos (p. ej. BYMONTHDAY=-1)
            return None
        numero = int(pieza)
        if not minimo <= numero <= maximo:
            return None
        valores.append(numero)
    return sorted(set(valores)) or None


def _intervalo(partes: dict[str, str]) -> int | None:
    crudo = partes.get("INTERVAL")
    if crudo is None:
        return 1
    if not crudo.isdigit() or int(crudo) < 1:
        return None
    return int(crudo)


def _enumerar(elementos: list[str]) -> str:
    """`["a", "b", "c"]` → `"a, b y c"`. Ningún nombre de día empieza por
    `i`/`hi`, así que nunca hace falta la variante "e"."""
    if len(elementos) == 1:
        return elementos[0]
    return f"{', '.join(elementos[:-1])} y {elementos[-1]}"


def _dias(crudo: str | None) -> list[str] | None:
    """`"MO,WE"` → `["MO", "WE"]` en orden de semana. `None` ante un `BYDAY`
    con ordinal (`"2FR"` = "el 2do viernes"), que no sabemos decir."""
    if crudo is None:
        return None
    codigos: list[str] = []
    for pieza in crudo.split(","):
        pieza = pieza.strip()
        if pieza not in _DAY_NAMES:
            return None
        codigos.append(pieza)
    if not codigos:
        return None
    return sorted(set(codigos), key=_WEEK_ORDER.index)


def _frase_horaria(partes: dict[str, str], zona: str) -> str | None:
    """Sufijo de hora, ya con su zona: `" a las 9:00 America/Bogota"`. `""`
    cuando la regla no fija ninguna hora (no hay nada que afirmar), `None`
    cuando la fija de una forma que no sabemos decir sin inventar."""
    horas_crudas = partes.get("BYHOUR")
    minutos_crudos = partes.get("BYMINUTE")

    if horas_crudas is None:
        # `BYMINUTE` suelto (sin `BYHOUR`) en una regla diaria/semanal deja la
        # HORA heredada del alta: no hay frase honesta posible.
        return None if minutos_crudos is not None else ""

    horas = _lista_de_enteros(horas_crudas, minimo=0, maximo=23)
    if horas is None:
        return None

    if minutos_crudos is None:
        # Minuto heredado del alta (ver docstring del módulo): se acota la
        # hora en vez de inventar ":00".
        if len(horas) != 1:
            return None
        hora = horas[0]
        return f" entre las {hora}:00 y las {hora}:59 {zona}"

    minutos = _lista_de_enteros(minutos_crudos, minimo=0, maximo=59)
    if minutos is None or len(minutos) != 1:
        return None
    minuto = minutos[0]
    return f" a las {_enumerar([f'{h}:{minuto:02d}' for h in horas])} {zona}"


def describe_rrule(rrule: str, *, timezone: str | None = None) -> str | None:
    """Frase en español para `rrule`, o `None` si no se puede traducir con
    certeza (ver docstring del módulo — quien llama debe mostrar algo honesto,
    no una traducción aproximada).

    `timezone` es la columna `automations.timezone` de la fila que se está
    describiendo (nombre IANA); `None`/vacío/inválido rotula
    `SCHEDULE_TIMEZONE` = `"UTC"`, que es exactamente el huso en el que esa
    fila corre. Pasa por `engine.normalize_timezone` para que el rótulo no
    pueda diferir de la zona que el motor va a usar de verdad: una zona con
    typo cae a UTC en los dos lados a la vez, nunca "dice Bogotá y corre en
    UTC".

    La hora que devuelve es la hora en que la regla CORRE, rotulada con su
    zona; nunca se convierte a la zona de quien lee.
    """
    partes = _parse(rrule)
    if partes is None:
        return None

    zona = normalize_timezone(timezone)

    intervalo = _intervalo(partes)
    if intervalo is None:
        return None
    freq = partes.get("FREQ")

    if freq == "MINUTELY":
        if not _solo_admite(partes):
            return None
        return "Cada minuto" if intervalo == 1 else f"Cada {intervalo} minutos"

    if freq == "HOURLY":
        if not _solo_admite(partes, "BYMINUTE"):
            return None
        base = "Cada hora" if intervalo == 1 else f"Cada {intervalo} horas"
        crudo = partes.get("BYMINUTE")
        if crudo is None:
            return base
        minutos = _lista_de_enteros(crudo, minimo=0, maximo=59)
        if minutos is None or len(minutos) != 1:
            return None
        return f"{base} en punto" if minutos[0] == 0 else f"{base} al minuto {minutos[0]}"

    if freq == "DAILY":
        if not _solo_admite(partes, "BYHOUR", "BYMINUTE"):
            return None
        hora = _frase_horaria(partes, zona)
        if hora is None:
            return None
        base = "Todos los días" if intervalo == 1 else f"Cada {intervalo} días"
        return base + hora

    if freq == "WEEKLY":
        if not _solo_admite(partes, "BYDAY", "BYHOUR", "BYMINUTE"):
            return None
        # Sin `BYDAY` el día de la semana lo hereda del alta — nombrarlo sería
        # adivinar, igual que el minuto sin `BYMINUTE`.
        dias = _dias(partes.get("BYDAY"))
        if dias is None:
            return None
        hora = _frase_horaria(partes, zona)
        if hora is None:
            return None
        nombres = [_DAY_NAMES[d] for d in dias]
        if intervalo != 1:
            return f"Cada {intervalo} semanas, los {_enumerar(nombres)}{hora}"
        if len(dias) == len(_WEEK_ORDER):
            return f"Todos los días{hora}"
        if dias == _WORKWEEK:
            return f"De lunes a viernes{hora}"
        if len(dias) == 1:
            return f"Todos los {nombres[0]}{hora}"
        return f"Los {_enumerar(nombres)}{hora}"

    if freq == "MONTHLY":
        if not _solo_admite(partes, "BYMONTHDAY", "BYHOUR", "BYMINUTE"):
            return None
        crudo = partes.get("BYMONTHDAY")
        if crudo is None:  # día del mes heredado del alta: mismo caso que BYDAY
            return None
        dias_mes = _lista_de_enteros(crudo, minimo=1, maximo=31)
        if dias_mes is None or len(dias_mes) != 1:
            return None
        hora = _frase_horaria(partes, zona)
        if hora is None:
            return None
        dia = dias_mes[0]
        # Los días 29-31 no existen en todos los meses, y RFC 5545 SALTA el mes que no
        # los tiene en vez de correr el 28 o el 30 (verificado contra
        # `compute_next_run`: `BYMONTHDAY=31` corre en julio, agosto y octubre, pero
        # no en septiembre). "De cada mes" prometería una corrida que nunca ocurre —
        # el mismo tipo de mentira que el minuto inventado en `_frase_horaria`. Con
        # `INTERVAL` encima no hay forma corta de decirlo sin enredar, así que ahí se
        # prefiere no traducir.
        if dia >= 29:
            if intervalo != 1:
                return None
            return f"El día {dia} de los meses que lo tienen{hora}"
        base = (
            f"El día {dia} de cada mes"
            if intervalo == 1
            else f"El día {dia} cada {intervalo} meses"
        )
        return base + hora

    return None
