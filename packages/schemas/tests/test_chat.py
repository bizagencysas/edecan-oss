from __future__ import annotations

import pytest
from edecan_schemas.chat import (
    AgentEventAdapter,
    ChatMessageIn,
    ConfirmationRequiredEvent,
    DoneEvent,
    ErrorEvent,
    TextDeltaEvent,
    ToolEndEvent,
    ToolStartEvent,
)
from pydantic import ValidationError


def test_chat_message_in():
    msg = ChatMessageIn(text="hola")
    assert msg.text == "hola"


@pytest.mark.parametrize(
    ("payload", "expected_cls"),
    [
        ({"type": "text_delta", "text": "hola"}, TextDeltaEvent),
        ({"type": "tool_start", "name": "hora_actual", "args": {}}, ToolStartEvent),
        (
            {"type": "tool_end", "name": "hora_actual", "result_preview": "14:32"},
            ToolEndEvent,
        ),
        (
            {
                "type": "confirmation_required",
                "tool_call_id": "call_1",
                "name": "enviar_correo",
                "args": {"to": "a@b.com"},
            },
            ConfirmationRequiredEvent,
        ),
        ({"type": "done", "usage": {"input_tokens": 10, "output_tokens": 5}}, DoneEvent),
        ({"type": "error", "message": "boom"}, ErrorEvent),
    ],
)
def test_agent_event_discrimina_por_type(payload, expected_cls):
    event = AgentEventAdapter.validate_python(payload)
    assert isinstance(event, expected_cls)
    assert event.type == payload["type"]


def test_agent_event_tipo_desconocido_falla():
    with pytest.raises(ValidationError):
        AgentEventAdapter.validate_python({"type": "no_existe"})


def test_agent_event_serializa_de_vuelta_a_dict_con_type():
    event = AgentEventAdapter.validate_python({"type": "text_delta", "text": "hola"})
    dumped = AgentEventAdapter.dump_python(event, mode="json")
    assert dumped == {"type": "text_delta", "text": "hola"}
