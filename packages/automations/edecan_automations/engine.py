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

from datetime import UTC, datetime
from typing import Any

from dateutil.rrule import rrulestr
from edecan_schemas.automations import (
    AccionDefAdapter,
    ScheduleTrigger,
    TriggerDefAdapter,
)
from pydantic import ValidationError

__all__ = ["validate_trigger", "validate_accion", "compute_next_run"]

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
    """Valida `accion` (`{"kind": "agent_instruction", "instruccion": ...}`)
    contra `edecan_schemas.automations.AccionDef`. Lanza `ValueError` si
    `kind` no es `"agent_instruction"` o si `instruccion` falta o queda
    vacía tras recortar espacios (Pydantic por sí solo aceptaría `""` o
    `"   "`: son `str` válidos, así que ese chequeo extra vive acá)."""
    try:
        parsed = AccionDefAdapter.validate_python(accion)
    except ValidationError as exc:
        raise ValueError(f"accion inválida: {_mensaje_de(exc)}") from exc

    if not parsed.instruccion.strip():
        raise ValueError("accion.instruccion no puede estar vacía.")


def compute_next_run(
    rrule: str, after: datetime, *, anchor: datetime | None = None
) -> datetime | None:
    """Próxima ocurrencia de `rrule` estrictamente posterior a `after`.

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
    ej. una fila vieja escrita antes de un cambio de validación).
    """
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)

    dtstart = after if anchor is None else anchor
    if dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    try:
        rule = rrulestr(rrule, dtstart=dtstart)
    except Exception as exc:  # noqa: BLE001 - dateutil lanza varios tipos distintos
        raise ValueError(f"rrule inválida: {exc}") from exc

    siguiente = rule.after(after, inc=False)
    if siguiente is None:
        return None
    if siguiente.tzinfo is None:
        siguiente = siguiente.replace(tzinfo=UTC)
    return siguiente
