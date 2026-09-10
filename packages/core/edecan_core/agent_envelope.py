"""Restricciones del envelope de un mensaje inter-agente (BOTS-03).

Helpers PURAS y sin I/O para que el runner (`run_persistent_agent`) aplique el
envelope de una delegación sin acoplar el worker al router de la API.

El envelope viaja dentro del payload del job (`env.payload["envelope"]`), que el
emisor (`agent_messages.py`) rellena desde la fila canónica de `agent_messages`.
Acá solo se transforma lo que ya llegó: no se lee DB, no se valida emisor/
receptor y no se resuelven dependencias — eso lo hace el runner al reclamar (ver
el punto de cableado documentado en el reporte de implementación).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from edecan_core.tools import ToolRegistry

# Campos del envelope que se transmiten al runner. `deadline` se serializa como
# ISO-8601 en el payload (ver `agent_messages._build_envelope`); acá se toleran
# las dos formas (str ISO o datetime) para no romper envelopes legacy.
_ENVELOPE_DEADLINE_KEY = "deadline"
_ENVELOPE_ALLOWED_TOOLS_KEY = "allowed_tools"


def apply_envelope_restrictions(registry: Any, envelope: Mapping[str, Any]) -> Any:
    """Restringe `registry` a las tools declaradas en `envelope["allowed_tools"]`.

    Regla BOTS-03: una delegación solo puede RESTRINGIR, jamás AMPLIAR. El
    resultado es la INTERSECCIÓN de las tools que `registry` ya expone y los
    nombres declarados en `allowed_tools`. Un envelope sin `allowed_tools`
    (o con lista vacía — envelope legacy) devuelve `registry` sin cambios.

    Pura y determinista: no lee configuración ni confirmaciones; solo copia
    las instancias de tool ya presentes en `registry` hacia un `ToolRegistry`
    nuevo. `registry` necesita la superficie que usa `Agent.run_turn`:
    `.all()` (para enumerar) — el resultado es un `ToolRegistry` estándar.
    """
    allowed = envelope.get(_ENVELOPE_ALLOWED_TOOLS_KEY) if isinstance(envelope, Mapping) else None
    if not allowed:
        return registry
    allowed_names = frozenset(str(name) for name in allowed)
    restricted = ToolRegistry()
    for tool in getattr(registry, "all", lambda: [])():
        name = str(getattr(tool, "name", ""))
        if name and name in allowed_names:
            restricted.register(tool)
    return restricted


def apply_envelope_extra_tools_filter(
    extra_tools: Iterable[Any], envelope: Mapping[str, Any]
) -> list[Any]:
    """Filtra `extra_tools` (persona + MCP) por `envelope["allowed_tools"]` (F2-HIGH).

    El runner recibe las tools extra (persona + MCP) FUERA del `ToolRegistry`; sin
    este filtro, `apply_envelope_restrictions` recorta solo el registry y las
    tools extra pasan SIN restricción. Misma semántica de INTERSECCIÓN (BOTS-03):
    `allowed_tools` no vacío → solo quedan las tools cuyo nombre está nombrado;
    sin `allowed_tools` (o vacío — envelope legacy) → devuelve la lista sin
    cambios. Pura y determinista: no lee I/O, no amplía nada.
    """
    allowed = envelope.get(_ENVELOPE_ALLOWED_TOOLS_KEY) if isinstance(envelope, Mapping) else None
    if not allowed:
        return list(extra_tools)
    allowed_names = frozenset(str(name) for name in allowed)
    return [tool for tool in extra_tools if str(getattr(tool, "name", "")) in allowed_names]


def envelope_expired(envelope: Mapping[str, Any], *, now: datetime | None = None) -> bool:
    """`True` si `envelope["deadline"]` existe y ya venció respecto a `now`.

    Sin `deadline` (o sin envelope) devuelve `False`: la delegación no impone
    límite temporal. Un `deadline` no parseable también devuelve `False`
    (falla abierto hacia "ejecutar", para no bloquear por un dato corrupto;
    el runner lo registra aparte si quiere ser estricto). `now` default:
    hora UTC actual. Pura: solo compara fechas.
    """
    deadline = envelope.get(_ENVELOPE_DEADLINE_KEY) if isinstance(envelope, Mapping) else None
    if deadline is None:
        return False
    if isinstance(deadline, datetime):
        dt = deadline
    elif isinstance(deadline, str):
        try:
            dt = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    reference = now if now is not None else datetime.now(UTC)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=UTC)
    return dt < reference