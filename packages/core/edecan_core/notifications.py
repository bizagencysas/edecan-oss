"""Eventos durables y preferencias de notificaciones importantes.

Esta capa no conoce APNs ni FCM. Primero agrega un evento mínimo a
``audit_log`` y solo informa si ese evento era nuevo; el proceso que llama
puede intentar el push *después* de cerrar/confirmar su transacción.

No se persisten títulos, nombres de archivo, prompts, errores ni resultados.
El registro contiene únicamente vocabulario controlado e identificadores
opacos necesarios para volver a la actividad, chat o artefacto correcto.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID, uuid4

from .memory._sql import sql

NotificationCategory = Literal["work", "content", "design", "files", "self_repair"]
NotificationEventKind = Literal[
    "work_completed",
    "work_failed",
    "content_created",
    "content_published",
    "design_ready",
    "design_export_ready",
    "file_ready",
    "pdf_ready",
    "self_repair_completed",
    "phone_call_incoming",
]

NOTIFICATION_CATEGORIES: tuple[NotificationCategory, ...] = (
    "work",
    "content",
    "design",
    "files",
    "self_repair",
)
DEFAULT_NOTIFICATION_PREFERENCES: Mapping[NotificationCategory, bool] = MappingProxyType(
    {category: True for category in NOTIFICATION_CATEGORIES}
)

_EVENT_DEFINITIONS: Mapping[NotificationEventKind, tuple[NotificationCategory, str, str, str]] = (
    MappingProxyType(
        {
            "work_completed": (
                "work",
                "Trabajo terminado",
                "Un trabajo terminó. Abre Edecán para ver el resultado.",
                "activity",
            ),
            "work_failed": (
                "work",
                "Trabajo pendiente",
                "Un trabajo necesita atención. Abre Edecán para revisar el estado.",
                "activity",
            ),
            "content_created": (
                "content",
                "Contenido listo",
                "Tu contenido está listo para revisar en Edecán.",
                "assistant",
            ),
            "content_published": (
                "content",
                "Publicación terminada",
                "La publicación terminó. Revisa el resultado en Edecán.",
                "activity",
            ),
            "design_ready": (
                "design",
                "Diseño listo",
                "Tu diseño está listo para revisar en Edecán.",
                "assistant",
            ),
            "design_export_ready": (
                "design",
                "Exportación lista",
                "La exportación de tu diseño está lista en Edecán.",
                "assistant",
            ),
            "file_ready": (
                "files",
                "Archivo listo",
                "Tu archivo está listo para usar en Edecán.",
                "assistant",
            ),
            "pdf_ready": (
                "files",
                "PDF listo",
                "Tu PDF está listo para revisar o descargar en Edecán.",
                "assistant",
            ),
            "self_repair_completed": (
                "self_repair",
                "Reparación terminada",
                "La reparación local terminó. Abre Edecán para revisar el resultado.",
                "activity",
            ),
            "phone_call_incoming": (
                "work",
                "Llamada entrante",
                "Edecán está atendiendo una llamada. Ábrelo en Actividad.",
                "activity",
            ),
        }
    )
)

_EVENT_ACTION = "notifications.event"
_PREFERENCES_ACTION = "notifications.preferences.updated"


@dataclass(frozen=True)
class ImportantNotificationEvent:
    """Evento sin texto libre, apto para persistencia y push.

    ``event_id`` es el UUID opaco de la ocurrencia (job, misión, archivo o
    artefacto). Junto con ``kind`` forma una clave que no puede producir dos
    pushes y que, por construcción, nunca contiene texto del usuario.
    """

    tenant_id: UUID
    user_id: UUID
    kind: NotificationEventKind
    event_id: UUID
    chat_id: UUID | None = None
    artifact_id: UUID | None = None
    resource_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.kind not in _EVENT_DEFINITIONS:
            raise ValueError(f"Tipo de notificación no soportado: {self.kind!r}.")
        opaque_ids = (self.tenant_id, self.user_id, self.event_id)
        if not all(isinstance(value, UUID) for value in opaque_ids):
            raise ValueError("tenant_id, user_id y event_id deben ser UUID opacos.")
        if self.chat_id is not None and self.artifact_id is not None:
            raise ValueError("Usa chat_id o artifact_id, no ambos.")

    @property
    def event_key(self) -> str:
        return f"{self.kind}:{self.event_id}"

    @property
    def category(self) -> NotificationCategory:
        return _EVENT_DEFINITIONS[self.kind][0]

    @property
    def title(self) -> str:
        return _EVENT_DEFINITIONS[self.kind][1]

    @property
    def body(self) -> str:
        return _EVENT_DEFINITIONS[self.kind][2]

    @property
    def route(self) -> str:
        return _EVENT_DEFINITIONS[self.kind][3]

    def safe_metadata(self) -> dict[str, Any]:
        """Metadatos de actividad: solo enums e identificadores opacos."""
        data: dict[str, Any] = {
            "version": 1,
            "category": self.category,
            "kind": self.kind,
            "event_key": self.event_key,
            "route": self.route,
        }
        for field_name in ("chat_id", "artifact_id", "resource_id"):
            value = getattr(self, field_name)
            if value is not None:
                data[field_name] = str(value)
        return data

    def push_data(self) -> dict[str, str]:
        data = {
            "route": self.route,
            "kind": _mobile_kind(self.category),
            "event": self.kind,
            "event_key": self.event_key,
        }
        if self.chat_id is not None:
            data["chat_id"] = str(self.chat_id)
            data["deeplink"] = f"edecan://chat/{self.chat_id}"
        elif self.artifact_id is not None:
            data["artifact_id"] = str(self.artifact_id)
            data["deeplink"] = f"edecan://artifact/{self.artifact_id}"
        elif self.resource_id is not None:
            data["resource_id"] = str(self.resource_id)
            data["deeplink"] = f"edecan://activity/{self.resource_id}"
        return data


@dataclass(frozen=True)
class DurableNotificationEvent:
    id: UUID
    created: bool
    push_enabled: bool


def normalize_notification_preferences(
    partial: Mapping[str, Any] | None,
) -> dict[NotificationCategory, bool]:
    """Aplica defaults y rechaza categorías desconocidas o valores no booleanos."""
    normalized = dict(DEFAULT_NOTIFICATION_PREFERENCES)
    if partial is None:
        return normalized
    unknown = set(partial) - set(NOTIFICATION_CATEGORIES)
    if unknown:
        raise ValueError(f"Categorías de notificación desconocidas: {', '.join(sorted(unknown))}.")
    for category, value in partial.items():
        if not isinstance(value, bool):
            raise ValueError(f"La preferencia {category!r} debe ser booleana.")
        normalized[category] = value  # type: ignore[literal-required]
    return normalized


async def get_notification_preferences(
    session: Any, *, tenant_id: UUID, user_id: UUID
) -> dict[NotificationCategory, bool]:
    result = await session.execute(
        sql(
            """
            SELECT meta
            FROM audit_log
            WHERE tenant_id = :tenant_id AND actor_user_id = :user_id
              AND action = :action
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ),
        {"tenant_id": tenant_id, "user_id": user_id, "action": _PREFERENCES_ACTION},
    )
    row = result.mappings().first()
    if row is None:
        return normalize_notification_preferences(None)
    meta = row.get("meta") if hasattr(row, "get") else None
    categories = meta.get("categories") if isinstance(meta, dict) else None
    return normalize_notification_preferences(categories if isinstance(categories, dict) else None)


async def save_notification_preferences(
    session: Any,
    *,
    tenant_id: UUID,
    user_id: UUID,
    categories: Mapping[str, Any],
) -> dict[NotificationCategory, bool]:
    current = await get_notification_preferences(session, tenant_id=tenant_id, user_id=user_id)
    validated = normalize_notification_preferences(categories)
    # ``normalize_notification_preferences`` rellena defaults. Aplicar solo
    # las claves recibidas permite PATCH semántico sin perder cambios previos.
    for key in categories:
        current[key] = validated[key]  # type: ignore[literal-required]
    await session.execute(
        sql(
            """
            INSERT INTO audit_log
                (id, tenant_id, actor_user_id, action, target, meta, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :user_id, :action, :target, :meta ::jsonb, now(), now())
            """
        ),
        {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "action": _PREFERENCES_ACTION,
            "target": str(user_id),
            "meta": _json({"version": 1, "categories": current}),
        },
    )
    return current


async def record_notification_event(
    session: Any, event: ImportantNotificationEvent
) -> DurableNotificationEvent:
    """Registra una vez la actividad usando un advisory lock transaccional.

    La terna ``tenant_id + user_id + event_key`` es idempotente incluso con
    dos workers concurrentes. El lock solo serializa esa clave concreta.
    """
    lock_key = f"{event.tenant_id}:{event.user_id}:{event.event_key}"
    await session.execute(
        sql("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    target = f"notification:{event.user_id}:{event.event_key}"
    existing = await session.execute(
        sql(
            """
            SELECT id
            FROM audit_log
            WHERE tenant_id = :tenant_id AND action = :action AND target = :target
            ORDER BY created_at ASC
            LIMIT 1
            """
        ),
        {"tenant_id": event.tenant_id, "action": _EVENT_ACTION, "target": target},
    )
    row = existing.mappings().first()
    preferences = await get_notification_preferences(
        session, tenant_id=event.tenant_id, user_id=event.user_id
    )
    if row is not None:
        return DurableNotificationEvent(
            id=UUID(str(row["id"])), created=False, push_enabled=preferences[event.category]
        )

    event_id = uuid4()
    await session.execute(
        sql(
            """
            INSERT INTO audit_log
                (id, tenant_id, actor_user_id, action, target, meta, created_at, updated_at)
            VALUES
                (:id, :tenant_id, :user_id, :action, :target, :meta ::jsonb, now(), now())
            """
        ),
        {
            "id": event_id,
            "tenant_id": event.tenant_id,
            "user_id": event.user_id,
            "action": _EVENT_ACTION,
            "target": target,
            "meta": _json(event.safe_metadata()),
        },
    )
    return DurableNotificationEvent(
        id=event_id, created=True, push_enabled=preferences[event.category]
    )


def _mobile_kind(category: NotificationCategory) -> str:
    return {"work": "mission", "content": "content"}.get(category, category)


def _json(value: Any) -> str:
    # Import local para mantener el módulo barato y explícito.
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "DEFAULT_NOTIFICATION_PREFERENCES",
    "NOTIFICATION_CATEGORIES",
    "DurableNotificationEvent",
    "ImportantNotificationEvent",
    "NotificationCategory",
    "NotificationEventKind",
    "get_notification_preferences",
    "normalize_notification_preferences",
    "record_notification_event",
    "save_notification_preferences",
]
