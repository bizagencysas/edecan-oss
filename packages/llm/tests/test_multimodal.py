from __future__ import annotations

import base64

from edecan_llm.base import ChatMessage, CompletionRequest
from edecan_llm.ollama import _to_ollama_messages
from edecan_llm.openai_compat import _to_openai_messages
from edecan_llm.vertex import _to_gemini_contents


def _request() -> CompletionRequest:
    encoded = base64.b64encode(b"fake-png").decode()
    return CompletionRequest(
        model="vision-model",
        messages=[
            ChatMessage(
                role="user",
                content=[
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/png",
                            "data": encoded,
                        },
                    },
                    {"type": "text", "text": "¿Qué ves?"},
                ],
            )
        ],
    )


def test_openai_compatible_recibe_data_url_multimodal():
    message = _to_openai_messages(_request())[0]
    assert message["content"][0]["type"] == "image_url"
    assert message["content"][0]["image_url"]["url"].startswith("data:image/png;base64,")
    assert message["content"][1] == {"type": "text", "text": "¿Qué ves?"}


def test_ollama_recibe_images_base64_y_texto():
    message = _to_ollama_messages(_request())[0]
    assert message["content"] == "¿Qué ves?"
    assert base64.b64decode(message["images"][0]) == b"fake-png"


def test_vertex_recibe_inline_data_multimodal():
    _system, contents = _to_gemini_contents(_request())
    parts = contents[0]["parts"]
    assert parts[0]["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(parts[0]["inlineData"]["data"]) == b"fake-png"
    assert parts[1] == {"text": "¿Qué ves?"}
