"""Adaptador Ollama — modelos locales (`DIRECCION_ACTUAL.md`, "Confirmado:
agregar Ollama", WP-V3-03).

`POST {base_url}/api/chat` vía `httpx.AsyncClient`. Ollama soporta
tool-calling nativo (a diferencia de los CLIs, ver `prompted_tools.py`):
traduce `ToolSpec` al mismo formato `{"type": "function", "function": {...}}`
que OpenAI-compat, y las respuestas `message.tool_calls` de vuelta a
`ToolCall` (Ollama no siempre incluye un `id` propio por tool call, así que
se genera uno con `uuid4` cuando falta).

Cien por ciento local: sin API key, sin llamada de red fuera de `localhost`
(o el `base_url` que el tenant configure) — es el proveedor "más privado y
gratis" de la lista (`DIRECCION_ACTUAL.md`). El timeout por defecto es
generoso (300s) porque los modelos locales pueden ser lentos según el
hardware del cliente.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from uuid import uuid4

import httpx

from .base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    Usage,
)
from .errors import LLMError

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434"
DEFAULT_TIMEOUT_SECONDS = 300.0


class OllamaProvider(LLMProvider):
    """Proveedor Ollama sobre `POST /api/chat` (REST puro, sin SDK)."""

    name = "ollama"

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model_principal: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") or DEFAULT_BASE_URL
        self._default_model = model_principal
        self._client = http_client or httpx.AsyncClient(base_url=self._base_url, timeout=timeout)

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    def _build_body(self, req: CompletionRequest, *, stream: bool) -> dict:
        body: dict = {
            "model": req.model or self._default_model or "",
            "messages": _to_ollama_messages(req),
            "stream": stream,
        }
        if req.tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.input_schema,
                    },
                }
                for t in req.tools
            ]
        return body

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        body = self._build_body(req, stream=False)
        response = await self._post(body)
        return _parse_completion(response.json())

    async def _post(self, body: dict) -> httpx.Response:
        try:
            response = await self._client.post("/api/chat", json=body)
        except httpx.ConnectError as exc:
            raise LLMError(_connect_error_message(self._base_url), provider=self.name) from exc
        if response.status_code >= 400:
            raise LLMError(
                f"Ollama devolvió {response.status_code}: {response.text}",
                provider=self.name,
                status_code=response.status_code,
            )
        return response

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        body = self._build_body(req, stream=True)
        try:
            async with self._client.stream("POST", "/api/chat", json=body) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise LLMError(
                        f"Ollama devolvió {response.status_code}: "
                        f"{detail.decode(errors='replace')}",
                        provider=self.name,
                        status_code=response.status_code,
                    )
                async for line in response.aiter_lines():
                    for chunk in _parse_stream_line(line):
                        yield chunk
        except httpx.ConnectError as exc:
            raise LLMError(_connect_error_message(self._base_url), provider=self.name) from exc


def _connect_error_message(base_url: str) -> str:
    return f"Ollama no está corriendo en {base_url}. Arranca Ollama o instala desde ollama.com"


def _to_ollama_messages(req: CompletionRequest) -> list[dict]:
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    for message in req.messages:
        if message.role == "system":
            messages.append({"role": "system", "content": _text_of(message.content)})
        elif message.role == "tool":
            messages.extend(_tool_result_messages(message.content))
        elif message.role == "assistant" and isinstance(message.content, list):
            messages.append(_assistant_blocks_to_ollama(message.content))
        else:
            content = (
                message.content if isinstance(message.content, str) else _text_of(message.content)
            )
            messages.append({"role": message.role, "content": content})
    return messages


def _assistant_blocks_to_ollama(blocks: list[dict]) -> dict:
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    entry: dict = {"role": "assistant", "content": text}
    if tool_use_blocks:
        entry["tool_calls"] = [
            {"function": {"name": b.get("name", ""), "arguments": b.get("input") or {}}}
            for b in tool_use_blocks
        ]
    return entry


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def _tool_result_messages(content: str | list[dict]) -> list[dict]:
    if isinstance(content, str):
        return [{"role": "tool", "content": content}]
    if not content:
        return [{"role": "tool", "content": ""}]
    messages: list[dict] = []
    for block in content:
        inner = block.get("content", "")
        text = inner if isinstance(inner, str) else _text_of(inner)
        messages.append({"role": "tool", "content": text})
    return messages


def _parse_completion(data: dict) -> CompletionResponse:
    message = data.get("message") or {}
    text = message.get("content") or ""
    tool_calls = [_to_tool_call(tc) for tc in message.get("tool_calls") or []]
    usage = Usage(
        input_tokens=data.get("prompt_eval_count") or 0,
        output_tokens=data.get("eval_count") or 0,
    )
    stop_reason = "tool_use" if tool_calls else "end"
    return CompletionResponse(
        text=text, tool_calls=tool_calls, usage=usage, stop_reason=stop_reason
    )


def _to_tool_call(tc: dict) -> ToolCall:
    function = tc.get("function") or {}
    return ToolCall(
        id=tc.get("id") or str(uuid4()),
        name=function.get("name", ""),
        arguments=_as_arguments(function.get("arguments")),
    )


def _as_arguments(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _parse_stream_line(line: str) -> list[StreamChunk]:
    line = line.strip()
    if not line:
        return []
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return []

    chunks: list[StreamChunk] = []
    message = data.get("message") or {}
    content = message.get("content")
    if content:
        chunks.append(StreamChunk(type="text", text=content))
    for tc in message.get("tool_calls") or []:
        chunks.append(StreamChunk(type="tool_call", tool_call=_to_tool_call(tc)))
    if data.get("done"):
        chunks.append(
            StreamChunk(
                type="usage",
                usage=Usage(
                    input_tokens=data.get("prompt_eval_count") or 0,
                    output_tokens=data.get("eval_count") or 0,
                ),
            )
        )
        chunks.append(StreamChunk(type="stop"))
    return chunks
