from __future__ import annotations

import uuid
from datetime import UTC, datetime

from edecan_api.chat_context import ChatContextLimits, build_contextual_history


def _limits(**overrides):
    base = dict(
        enabled=True,
        recent_messages=2,
        max_messages=10,
        max_chars=4_000,
        cross_chat_enabled=True,
        cross_chat_conversations=4,
        cross_chat_messages_per_conversation=3,
        cross_chat_max_chars=4_000,
    )
    base.update(overrides)
    return ChatContextLimits(**base)


def test_context_pack_keeps_recent_tail_and_adds_previous_chat_context() -> None:
    previous_chat = uuid.uuid4()
    history = [
        {"role": "user", "content": {"text": "Mensaje antiguo sobre Acme"}},
        {"role": "assistant", "content": {"text": "Respuesta antigua"}},
        {"role": "user", "content": {"text": "Mensaje reciente"}},
        {"role": "assistant", "content": {"text": "Respuesta reciente"}},
    ]
    cross = [
        {
            "conversation_id": previous_chat,
            "conversation_title": "Estrategia Acme",
            "conversation_updated_at": datetime(2026, 7, 28, tzinfo=UTC),
            "role": "user",
            "content": {"text": "Acme ya está aprobada en iOS y Android."},
        }
    ]

    packed = build_contextual_history(
        current_rows=history,
        cross_chat_rows=cross,
        limits=_limits(),
    )

    assert packed[0].role == "system"
    assert "Contexto anterior de esta conversación" in packed[0].content
    assert "Estrategia Acme" in packed[0].content
    assert "aprobada en iOS y Android" in packed[0].content
    assert [message.content for message in packed[-2:]] == [
        "Mensaje reciente",
        "Respuesta reciente",
    ]


def test_context_pack_can_be_disabled() -> None:
    packed = build_contextual_history(
        current_rows=[
            {"role": "user", "content": {"text": "A"}},
            {"role": "assistant", "content": {"text": "B"}},
            {"role": "user", "content": {"text": "C"}},
        ],
        cross_chat_rows=[
            {
                "conversation_id": uuid.uuid4(),
                "conversation_title": "No debe aparecer",
                "role": "user",
                "content": {"text": "Texto externo"},
            }
        ],
        limits=_limits(enabled=False, recent_messages=2),
    )

    assert [message.content for message in packed] == ["B", "C"]
