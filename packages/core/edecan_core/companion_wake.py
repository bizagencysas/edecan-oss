"""Companion wake: quiet hours, idempotency and helpers for proactive turns.

The scheduler only wakes the companion; it never supplies chat content.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from edecan_core.safety import redact

from .memory._sql import sql

_BOGOTA = ZoneInfo("America/Bogota")
_QUIET_START_HOUR = 22  # inclusive
_QUIET_END_HOUR = 7  # exclusive upper bound for "night"

_WAKE_DELIVERY_ACTION = "notifications.companion_wake.delivery"
_PREFERENCES_ACTION = "notifications.preferences.updated"

SILENCE_SENTINEL = "[NO_MESSAGE]"

DEFAULT_WAKE_INSTRUCTION = """\
[Edecán — turno proactivo interno, no visible para el usuario]

Fuiste despertado para revisar si hay algo útil que decirle al dueño en el chat principal.
Antes de escribir, inspecciona el estado real con tus herramientas cuando haga falta:
conversaciones abiertas, aprobaciones pendientes, trabajo reciente, calendario, GitHub,
Stripe u otras fuentes disponibles.

Reglas:
- Solo escribe si hay una razón concreta y accionable; el silencio es válido y preferible
  a un "solo pasaba a saludar".
- Nunca envíes plantillas genéricas ("Buenos días", "¿En qué te ayudo?", "¿Todo bien?").
- Si no hay nada que valga la pena decir, responde EXACTAMENTE: [NO_MESSAGE]
- Si sí escribes, sé breve, útil y en español de Venezuela (tú, sin voseo).
"""


def is_quiet_hours(now: datetime | None = None) -> bool:
    """True between 22:00 and 07:00 America/Bogota."""
    instant = now or datetime.now(UTC)
    local = instant.astimezone(_BOGOTA)
    hour = local.hour
    return hour >= _QUIET_START_HOUR or hour < _QUIET_END_HOUR


async def companion_always_on(session: Any, *, tenant_id: UUID, user_id: UUID) -> bool:
    """Whether proactive companion wakes are enabled for this user.

    Defaults to ``True`` (24/7 life companion). Opt-out via
    ``meta.companion_24_7 = false`` in notification preferences.
    """
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
        return True
    meta = row.get("meta") if hasattr(row, "get") else None
    if not isinstance(meta, dict):
        return True
    if "companion_24_7" not in meta:
        return True
    return bool(meta.get("companion_24_7"))


def is_pulse_window(now: datetime | None = None) -> bool:
    """True during companion pulse hours (08:00–21:59 America/Bogota)."""
    instant = now or datetime.now(UTC)
    hour = instant.astimezone(_BOGOTA).hour
    return 8 <= hour <= 21


def pulse_wake_key(now: datetime | None = None) -> str:
    """Hourly idempotency key for periodic companion pulses."""
    instant = (now or datetime.now(UTC)).astimezone(_BOGOTA)
    return f"pulse:{instant.strftime('%Y-%m-%d-%H')}"


def format_push_preview(text: str, *, max_chars: int = 160) -> str:
    """One-line APNs body preview from assistant chat text.

    Los modelos escriben markdown (`**negrita**`, `*cursiva*`, enlaces…) y el
    lock screen y APNs son texto plano: se elimina el marcado y se queda el
    texto. Best-effort: no es un parser completo, basta para que nunca salga
    un asterisco literal en la notificación.
    """
    import re as _re

    t = text.strip()
    t = _re.sub(r"\*\*(.+?)\*\*", r"\1", t)
    t = _re.sub(r"__(.+?)__", r"\1", t)
    t = _re.sub(r"\*([^*\n]+)\*", r"\1", t)
    t = _re.sub(r"_([^_\n]+)_", r"\1", t)
    t = _re.sub(r"`([^`\n]*)`", r"\1", t)
    t = _re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    one_line = " ".join(t.split())
    if not one_line:
        return ""
    if len(one_line) <= max_chars:
        return one_line
    trimmed = one_line[: max_chars - 1].rstrip()
    return f"{trimmed}…"


def companion_push_title(source: str | None) -> str:
    if source == "phone_call_finished":
        return "Llamada"
    return "Edecán"


def should_run_wake(
    *,
    now: datetime | None = None,
    urgent: bool = False,
    companion_enabled: bool = True,
) -> bool:
    if urgent:
        return True
    if not companion_enabled:
        return False
    return not is_quiet_hours(now)


async def record_companion_wake(
    session: Any, *, tenant_id: UUID, user_id: UUID, wake_key: str
) -> bool:
    """Idempotent claim for one companion wake occurrence.

    Returns True the first time this ``wake_key`` is seen for the user.
    """
    lock_key = f"{tenant_id}:{user_id}:{wake_key}"
    await session.execute(
        sql("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
        {"lock_key": lock_key},
    )
    target = f"companion_wake:{user_id}:{wake_key}"
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
        {"tenant_id": tenant_id, "action": _WAKE_DELIVERY_ACTION, "target": target},
    )
    row = existing.mappings().first()
    if row is not None:
        return False
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
            "action": _WAKE_DELIVERY_ACTION,
            "target": target,
            "meta": _json({"version": 1, "wake_key": wake_key}),
        },
    )
    return True


def is_substantive_assistant_text(text: str) -> bool:
    """False for empty output or an explicit silence sentinel."""
    cleaned = text.strip()
    if not cleaned:
        return False
    if cleaned == SILENCE_SENTINEL:
        return False
    if re.fullmatch(r"\[NO[_\s-]?MESSAGE\]", cleaned, flags=re.IGNORECASE):
        return False
    return True


def rows_to_chat_messages(rows: list[dict[str, Any]], *, limit: int = 40) -> list[Any]:
    """Minimal row→ChatMessage conversion for worker companion turns."""
    from edecan_llm.base import ChatMessage

    messages: list[ChatMessage] = []
    for row in rows[-limit:]:
        role = row.get("role")
        if role not in {"system", "user", "assistant", "tool"}:
            continue
        text = extract_message_text(row.get("content"))
        if text:
            messages.append(ChatMessage(role=role, content=text))
    return messages


def extract_message_text(content: Any, *, max_chars: int = 8_000) -> str:
    if isinstance(content, str):
        text = content
    elif isinstance(content, dict):
        if isinstance(content.get("text"), str):
            text = content["text"]
        else:
            text = json.dumps(content, ensure_ascii=False, default=str)
    elif isinstance(content, list):
        parts = [str(item) for item in content if item]
        text = " ".join(parts)
    else:
        text = str(content or "")
    return redact(" ".join(text.split()))[:max_chars]


def stable_event_id(*, tenant_id: UUID, wake_key: str) -> UUID:
    """Deterministic UUID for notification dedupe tied to a wake."""
    from uuid import NAMESPACE_URL, uuid5

    return uuid5(NAMESPACE_URL, f"companion-wake:{tenant_id}:{wake_key}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


__all__ = [
    "DEFAULT_WAKE_INSTRUCTION",
    "SILENCE_SENTINEL",
    "companion_always_on",
    "companion_push_title",
    "extract_message_text",
    "format_push_preview",
    "is_pulse_window",
    "is_quiet_hours",
    "is_substantive_assistant_text",
    "pulse_wake_key",
    "record_companion_wake",
    "rows_to_chat_messages",
    "should_run_wake",
    "stable_event_id",
]
