from __future__ import annotations

import json

from edecan_llm.base import ChatMessage, ToolSpec
from edecan_llm.workers_ai import (
    construir_solicitud_estable,
    herramienta_a_workers_ai,
    mensajes_a_workers_ai,
)


def _serializar_prefijo(system: str, tools: list[ToolSpec], mensajes: list[ChatMessage]) -> str:
    req = construir_solicitud_estable(
        model="@cf/zai-org/glm-5.2",
        system=system,
        tools=tools,
        historial=mensajes[:-1] if len(mensajes) > 1 else [],
        turno_actual=mensajes[-1] if mensajes else None,
    )

    cuerpo = {
        "messages": mensajes_a_workers_ai(req),
        "tools": [herramienta_a_workers_ai(t) for t in req.tools],
    }
    return json.dumps(cuerpo, ensure_ascii=False)


def test_prompt_prefix_stability_between_consecutive_turns() -> None:
    system = "Eres Forge, el IDE de agentes de Edecán."
    tools = [
        ToolSpec(name="terminal", description="Ejecuta comando", input_schema={"type": "object"}),
        ToolSpec(name="filesystem", description="Edita archivo", input_schema={"type": "object"}),
    ]

    # Turno 1
    turno_1_msg = [ChatMessage(role="user", content="Crea una landing page en React")]
    req_1_json = _serializar_prefijo(system, tools, turno_1_msg)

    # Turno 2 (con historial del turno 1)
    turno_2_msg = [
        ChatMessage(role="user", content="Crea una landing page en React"),
        ChatMessage(role="assistant", content="Creando archivo App.tsx..."),
        ChatMessage(role="user", content="Ahora añade un botón de login"),
    ]
    req_2_json = _serializar_prefijo(system, tools, turno_2_msg)

    # El inicio de req_2_json debe ser idéntico byte a byte a req_1_json hasta la divergencia
    payload_1 = json.loads(req_1_json)
    payload_2 = json.loads(req_2_json)

    # Herramientas idénticas y ordenadas
    assert payload_1["tools"] == payload_2["tools"]

    # Mensajes del turno 1 son idénticos al inicio del turno 2
    assert payload_2["messages"][: len(payload_1["messages"])] == payload_1["messages"]

    # Verificación en texto raw (la parte inicial del JSON hasta la lista de mensajes coincide)
    corte = req_1_json.rfind('"content": "Crea una landing page en React"')
    assert corte > 0
    prefijo_1 = req_1_json[:corte]
    prefijo_2 = req_2_json[:corte]

    assert prefijo_1 == prefijo_2
