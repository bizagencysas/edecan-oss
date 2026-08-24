"""Job `daily_brief`: resumen proactivo periódico del tenant (PHASE2.md §56-57).

Compone un brief "Hoy: ..." a partir de datos locales del tenant y lo entrega
como mensaje en la conversación principal + push best-effort. NO llama a un
LLM en el camino de disparo (mismo criterio que `run_gym_checkin.py` /
`send_reminder.py`): el brief se arma con conteos deterministas.

## Qué lee (todo best-effort, degrada con gracia)

- Corridas recientes de automatizaciones (`automation_runs`): cuántas
  terminaron bien, cuántas fallaron, cuáles siguen corriendo (ver
  `smart_resume`, PHASE2 §57).
- Nombres de las automatizaciones que fallaron recientemente.
- Recordatorios pendientes del usuario (`reminders.status='pending'`).
- Próximos eventos de calendario — OPCIONAL: no hay una tabla de calendario
  pinneada en el esquema hoy (`ARCHITECTURE.md` §10.3), así que
  `_proximos_eventos` intenta una tabla `calendar_events` y degrada a `[]`
  ante cualquier fallo (tabla inexistente incluida).

## Idempotencia (presupuesto de atención, PHASE2 §55)

Un job de agenda puede doble-dispararse (redelivery de SQS). Para no enviar el
mismo brief dos veces el mismo día, se marca la entrega en `audit_log` vía
`edecan_core.notifications.record_daily_brief_delivery` (clave = fecha ISO);
la segunda ejecución del día termina sin enviar nada.

## Registro / disparo

El handler NO se registra todavía en `edecan_worker.handlers.HANDLERS`: el
invariante `set(HANDLERS) <= set(JOB_TYPES)` (verificado en
`apps/worker/tests/test_v2_handlers_registry.py`) exige que todo job type
registrado exista antes en `edecan_schemas.queue.JOB_TYPES`, y ese paquete
está fuera del alcance de este WP. Para activar el job en producción hacen
falta dos pasos (dueño del WP de schemas + este handler):

1. Agregar `"daily_brief"` a `edecan_schemas.queue.JOB_TYPES`.
2. Registrar el handler: `_register_defensive(HANDLERS, "daily_brief",
   "daily_brief")` en `apps/worker/edecan_worker/handlers/__init__.py`.

A diferencia de los jobs de sistema sin tenant (`send_reminder_scan`,
`automation_scan`, ...), `daily_brief` es POR TENANT + USUARIO: requiere
`env.tenant_id` y `env.payload["user_id"]` (mismo contrato que
`notify_important_event`). Por eso NO entra en `JOBS_PERIODICOS` de
`scheduler.py` (esa tupla encola con `tenant_id=None`). El disparo periódico
debe ser un barrido global que haga fan-out por tenant/usuario (un job
`daily_brief_scan` análogo a `automation_scan`, o un EventBridge Scheduler
que encola un `daily_brief` por usuario) — queda documentado acá y fuera de
alcance de este handler.

Payload: `{"user_id": "<uuid>"}`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from edecan_core.notifications import record_daily_brief_delivery
from edecan_schemas import JobEnvelope
from sqlalchemy import text

from edecan_worker import push
from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

TITULO_PUSH = "Edecán"
TITULO_BRIEF = "Resumen de hoy"

# Ventana por defecto para el "desde la última vez" del resumen.
VENTANA_RESUMEN = timedelta(days=1)

# Cuántos nombres de automatizaciones fallidas se mencionan, como máximo.
MAX_FALLIDAS_EN_BRIEF = 3

# Cuántos eventos de calendario se mencionan, como máximo.
MAX_EVENTOS_EN_BRIEF = 3


@dataclass(frozen=True)
class EstadoTenant:
    """Datos ya leídos y contados para componer el brief (sin lógica)."""

    completadas: int
    fallidas: int
    pendientes: int
    fallidas_nombres: tuple[str, ...]
    recordatorios: int
    eventos: tuple[str, ...]


def _conjuga(n: int, singular: str, plural: str) -> str:
    """Elige la forma verbal/nominal según el número (sin depender de gettext)."""
    return singular if n == 1 else plural


async def smart_resume(
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session: Any,
    *,
    desde: datetime | None = None,
    ventana: timedelta = VENTANA_RESUMEN,
) -> str:
    """Resume la historia de automatizaciones "desde la última vez" (PHASE2 §57).

    Devuelve un texto como::

        Desde la última vez:
        - se completaron 2 automatizaciones
        - falló 1
        - sigue pendiente 1

    ``automation_runs`` no tiene columna ``user_id`` (es de granularidad
    tenant, `ROADMAP_V2.md` §7.4), así que el conteo es por tenant; ``user_id``
    se acepta por compatibilidad de contrato con el resto del handler (y para
    que futuras tablas de actividad por-usuario puedan filtrar sin cambiar la
    firma).
    """
    if desde is None:
        desde = datetime.now(UTC) - ventana
    completadas = await _contar_runs(session, tenant_id, "done", desde)
    fallidas = await _contar_runs(session, tenant_id, "error", desde)
    pendientes = await _contar_runs(session, tenant_id, "running", None)
    return "\n".join(
        (
            "Desde la última vez:",
            f"- {_conjuga(completadas, 'se completó', 'se completaron')} "
            f"{completadas} {_conjuga(completadas, 'automatización', 'automatizaciones')}",
            f"- {_conjuga(fallidas, 'falló', 'fallaron')} {fallidas}",
            f"- {_conjuga(pendientes, 'sigue pendiente', 'siguen pendientes')} {pendientes}",
        )
    )


async def handle(env: JobEnvelope, deps: Deps) -> None:
    """Lee el estado del tenant, compone el brief y lo entrega (chat + push)."""
    if env.tenant_id is None:
        raise ValueError("daily_brief requiere tenant_id")
    tenant_id: uuid.UUID = env.tenant_id
    user_id = _user_id_del_payload(env)
    hoy = date.today().isoformat()

    async with deps.session_factory(None) as session:
        estado = await _leer_estado(session, tenant_id, user_id)

    brief = _componer_brief(estado)
    if brief is None:
        logger.info(
            "daily_brief: nada que reportar para tenant_id=%s user_id=%s; no se envía nada.",
            tenant_id,
            user_id,
        )
        return

    conversation_id: uuid.UUID | None = None
    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        # Idempotencia por día (ver docstring del módulo): si ya se entregó,
        # este disparo redundante termina sin escribir ni enviar nada.
        if not await record_daily_brief_delivery(
            session, tenant_id=tenant_id, user_id=user_id, brief_key=hoy
        ):
            logger.info("daily_brief: ya entregado hoy para user_id=%s; se ignora.", user_id)
            return
        conversation = await repo.resolve_main_conversation(
            tenant_id=tenant_id, user_id=user_id
        )
        conversation_id = conversation["id"]
        await repo.add_message(
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            role="assistant",
            content={"text": brief},
        )

    # El mensaje YA quedó persistido (la transacción cerró arriba) antes de
    # intentar el push — un fallo de push nunca hace que el brief "se pierda".
    # `push.enviar_push_a_usuario` ya nunca lanza por diseño; el `try/except`
    # es una segunda red de seguridad, igual que `send_reminder.py`/`run_gym_checkin.py`.
    try:
        resultado = await push.enviar_push_a_usuario(
            deps,
            tenant_id=tenant_id,
            user_id=user_id,
            titulo=TITULO_PUSH,
            cuerpo=_resumen_push(estado),
            data={"route": "activity", "chat_id": str(conversation_id)}
            if conversation_id
            else {"route": "activity"},
        )
        logger.info(
            "daily_brief: push tenant_id=%s enviados=%d fallidos=%d",
            tenant_id,
            resultado.enviados,
            resultado.fallidos,
        )
    except Exception:
        logger.warning(
            "daily_brief: fallo inesperado enviando push (el brief ya quedó guardado; "
            "esto no lo afecta).",
            exc_info=True,
        )

    logger.info("daily_brief completado tenant_id=%s user_id=%s", tenant_id, user_id)


# ---------------------------------------------------------------------------
# Lectura de estado (SQL parametrizado directo, mismo criterio que `repo.py`)
# ---------------------------------------------------------------------------


async def _leer_estado(session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID) -> EstadoTenant:
    desde = datetime.now(UTC) - VENTANA_RESUMEN
    completadas = await _contar_runs(session, tenant_id, "done", desde)
    fallidas = await _contar_runs(session, tenant_id, "error", desde)
    pendientes = await _contar_runs(session, tenant_id, "running", None)
    fallidas_nombres = await _automatizaciones_fallidas(session, tenant_id, desde)
    recordatorios = await _contar_recordatorios_pendientes(session, tenant_id, user_id)
    eventos = await _proximos_eventos(session, tenant_id, user_id)
    return EstadoTenant(
        completadas=completadas,
        fallidas=fallidas,
        pendientes=pendientes,
        fallidas_nombres=tuple(fallidas_nombres),
        recordatorios=recordatorios,
        eventos=tuple(eventos),
    )


async def _contar_runs(
    session: Any, tenant_id: uuid.UUID, status: str, desde: datetime | None
) -> int:
    """Cuenta corridas de `automation_runs` con ``status``; ``desde`` acota a las recientes."""
    if desde is None:
        stmt = (
            "SELECT COUNT(*) AS n FROM automation_runs "
            "WHERE tenant_id = :tenant_id AND status = :status"
        )
        params: dict[str, Any] = {"tenant_id": str(tenant_id), "status": status}
    else:
        stmt = (
            "SELECT COUNT(*) AS n FROM automation_runs "
            "WHERE tenant_id = :tenant_id AND status = :status AND started_at >= :desde"
        )
        params = {"tenant_id": str(tenant_id), "status": status, "desde": desde}
    result = await session.execute(text(stmt), params)
    row = result.mappings().first()
    return int(row["n"]) if row is not None else 0


async def _contar_recordatorios_pendientes(
    session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> int:
    result = await session.execute(
        text(
            "SELECT COUNT(*) AS n FROM reminders "
            "WHERE tenant_id = :tenant_id AND user_id = :user_id AND status = 'pending'"
        ),
        {"tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    row = result.mappings().first()
    return int(row["n"]) if row is not None else 0


async def _automatizaciones_fallidas(
    session: Any, tenant_id: uuid.UUID, desde: datetime
) -> list[str]:
    """Nombres (sin duplicar, en orden) de automatizaciones con corridas fallidas recientes."""
    result = await session.execute(
        text(
            "SELECT a.nombre FROM automation_runs r "
            "JOIN automations a ON a.id = r.automation_id AND a.tenant_id = r.tenant_id "
            "WHERE r.tenant_id = :tenant_id AND r.status = 'error' AND r.started_at >= :desde "
            "ORDER BY r.started_at DESC"
        ),
        {"tenant_id": str(tenant_id), "desde": desde},
    )
    nombres: list[str] = []
    vistos: set[str] = set()
    for row in result.mappings().all():
        nombre = str(row.get("nombre") or "").strip()
        if nombre and nombre not in vistos:
            vistos.add(nombre)
            nombres.append(nombre)
    return nombres


async def _proximos_eventos(
    session: Any, tenant_id: uuid.UUID, user_id: uuid.UUID
) -> list[str]:
    """Próximos eventos de calendario como líneas de texto — best-effort.

    No hay una tabla de calendario pinneada en el esquema hoy
    (`ARCHITECTURE.md` §10.3). Se intenta ``calendar_events`` y CUALQUIER
    fallo (tabla inexistente incluida) degrada a ``[]`` sin tumbar el brief.
    """
    try:
        result = await session.execute(
            text(
                "SELECT titulo, inicio_at FROM calendar_events "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id "
                "AND inicio_at >= now() ORDER BY inicio_at ASC LIMIT :limite"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "limite": MAX_EVENTOS_EN_BRIEF,
            },
        )
    except Exception:
        logger.debug(
            "daily_brief: calendario no disponible para tenant_id=%s; se omite.",
            tenant_id,
            exc_info=True,
        )
        return []
    eventos: list[str] = []
    for row in result.mappings().all():
        titulo = str(row.get("titulo") or "").strip()
        if not titulo:
            continue
        inicio = row.get("inicio_at")
        hora = ""
        if isinstance(inicio, datetime):
            hora = inicio.strftime("%H:%M")
        elif inicio is not None:
            hora = str(inicio)
        eventos.append(f"{titulo} a las {hora}" if hora else titulo)
    return eventos


# ---------------------------------------------------------------------------
# Composición del brief (determinista, sin LLM)
# ---------------------------------------------------------------------------


def _componer_brief(estado: EstadoTenant) -> str | None:
    """Arma el texto del brief o devuelve ``None`` si no hay nada que reportar.

    Un brief vacío no aporta valor y viola el presupuesto de atención
    (PHASE2 §55): si no hubo corridas, fallos, pendientes, recordatorios ni
    eventos, se omite el envío.
    """
    if not _hay_contenido(estado):
        return None

    lineas = [f"{TITULO_BRIEF}:"]
    lineas.append(
        f"- {_conjuga(estado.completadas, 'corrió bien', 'corrieron bien')} "
        f"{estado.completadas} {_conjuga(estado.completadas, 'automatización', 'automatizaciones')}"
    )
    if estado.fallidas:
        detalle = ""
        if estado.fallidas_nombres:
            detalle = " (" + ", ".join(estado.fallidas_nombres[:MAX_FALLIDAS_EN_BRIEF]) + ")"
        lineas.append(
            f"- {_conjuga(estado.fallidas, 'falló', 'fallaron')} {estado.fallidas}{detalle}"
        )
    if estado.pendientes:
        lineas.append(
            f"- {_conjuga(estado.pendientes, 'sigue corriendo', 'siguen corriendo')} "
            f"{estado.pendientes}"
        )
    if estado.recordatorios:
        sust = _conjuga(
            estado.recordatorios, "recordatorio pendiente", "recordatorios pendientes"
        )
        lineas.append(f"- {estado.recordatorios} {sust}")
    for evento in estado.eventos[:MAX_EVENTOS_EN_BRIEF]:
        lineas.append(f"- {evento}")
    return "\n".join(lineas)


def _hay_contenido(estado: EstadoTenant) -> bool:
    return bool(
        estado.completadas
        or estado.fallidas
        or estado.pendientes
        or estado.recordatorios
        or estado.eventos
    )


def _resumen_push(estado: EstadoTenant) -> str:
    """Cuerpo corto del push (una línea), derivado del mismo estado."""
    piezas: list[str] = []
    if estado.completadas:
        verbo = _conjuga(
            estado.completadas, "automatización corrió", "automatizaciones corrieron"
        )
        piezas.append(f"{estado.completadas} {verbo} bien")
    if estado.fallidas:
        piezas.append(f"{estado.fallidas} {_conjuga(estado.fallidas, 'falló', 'fallaron')}")
    if estado.recordatorios:
        sust = _conjuga(
            estado.recordatorios, "recordatorio pendiente", "recordatorios pendientes"
        )
        piezas.append(f"{estado.recordatorios} {sust}")
    if not piezas:
        piezas.append("tienes actividad nueva")
    return "Hoy: " + ", ".join(piezas) + "."


def _user_id_del_payload(env: JobEnvelope) -> uuid.UUID:
    valor = env.payload.get("user_id")
    if valor in (None, ""):
        raise ValueError("daily_brief requiere user_id en el payload")
    try:
        return uuid.UUID(str(valor))
    except (TypeError, ValueError) as exc:
        raise ValueError("daily_brief requiere user_id UUID") from exc


__all__ = ["EstadoTenant", "handle", "smart_resume"]