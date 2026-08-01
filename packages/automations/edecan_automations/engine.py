"""Validación pura del `trigger`/`accion` de una automatización, y matemática
de recurrencia (`ROADMAP_V2.md` §7.4, §7.7).

Sin IO: no importa `edecan_db` ni abre sesiones — lo usan tanto
`apps/api/edecan_api/routers/automations.py`/`routers/hooks.py` (validar
antes de guardar) como `apps/worker/edecan_worker/handlers/automation_scan.py`
(recalcular `next_run_at`), siempre de forma síncrona/en memoria.

`trigger`/`accion` viajan como `dict` (el JSON crudo que entra por HTTP o que
se lee de la columna `jsonb`) porque el llamador necesita poder AUMENTARLOS
antes de validar — en concreto, `routers/automations.py` genera
`hook_secret` server-side y lo mete en el `trigger` de kind `"webhook"` ANTES
de llamar a `validate_trigger` (el cliente nunca puede proponer su propio
secreto). La forma final validada es
`edecan_schemas.automations.TriggerDef`/`AccionDef` — este módulo delega ahí
en vez de reimplementar el esquema, y solo añade la semántica que Pydantic no
puede expresar (¿la `rrule` es sintácticamente válida?).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from edecan_schemas.automations import (
    AccionDefAdapter,
    AgentInstructionAccion,
    ScheduleTrigger,
    TriggerDefAdapter,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)

__all__ = [
    "validate_trigger",
    "validate_accion",
    "compute_next_run",
    "normalize_timezone",
    "DEFAULT_TIMEZONE",
]

DEFAULT_TIMEZONE = "UTC"
"""Zona en la que se evalúa una `rrule` cuando la fila no declara ninguna.
MISMO valor que el `server_default` de `automations.timezone` (migración
`0029_social_drafts_tz`): estrenar la columna no debe mover ni un horario ya
sembrado, así que "sin zona" tiene que seguir significando exactamente lo que
significaba antes de que la columna existiera."""

# Fecha ancla arbitraria (tz-aware UTC) SOLO para probar que una `rrule` es
# sintácticamente parseable durante `validate_trigger` — no tiene ningún
# significado de negocio (a diferencia del `after`/`anchor` reales que le
# pasa `compute_next_run` a `rrulestr`, que sí le importan a quien llama).
# Si la `rrule` ya trae su propio `DTSTART` (RFC 5545), `rrulestr` lo usa a
# ese en vez de este `dtstart` de respaldo.
_VALIDATION_ANCHOR = datetime(2020, 1, 1, tzinfo=UTC)


def _mensaje_de(exc: ValidationError) -> str:
    """Primer error de una `pydantic.ValidationError`, en una sola línea
    legible (evita volcar el `repr` completo, multilínea, de Pydantic)."""
    errores = exc.errors()
    if not errores:
        return str(exc)
    primero = errores[0]
    campo = ".".join(str(parte) for parte in primero.get("loc", ())) or "valor"
    return f"{campo}: {primero.get('msg', 'inválido')}"


def validate_trigger(trigger: dict[str, Any]) -> None:
    """Valida `trigger` (`{"kind": "schedule", "rrule": ...}` o
    `{"kind": "webhook", "hook_secret": ...}`) contra
    `edecan_schemas.automations.TriggerDef`. Lanza `ValueError` (nunca
    `pydantic.ValidationError`, para que los callers —routers, la tool—
    solo necesiten atrapar un tipo) si `kind` no es uno de los dos
    reconocidos, si falta el campo que le corresponde a ese `kind`, o (para
    `"schedule"`) si `rrule` no es una regla RFC 5545 sintácticamente válida.
    """
    try:
        parsed = TriggerDefAdapter.validate_python(trigger)
    except ValidationError as exc:
        raise ValueError(f"trigger inválido: {_mensaje_de(exc)}") from exc

    if isinstance(parsed, ScheduleTrigger):
        try:
            rrulestr(parsed.rrule, dtstart=_VALIDATION_ANCHOR)
        except Exception as exc:  # noqa: BLE001 - dateutil lanza varios tipos distintos
            raise ValueError(f"trigger.rrule inválida: {exc}") from exc


def validate_accion(accion: dict[str, Any]) -> None:
    """Valida `accion` (`{"kind": "agent_instruction", "instruccion": ...}`
    o `{"kind": "create_linkedin_post", ...}`) contra
    `edecan_schemas.automations.AccionDef` (unión discriminada por `kind`,
    ver su docstring). Lanza `ValueError` si `kind` no es uno de los
    reconocidos, si a `"schedule"` le falta el campo que le corresponde, o
    (solo para `"agent_instruction"`) si `instruccion` falta o queda vacía
    tras recortar espacios (Pydantic por sí solo aceptaría `""` o `"   "`:
    son `str` válidos, así que ese chequeo extra vive acá). A diferencia de
    antes de que `AccionDef` fuera una unión real, el `kind` de entrada
    AHORA es obligatorio: una unión discriminada de Pydantic v2 no tolera
    inferirlo desde el default de ningún miembro cuando el dict de entrada
    no lo trae — todo caller de este repo ya lo manda explícito
    (`tools.py::_crear`, `routers/automations.py::_normalize_accion_in`,
    el sembrador de `apps/local/edecan_local/linkedin_automations_seed.py`)."""
    try:
        parsed = AccionDefAdapter.validate_python(accion)
    except ValidationError as exc:
        raise ValueError(f"accion inválida: {_mensaje_de(exc)}") from exc

    if isinstance(parsed, AgentInstructionAccion) and not parsed.instruccion.strip():
        raise ValueError("accion.instruccion no puede estar vacía.")


def _zona_horaria(nombre: str | None) -> tzinfo:
    """`tzinfo` del huso `nombre` (IANA, p. ej. `"America/Bogota"`), o UTC si
    viene vacío/inválido. **Nunca lanza, a propósito.**

    Una zona rota (un typo del dueño, una fila importada de otro sistema, un
    huso que el SO ya no trae en su base de `tzdata`) NO puede tumbar el
    barrido: `handlers/automation_scan.py` recorre TODOS los tenants en una
    sola pasada, así que una excepción acá dejaría también sin reprogramar las
    automatizaciones sanas de los demás — un typo de un tenant apagaría el
    cron del vecino. Se cae al comportamiento histórico (UTC) y se loguea con
    el nombre ofensor, que es lo único accionable para corregirlo.
    """
    if not isinstance(nombre, str):
        return UTC
    limpio = nombre.strip()
    # El caso abrumadoramente común: se evita construir un `ZoneInfo` (que lee
    # `tzdata` del disco la primera vez) para la zona que ya trae `datetime`.
    if not limpio or limpio.upper() == DEFAULT_TIMEZONE:
        return UTC
    try:
        return ZoneInfo(limpio)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError(KeyError), ValueError, OSError...
        logger.warning(
            "zona horaria %r inválida o desconocida; se evalúa la rrule en %s.",
            limpio,
            DEFAULT_TIMEZONE,
        )
        return UTC


def normalize_timezone(timezone: str | None) -> str:
    """Nombre de huso ya validado, listo para guardar en
    `automations.timezone`: el mismo `timezone` si `zoneinfo` lo reconoce,
    `"UTC"` si viene vacío/inválido (con el mismo warning que `_zona_horaria`).

    Existe para que quien ESCRIBE la fila (hoy
    `apps/local/edecan_local/linkedin_automations_seed.py`) valide con
    exactamente el mismo criterio que quien después la LEE (`compute_next_run`):
    si la validación viviera duplicada del lado del escritor, una zona podría
    pasar el filtro al guardarse y caer a UTC en silencio al dispararse — el
    horario real terminaría siendo otro que el que muestra la fila.
    """
    zona = _zona_horaria(timezone)
    # `str(ZoneInfo("America/Bogota")) == "America/Bogota"` (su `__str__` es la
    # clave IANA). El `is UTC` distingue exacto el camino de respaldo: es el
    # mismo objeto singleton que devuelve `_zona_horaria` para vacío/inválido.
    return DEFAULT_TIMEZONE if zona is UTC else str(zona)


def compute_next_run(
    rrule: str,
    after: datetime,
    *,
    anchor: datetime | None = None,
    timezone: str | None = None,
) -> datetime | None:
    """Próxima ocurrencia de `rrule` estrictamente posterior a `after`.

    `timezone` (nombre IANA, `automations.timezone`) es el huso en el que se
    interpreta la regla: `"FREQ=DAILY;BYHOUR=9;BYMINUTE=0"` con
    `timezone="America/Bogota"` significa las 9 de la mañana **de Bogotá**, y
    devuelve las 14:00 UTC. Por default es `None` = `DEFAULT_TIMEZONE` = el
    comportamiento histórico exacto (todo en UTC), para que ninguna fila que
    no declare zona cambie de horario. Ese default es justamente lo que hacía
    que un slot sembrado como "09:00" disparara a las 4:00 a.m. en Bogotá:
    nadie mentía, la regla siempre fue UTC — lo que faltaba era poder decir en
    qué huso se lee.

    **El contrato de entrada/salida sigue siendo UTC absoluto.** `after` y
    `anchor` son instantes (naive = UTC, tz-aware = respetado) y el resultado
    vuelve SIEMPRE tz-aware UTC, sin importar `timezone`: quien llama guarda
    ese valor en `automations.next_run_at` (`TIMESTAMP(timezone=True)`) y lo
    compara contra `now()`, así que un retorno en hora local sería un bug de
    cuatro/cinco horas. `timezone` solo cambia el RELOJ DE PARED contra el que
    se evalúan `BYHOUR`/`BYMINUTE`/`BYDAY`, no el tipo de dato que sale.

    Con un huso que sí tiene horario de verano, esto es además lo correcto
    para una recurrencia: se fija la hora LOCAL (las 9 siguen siendo las 9
    después del cambio de hora) y el instante UTC se corre solo. En la hora que
    el cambio de horario borra una vez al año, `zoneinfo` resuelve el reloj de
    pared inexistente con `fold=0` en vez de reventar — la corrida de ese día
    se desplaza una hora, nunca se pierde.

    `anchor` fija la FASE de la recurrencia (el `dtstart` que ve
    `rrulestr` cuando `rrule` no trae su propio `DTSTART` ni fija
    `BYMINUTE`/`BYSECOND` explícitos — RFC 5545 hereda esos campos de
    `dtstart`). Por default es `None`, que reutiliza `after` como ancla
    (correcto para un caller que está fijando la fase POR PRIMERA VEZ —
    `tools.py::_crear`, `routers/automations.py::_next_run_for` — ahí no
    hay otro valor disponible). Un caller que en cambio RECOMPUTA
    `next_run_at` en cada ciclo sin que la `rrule` haya cambiado
    (`handlers/automation_scan.py`) DEBE pasar el `next_run_at` ya
    persistido como `anchor`: si en su lugar se reutiliza el `after`
    volátil del sondeo (`datetime.now(UTC)`) como ancla en cada recomputo,
    la fase (minuto/segundo) deriva sin fin de un ciclo a otro — mismo
    patrón correcto que `edecan_worker.repo._next_occurrence(due_at,
    rrule, after=...)`, que ancla en `due_at` (el valor persistido) y usa
    `after` solo como filtro de búsqueda.

    `after`/`anchor` pueden ser naive (se asumen UTC) o tz-aware; el
    resultado, si lo hay, siempre vuelve tz-aware UTC. Devuelve `None` si
    la regla ya se agotó (p. ej. `UNTIL` ya pasó o se alcanzó `COUNT`) — un
    `None` es una respuesta VÁLIDA, no un error: los callers
    (`routers/automations.py`, `handlers/automation_scan.py`) lo tratan
    como "esta automatización no vuelve a dispararse sola". Si `rrule` es
    sintácticamente inválida, en cambio, sí lanza `ValueError` — no
    debería ocurrir en la práctica porque `validate_trigger` ya la validó
    antes de guardarla, pero un caller no debe asumir eso ciegamente (p.
    ej. una fila vieja escrita antes de un cambio de validación). Una
    `timezone` inválida, en cambio, NO lanza: cae a UTC y loguea (ver
    `_zona_horaria` para el porqué).
    """
    zona = _zona_horaria(timezone)

    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)

    dtstart = after if anchor is None else anchor
    if dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    # `dateutil` no convierte husos: arma cada ocurrencia con los campos de
    # calendario de `dtstart` y le RE-PEGA su mismo `tzinfo` (`.replace`), así
    # que `BYHOUR=9` significa "las 9 del reloj que traiga `dtstart`". Mover
    # ambos instantes a `zona` ANTES de iterar es lo que hace que la regla se
    # lea en hora local; `after` va también convertido para que la comparación
    # de `rule.after()` ocurra entre dos datetimes del mismo huso (son
    # instantes, el resultado no cambiaría, pero deja el contrato explícito).
    dtstart = dtstart.astimezone(zona)
    after = after.astimezone(zona)

    try:
        rule = rrulestr(rrule, dtstart=dtstart)
    except Exception as exc:  # noqa: BLE001 - dateutil lanza varios tipos distintos
        raise ValueError(f"rrule inválida: {exc}") from exc

    siguiente = rule.after(after, inc=False)
    if siguiente is None:
        return None
    if siguiente.tzinfo is None:
        # Solo pasa si la `rrule` trae un `DTSTART` propio SIN huso: ahí
        # `dateutil` ignora el `dtstart` que le pasamos y produce datetimes
        # naive. Se interpretan en `zona` (el reloj en el que se pidió leer la
        # regla), no en UTC — si no, la misma regla saltaría de huso según
        # traiga o no `DTSTART`.
        siguiente = siguiente.replace(tzinfo=zona)
    # Siempre UTC hacia afuera: ver "El contrato de entrada/salida" arriba.
    return siguiente.astimezone(UTC)
