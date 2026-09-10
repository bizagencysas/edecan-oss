"""Plataforma de logging TOTAL: `log_event` escribe filas en la tabla `event_log`.

Ver la migración `0067_event_log` para el esquema (id, tenant_id nullable,
categoria, accion, detalle jsonb, created_at + índice para el borrado diario de
retención) y `edecan_worker.handlers.event_log_cleanup` para el job que
auto-elimina las filas de más de 7 días.

Contrato fail-open ABSOLUTO: ni `log_event` ni `log_event_con_factory` lanzan
jamás — un fallo del log (BD caída, tabla todavía sin migrar, detalle que no
serializa, sesión rota) queda en `logger` y el flujo del negocio sigue. El log
es observabilidad; jamás una dependencia del camino.

Detalle importante sobre la sesión: `log_event` NO hace commit — solo ejecuta
el INSERT y deja la transacción en manos del dueño de la sesión
(`edecan_db.session.get_session` commitea al salir del bloque `async with`).
PASAR SIEMPRE UNA SESIÓN DEDICADA al log: un INSERT fallido aborta la
transacción de Postgres entera, así que escribir el log en la sesión de la
petición o del job (que todavía tiene trabajo pendiente) convertiría un fallo
del log en un fallo del flujo — exactamente lo contrario del fail-open. Por eso
`log_event_con_factory` abre una sesión propia con `session_factory(tenant_id)`.

El worker y la API corren en el MISMO proceso (`edecan_local`), así que los
handlers del worker importan este módulo por nombre con import perezoso (mismo
criterio que `edecan_api.presencia`); un worker desplegado sin la API sigue
funcionando sin log.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_INSERT_EVENT_LOG = text(
    "INSERT INTO event_log (id, tenant_id, categoria, accion, detalle, created_at) "
    "VALUES (:id, :tenant_id, :categoria, :accion, :detalle, :created_at)"
)


def _serializar_detalle(detalle: dict[str, Any] | None) -> str:
    """El detalle a JSON plano para `jsonb`.

    `default=str` cubre cualquier valor que no sea JSON nativo (UUIDs,
    datetimes, enums) — un log de hechos no puede perder el evento por un tipo
    raro. Los `NaN`/`Infinity` (ilegales en jsonb) degradan a `None` en vez de
    tumbar el INSERT.
    """
    try:
        return json.dumps(detalle or {}, ensure_ascii=False, default=str, allow_nan=False)
    except (TypeError, ValueError):
        limpio = _limpiar_no_json(detalle)
        return json.dumps(limpio, ensure_ascii=False, default=str, allow_nan=False)


def _limpiar_no_json(valor: Any) -> Any:
    """Recorre el detalle reemplazando lo que `json.dumps(allow_nan=False)`
    rechaza (floats no finitos) por `None`."""
    if isinstance(valor, float) and valor != valor:  # NaN
        return None
    if isinstance(valor, float) and valor in (float("inf"), float("-inf")):
        return None
    if isinstance(valor, dict):
        return {str(k): _limpiar_no_json(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple)):
        return [_limpiar_no_json(v) for v in valor]
    return valor


async def log_event(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID | None,
    categoria: str,
    accion: str,
    detalle: dict[str, Any] | None = None,
) -> bool:
    """Inserta una fila en `event_log`. Devuelve `True` si quedó escrita.

    Fail-open: NUNCA lanza. La sesión debe ser dedicada al log (ver docstring
    del módulo); el commit lo hace el dueño de la sesión. Con una sesión de
    `get_session`, la fila queda commiteada al salir del bloque.
    """
    try:
        await session.execute(
            _INSERT_EVENT_LOG,
            {
                "id": uuid.uuid4(),
                "tenant_id": tenant_id,
                "categoria": str(categoria),
                "accion": str(accion),
                "detalle": _serializar_detalle(detalle),
                "created_at": datetime.now(UTC),
            },
        )
    except Exception:
        logger.exception(
            "event_log: no se pudo escribir el evento (%s/%s, tenant_id=%s)",
            categoria,
            accion,
            tenant_id,
        )
        return False
    return True


async def log_event_con_factory(
    session_factory: Callable[[uuid.UUID | None], Any],
    *,
    tenant_id: uuid.UUID | None,
    categoria: str,
    accion: str,
    detalle: dict[str, Any] | None = None,
) -> bool:
    """Abre una sesión propia y llama `log_event` (devuelve si quedó escrita).

    El punto de entrada para los call-sites que solo tienen una
    `session_factory` (los handlers del worker con `deps.session_factory`, la
    API con `edecan_db.session.get_session`). Fail-open total: si ni siquiera
    abrir la sesión se puede (BD caída), traga, loguea y devuelve `False`.
    """
    try:
        async with session_factory(tenant_id) as session:
            return await log_event(
                session,
                tenant_id=tenant_id,
                categoria=categoria,
                accion=accion,
                detalle=detalle,
            )
    except Exception:
        logger.exception(
            "event_log: no se pudo abrir la sesión para el evento (%s/%s, tenant_id=%s)",
            categoria,
            accion,
            tenant_id,
        )
        return False


__all__ = ["log_event", "log_event_con_factory"]