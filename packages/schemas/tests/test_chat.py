from __future__ import annotations

from uuid import uuid4

import pytest
from edecan_schemas.chat import (
    AgentEventAdapter,
    ChatBlockAdapter,
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


def test_chat_message_permite_archivo_sin_texto():
    file_id = uuid4()
    msg = ChatMessageIn(attachments=[file_id])
    assert msg.text == ""
    assert msg.attachments == [file_id]


def test_chat_message_rechaza_vacio_total():
    with pytest.raises(ValidationError):
        ChatMessageIn()


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


def test_tool_end_acepta_bloques_versionados_y_correlacionados():
    event = AgentEventAdapter.validate_python(
        {
            "type": "tool_end",
            "tool_call_id": "call_web_1",
            "name": "navegar_web",
            "result_preview": "Sitio abierto",
            "blocks_version": 1,
            "blocks": [
                {
                    "type": "link_preview",
                    "fallback_text": "Fuente",
                    "url": "https://example.com/noticia",
                    "title": "Noticia",
                    "source_mode": "live",
                    "actions": [
                        {
                            "id": "source.open.1",
                            "label": "Abrir",
                            "action": "open_url",
                            "url": "https://example.com/noticia",
                        }
                    ],
                }
            ],
        }
    )

    assert isinstance(event, ToolEndEvent)
    assert event.tool_call_id == "call_web_1"
    assert event.blocks[0].fallback_text == "Fuente"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "http://127.0.0.1/admin",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "https://printer.local/status",
    ],
)
def test_link_preview_rechaza_urls_activas_privadas_o_con_credenciales(url: str):
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(
            {"type": "link_preview", "url": url, "title": "No debe pasar"}
        )


def test_accion_prefill_no_puede_disfrazarse_de_url():
    with pytest.raises(ValidationError):
        ChatBlockAdapter.validate_python(
            {
                "type": "link_preview",
                "url": "https://example.com",
                "title": "Fuente",
                "actions": [
                    {
                        "id": "bad",
                        "label": "Mala",
                        "action": "prefill_message",
                        "url": "https://example.com",
                    }
                ],
            }
        )
