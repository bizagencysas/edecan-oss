"""Entrega un evento importante ya reducido a enums e identificadores UUID.

Antes de entregar, consulta la presencia SSE del chat (`chat_id`): la API y el
worker corren en el MISMO proceso (`edecan_local`), así que este handler importa
`edecan_api.presencia` por nombre de módulo con import perezoso y guardado
(fail-open: si el módulo no existe — worker desplegado sin la API — se entrega
el push igual). Si el dueño está DENTRO del chat de esa conversación, el push
se suprime sin error: "no push si estoy dentro del chat; el aviso in-app lo da
el propio stream SSE" (ver `edecan_api.presencia` para la decisión completa).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from edecan_core.notifications import ImportantNotificationEvent
from edecan_schemas import JobEnvelope

from edecan_worker.deps import Deps
from edecan_worker.universal_notifications import notify_important_event

logger = logging.getLogger(__name__)


def _event_log_fn() -> Any | None:
    """`edecan_api.event_log.log_event_con_factory`; `None` si no existe (fail-open).

    Import perezoso por nombre de módulo, mismo criterio que `_presencia`:
    un worker desplegado sin la API sigue entregando pushes sin log de eventos.
    """
    try:
        from edecan_api.event_log import log_event_con_factory
    except ImportError:
        logger.debug("edecan_api.event_log no disponible; push sin log de eventos.")
        return None
    return log_event_con_factory


async def _log_evento(
    deps: Deps,
    *,
    tenant_id: uuid.UUID | None,
    categoria: str,
    accion: str,
    detalle: dict[str, object] | None = None,
) -> None:
    """Registra el hecho en `event_log` (plataforma de logging TOTAL).

    Fail-open en CADA eslabón: sin módulo, sin sesión o con INSERT fallido,
    el push sigue exactamente igual — el log jamás tumba la entrega.
    """
    fn = _event_log_fn()
    if fn is None:
        return
    try:
        await fn(
            deps.session_factory,
            tenant_id=tenant_id,
            categoria=categoria,
            accion=accion,
            detalle=detalle,
        )
    except Exception:  # noqa: BLE001 - el log es observabilidad, no requisito
        logger.warning(
            "notify_important_event: no se pudo registrar en event_log (%s/%s)",
            categoria,
            accion,
            exc_info=True,
        )


def _presencia() -> Any | None:
    """Singleton de presencia de la API; `None` si el módulo no existe (fall-open).

    Import perezoso por nombre de módulo, mismo criterio de hermanos que
    `edecan_worker.deps` (ARCHITECTURE.md §10.1): los tests con fakes nunca
    pagan el import real, y un worker mínimo sin `edecan_api` sigue
    entregando pushes.
    """
    try:
        from edecan_api.presencia import presencia
    except ImportError:
        logger.debug("edecan_api.presencia no disponible; entrega sin supresión por presencia.")
        return None
    return presencia


def _optional_uuid(payload: dict[str, object], name: str) -> uuid.UUID | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"notify_important_event requiere {name} UUID") from exc


async def handle(env: JobEnvelope, deps: Deps) -> dict[str, object] | None:
    if env.tenant_id is None:
        raise ValueError("notify_important_event requiere tenant_id")
    try:
        user_id = uuid.UUID(str(env.payload["user_id"]))
        event_id = uuid.UUID(str(env.payload["event_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("notify_important_event requiere user_id y event_id UUID") from exc

    chat_id = _optional_uuid(env.payload, "chat_id")
    if chat_id is not None:
        registro = _presencia()
        if registro is not None and registro.esta_activa(chat_id):
            logger.info(
                "notify_important_event: push suprimido por presencia en chat "
                "tenant_id=%s chat_id=%s kind=%r",
                env.tenant_id,
                chat_id,
                env.payload.get("kind"),
            )
            await _log_evento(
                deps,
                tenant_id=env.tenant_id,
                categoria="push",
                accion="suprimido_presencia",
                detalle={
                    "job_id": str(env.job_id),
                    "attempt": env.attempt,
                    "chat_id": str(chat_id),
                    "kind": str(env.payload.get("kind") or ""),
                    "payload": dict(env.payload),
                },
            )
            return {
                "suppressed": True,
                "reason": "suppressed_in_chat",
                "chat_id": str(chat_id),
            }

    event = ImportantNotificationEvent(
        tenant_id=env.tenant_id,
        user_id=user_id,
        kind=str(env.payload.get("kind") or ""),  # type: ignore[arg-type]
        event_id=event_id,
        chat_id=chat_id,
        artifact_id=_optional_uuid(env.payload, "artifact_id"),
        resource_id=_optional_uuid(env.payload, "resource_id"),
        # Overrides de texto del productor (p. ej. «{bot} terminó: {resumen}»).
        # Sanitizados y acotados: el push es una superficie visible y el
        # payload viaja por una cola — nunca texto libre sin límite.
        apns_title=_optional_text(env.payload, "apns_title", 80),
        apns_body=_optional_text(env.payload, "apns_body", 200),
        worker_id=_optional_uuid(env.payload, "worker_id"),
        sender_display_name=_optional_text(env.payload, "sender_display_name", 80),
        avatar_shape=_optional_avatar_shape(env.payload, "avatar_shape"),
        avatar_fill=_optional_avatar_color(env.payload, "avatar_fill"),
        avatar_accent=_optional_avatar_color(env.payload, "avatar_accent"),
    )
    await _log_evento(
        deps,
        tenant_id=env.tenant_id,
        categoria="push",
        accion="salio_push",
        detalle={
            "job_id": str(env.job_id),
            "attempt": env.attempt,
            "payload": dict(env.payload),
        },
    )
    await notify_important_event(deps, event)
    return None


def _optional_text(payload: dict[str, object], name: str, max_chars: int) -> str | None:
    value = payload.get(name)
    if value in (None, ""):
        return None
    texto = " ".join(str(value).split())
    return texto[:max_chars] if texto else None


_GROK_SHAPES = frozenset({"circle", "rounded_square", "oval", "hexagon", "squircle"})
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


def _optional_avatar_shape(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if not isinstance(value, str):
        return None
    forma = value.strip()
    return forma if forma in _GROK_SHAPES else None


def _optional_avatar_color(payload: dict[str, object], name: str) -> str | None:
    value = payload.get(name)
    if not isinstance(value, str):
        return None
    texto = value.strip()
    if len(texto) == 4 and texto.startswith("#"):
        texto = "#" + "".join(c * 2 for c in texto[1:])
    if not _HEX_COLOR.fullmatch(texto):
        return None
    return texto.lower()
