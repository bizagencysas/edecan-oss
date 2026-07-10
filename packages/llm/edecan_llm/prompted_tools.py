"""Protocolo de tool-calling **por prompt** — para proveedores que no aceptan
tool-schemas nativos (`claude_cli.py`, `codex_cli.py`, WP-V3-03).

Los CLIs locales (`claude -p`, `codex exec`) no tienen una API de tool-use
estructurada como Anthropic/OpenAI: el único canal es texto. Este módulo
implementa el protocolo mínimo que usan ambos proveedores CLI:

1. `render_tools_block(tools)` — instrucción en español que se agrega al
   `system` cuando `req.tools` no está vacío: si el modelo necesita una
   herramienta, debe responder ÚNICAMENTE un JSON `{"tool_call": {...}}`.
2. `parse_tool_call(text)` — intenta extraer ese JSON de la respuesta del
   CLI. Si no lo encuentra, el llamador trata la respuesta como texto normal.
3. `render_prompt(req)` — serializa un `CompletionRequest` completo (system +
   bloque de tools + historia) a un único prompt de texto, porque el modo
   "print"/`exec` de ambos CLIs es de un solo turno (sin memoria de proceso).

Es **mejor esfuerzo**, no un contrato tan confiable como el tool-use nativo:
un CLI puede ignorar la instrucción, envolver el JSON en prosa, o truncarlo.
Ver `docs/proveedores-llm.md` para el detalle de esta limitación.
"""

from __future__ import annotations

import json
from uuid import uuid4

from .base import ChatMessage, CompletionRequest, ToolCall, ToolSpec

_TOOLS_BLOCK_HEADER = (
    "Tienes acceso a las siguientes herramientas. Si para responder necesitas "
    "usar una, responde ÚNICAMENTE (sin texto antes ni después, sin explicar "
    "nada más) un objeto JSON con esta forma exacta:\n"
    '{"tool_call": {"name": "<nombre_de_la_herramienta>", "arguments": {...}}}\n\n'
    "Si NO necesitas ninguna herramienta para responder, contesta normalmente "
    "en texto plano (nunca uses ese formato JSON en ese caso).\n\n"
    "Herramientas disponibles:"
)


def render_tools_block(tools: list[ToolSpec]) -> str:
    """Renderiza el bloque de instrucciones de tool-calling por prompt.

    Devuelve `""` si `tools` está vacío — así el llamador puede hacer
    `if tools_block:` sin chequear `tools` por separado.
    """
    if not tools:
        return ""
    lineas = [_TOOLS_BLOCK_HEADER]
    for tool in tools:
        schema = json.dumps(tool.input_schema, ensure_ascii=False)
        lineas.append(f"- {tool.name}: {tool.description}\n  Parámetros (JSON Schema): {schema}")
    return "\n".join(lineas)


def parse_tool_call(text: str) -> ToolCall | None:
    """Extrae el primer `{"tool_call": {"name": ..., "arguments": {...}}}`
    válido de `text`.

    Tolera bloques ```json ... ``` (o sin el lenguaje) y prosa alrededor del
    JSON: `_scan_tool_call` prueba cada `{` de `text` en orden con
    `json.JSONDecoder.raw_decode`, así que las comillas de un fence de
    markdown (o cualquier prosa antes/después) quedan afuera del objeto
    parseado sin necesidad de despojarlas primero. Genera un `id` con
    `uuid4` porque los CLIs no dan uno propio. Devuelve `None` si no
    encuentra ninguno (la respuesta es texto normal).
    """
    if not text:
        return None
    return _scan_tool_call(text)


def render_prompt(req: CompletionRequest) -> str:
    """Serializa un `CompletionRequest` completo a un único prompt de texto.

    Usado por `ClaudeCLIProvider`/`CodexCLIProvider`: su modo no interactivo
    (`claude -p`, `codex exec`) es de un solo turno, así que todo el
    `system` + la historia + el bloque de tools (si `req.tools` no está
    vacío) se aplanan a texto. Orden: `system`, bloque de tools, luego cada
    mensaje de `req.messages` como un turno `Usuario:`/`Asistente:` (o
    `Resultado de herramienta:` para `role="tool"`) — el último renglón
    termina siendo naturalmente el último turno de la conversación (lo normal
    es que sea el turno de usuario a responder; si en cambio es un resultado
    de herramienta, es porque `edecan_core.agent.Agent` está en medio de un
    loop de tool-use y le está pidiendo al CLI que continúe).
    """
    partes: list[str] = []
    if req.system:
        partes.append(req.system.strip())
    tools_block = render_tools_block(req.tools)
    if tools_block:
        partes.append(tools_block)
    for message in req.messages:
        renderizado = _render_message(message)
        if renderizado:
            partes.append(renderizado)
    return "\n\n".join(p for p in partes if p)


def _render_message(message: ChatMessage) -> str:
    if message.role == "system":
        return _text_of(message.content)
    if message.role == "user":
        return f"Usuario: {_text_of(message.content)}"
    if message.role == "assistant":
        return f"Asistente: {_assistant_text(message.content)}"
    if message.role == "tool":
        return f"Resultado de herramienta: {_tool_result_text(message.content)}"
    return _text_of(message.content)  # pragma: no cover - roles futuros, defensivo


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def _assistant_text(content: str | list[dict]) -> str:
    """Texto de un turno `assistant`, incluyendo sus `tool_use` (si los
    tiene) re-serializados en el MISMO formato `{"tool_call": ...}` que este
    módulo le pide al CLI — así el historial que se le reenvía es coherente
    con lo que el propio CLI habría escrito.
    """
    if isinstance(content, str):
        return content
    partes: list[str] = []
    for block in content:
        if block.get("type") == "text" and block.get("text"):
            partes.append(block["text"])
        elif block.get("type") == "tool_use":
            llamada = json.dumps(
                {
                    "tool_call": {
                        "name": block.get("name", ""),
                        "arguments": block.get("input") or {},
                    }
                },
                ensure_ascii=False,
            )
            partes.append(llamada)
    return "".join(partes)


def _tool_result_text(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    partes: list[str] = []
    for block in content:
        inner = block.get("content", "")
        texto = inner if isinstance(inner, str) else _text_of(inner)
        if texto:
            partes.append(texto)
    return "; ".join(partes)


def _scan_tool_call(text: str) -> ToolCall | None:
    """Busca el primer objeto JSON balanceado en `text` con forma
    `{"tool_call": {"name": str, "arguments": {...}}}`.

    Usa `json.JSONDecoder.raw_decode` (que tolera contenido después del
    objeto) posicionado en cada `{` de `text`, en vez de contar llaves a
    mano — más robusto ante strings que contengan `{`/`}` escapados.
    """
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, idx=i)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        tool_call = obj.get("tool_call")
        if isinstance(tool_call, dict) and isinstance(tool_call.get("name"), str):
            arguments = tool_call.get("arguments")
            return ToolCall(
                id=str(uuid4()),
                name=tool_call["name"],
                arguments=arguments if isinstance(arguments, dict) else {},
            )
    return None
