"""Action Ledger — registro de acciones reversibles del agente (PHASE2.md §63-71).

El agente ejecuta herramientas con efectos sobre el mundo (editar un archivo,
crear una memoria, publicar un borrador). Para poder responder "¿qué
cambiaste?" (§69) y ofrecer "deshacer" (§64), cada ejecución exitosa de una
tool que declara cómo revertirse (`Tool.inverse`, ver `edecan_core.tools.base`)
deja aquí un `ActionEffect`.

Diseño en dos capas, ambas "best-effort" (nunca lanzan — el ledger jamás debe
tumbar un turno):

1. **En memoria**: un ring buffer por tenant (bounded, últimas
   `_RING_MAXLEN` entradas). Es lo que leen `last_reversible_action`/
   `undo_last_action`/`describe_last_actions`, que son síncronas y viven en el
   mismo proceso que ejecuta el turno — el camino más corto entre "acabo de
   ejecutar la tool" y "¿qué cambiaste?".

2. **Persistente**: si `record_action_effect` recibe una `session`
   (la `AsyncSession` de `edecan_db.session.get_session`, inyectada en
   `ToolContext.session`), inserta la fila en la tabla `action_effects`
   (migración `0038_action_effects`) dentro de la MISMA transacción del turno.
   Así el historial sobrevive a un reinicio del proceso.

`edecan_core` no declara `sqlalchemy` como dependencia dura (ver
`pyproject.toml`): la persistencia usa SQL textual parametrizado con un import
diferido de `sqlalchemy.text()` — mismo patrón que `edecan_core.memory._sql`.
Sin `sqlalchemy` disponible, se pasa el SQL como `str` plano (suficiente para
un `session` duck-typed de prueba); la escritura sigue siendo best-effort.
"""

from __future__ import annotations

import json
import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

try:
    from sqlalchemy import text as _sqlalchemy_text
except ImportError:  # pragma: no cover - sqlalchemy no instalada
    _sqlalchemy_text = None

# Últimas N acciones por tenant. Bounded a propósito: el ledger en memoria es
# una vista caliente de trabajo ("¿qué acabo de hacer?"), no el historial
# completo — ese vive en la tabla `action_effects` (migración `0038`).
_RING_MAXLEN = 100

# `_RING[tenant_id]` es un `deque` de `ActionEffect`. Un `dict` plano alcanza:
# el loop del agente corre en un solo event loop async, no hay acceso
# concurrente entre hilos que exija un lock.
_RING: dict[UUID, deque[ActionEffect]] = {}


def _sql(statement: str) -> Any:
    """Envuelve `statement` con `sqlalchemy.text()` si sqlalchemy está disponible."""
    return _sqlalchemy_text(statement) if _sqlalchemy_text is not None else statement


@dataclass(frozen=True)
class ActionEffect:
    """Una acción ejecutada por una tool, con su operación inversa (PHASE2.md §64).

    `inverse_op` es un dict JSON-serializable con lo necesario para revertir:
    hoy `{"description": ..., "tool_name": ..., "args": ...}` — la reversión
    real la ejecutan las tools que declaran `Tool.inverse`, no el ledger.
    `reversible` marca si es candidata a deshacer; `created_at` es la marca
    temporal de registro.
    """

    id: UUID
    tenant_id: UUID
    user_id: UUID
    tool_name: str
    target: str | None
    inverse_op: dict[str, Any]
    reversible: bool
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _append(effect: ActionEffect) -> None:
    """Anexa `effect` al ring buffer de su tenant (bounded, ver `_RING_MAXLEN`)."""
    buffer = _RING.get(effect.tenant_id)
    if buffer is None:
        buffer = deque(maxlen=_RING_MAXLEN)
        _RING[effect.tenant_id] = buffer
    buffer.append(effect)


async def _persist(session: Any, effect: ActionEffect) -> None:
    """Inserta `effect` en la tabla `action_effects` dentro de la transacción de
    `session`. Best-effort: cualquier fallo se registra y se ignora — un
    problema de base de datos nunca debe tumbar el turno ni el registro en
    memoria."""
    try:
        await session.execute(
            _sql(
                """
                INSERT INTO action_effects (
                    id, tenant_id, user_id, tool_name, target, inverse_op,
                    reversible, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :user_id, :tool_name, :target,
                    :inverse_op ::jsonb, :reversible, now(), now()
                )
                """
            ),
            {
                "id": effect.id,
                "tenant_id": effect.tenant_id,
                "user_id": effect.user_id,
                "tool_name": effect.tool_name,
                "target": effect.target,
                "inverse_op": json.dumps(effect.inverse_op),
                "reversible": effect.reversible,
            },
        )
    except Exception:  # noqa: BLE001 - best-effort por contrato
        logger.warning(
            "No se pudo persistir el efecto de la tool %r en action_effects",
            effect.tool_name,
            exc_info=True,
        )


async def record_action_effect(
    tenant_id: UUID,
    user_id: UUID,
    tool_name: str,
    target: str | None,
    inverse_op: dict[str, Any],
    reversible: bool,
    *,
    session: Any | None = None,
    created_at: datetime | None = None,
) -> ActionEffect:
    """Registra un efecto de tool en memoria (ring buffer por tenant) y, si hay
    `session`, lo persiste en `action_effects`.

    Nunca lanza: el append en memoria no puede fallar y la persistencia está
    envuelta en try/except (`_persist`). Devuelve el `ActionEffect` construido
    para que quien llame pueda inspeccionarlo si quiere.
    """
    effect = ActionEffect(
        id=uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        tool_name=tool_name,
        target=target,
        inverse_op=dict(inverse_op),
        reversible=reversible,
        created_at=created_at or datetime.now(UTC),
    )
    _append(effect)
    if session is not None:
        await _persist(session, effect)
    return effect


def last_reversible_action(tenant_id: UUID, user_id: UUID) -> ActionEffect | None:
    """La acción reversible más reciente de `user_id` en `tenant_id`, o `None`.

    Lee SOLO el ring buffer en memoria (síncrono, sin I/O): es la fuente del
    "¿qué acabo de hacer?" dentro del proceso vivo. Escanea del más reciente al
    más antiguo y devuelve el primero con `reversible=True` y `user_id`
    coincidente.
    """
    buffer = _RING.get(tenant_id)
    if not buffer:
        return None
    for effect in reversed(buffer):
        if effect.user_id == user_id and effect.reversible:
            return effect
    return None


def undo_last_action(tenant_id: UUID, user_id: UUID) -> dict[str, Any]:
    """Devuelve la operación inversa de la última acción reversible y la retira
    del ledger en memoria (no se puede deshacer dos veces).

    `{}` cuando no hay nada que deshacer — nunca lanza. La reversión real la
    ejecuta quien consume el dict (una tool que declara `Tool.inverse`); aquí
    solo se entrega la operación y se consume el registro.
    """
    effect = last_reversible_action(tenant_id, user_id)
    if effect is None:
        return {}
    buffer = _RING.get(tenant_id)
    if buffer is not None and effect in buffer:
        buffer.remove(effect)
    return dict(effect.inverse_op)


def describe_last_actions(tenant_id: UUID, user_id: UUID, limit: int = 5) -> list[str]:
    """Resúmenes legibles de las últimas acciones reversibles de `user_id`.

    Devuelve frases cortas tipo "editar archivo", "crear memoria" (PHASE2.md
    §69) para que el agente conteste "¿qué cambiaste?". Deriva el verbo del
    `tool_name` (guiones bajos → espacios) y, si hay `target`, lo anexa. Solo
    incluye acciones `reversible=True` y respeta `limit` (default 5).
    """
    buffer = _RING.get(tenant_id)
    if not buffer:
        return []
    summaries: list[str] = []
    for effect in reversed(buffer):
        if effect.user_id != user_id or not effect.reversible:
            continue
        nombre = effect.tool_name.replace("_", " ").strip()
        objetivo = str(effect.target).strip() if effect.target else ""
        resumen = f"{nombre} {objetivo}".strip() if objetivo else nombre
        summaries.append(resumen)
        if len(summaries) >= limit:
            break
    return summaries
