"""Adaptador OpenAI-compatible (`ARCHITECTURE.md` §10.6).

Sirve cualquier endpoint compatible con `POST {base_url}/chat/completions`
(OpenAI, Groq, Together.ai, vLLM, etc. — vía `OPENAI_COMPAT_BASE_URL` /
`OPENAI_COMPAT_API_KEY`). Traduce el formato común de `edecan_llm.base` hacia
y desde el formato de mensajes/herramientas de la API de Chat Completions.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx

from .base import (
    CompletionRequest,
    CompletionResponse,
    LLMProvider,
    StreamChunk,
    ToolCall,
    Usage,
)
from .errors import LLMError, ProviderDownError, RateLimitedError
from .multimodal import image_source, text_blocks

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60.0

_FINISH_REASON_MAP = {"tool_calls": "tool_use", "length": "max_tokens"}


class OpenAICompatProvider(LLMProvider):
    """Proveedor para cualquier API compatible con `/chat/completions` de OpenAI."""

    name = "openai_compat"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max(1, max_retries)
        self._retry_base_delay = retry_base_delay
        self._client = http_client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout
        )

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {self._api_key}", "content-type": "application/json"}

    def _build_body(self, req: CompletionRequest, *, stream: bool) -> dict:
        body: dict = {
            "model": req.model,
            "messages": _to_openai_messages(req),
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": stream,
        }
        if stream:
            # Sin este flag, la API real de OpenAI (y la mayoría de endpoints
            # OpenAI-compatible que siguen ese estándar) NO incluye `usage` en
            # ningún chunk del streaming, dejando el tracking de costo/billing
            # en 0 para ese turno (ver `_iter_openai_sse`).
            body["stream_options"] = {"include_usage": True}
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
        response = await self._post_with_retry(body)
        return _parse_completion(response.json())

    async def _post_with_retry(self, body: dict) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = await self._client.post(
                    "/chat/completions", json=body, headers=self._headers()
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderDownError(
                        f"No se pudo conectar con {self._client.base_url}: {exc}",
                        provider=self.name,
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise RateLimitedError(
                        f"Proveedor OpenAI-compat devolvió {response.status_code} "
                        f"tras {attempt} intento(s)",
                        provider=self.name,
                        status_code=response.status_code,
                    )
                await self._sleep_backoff(attempt, response=response)
                continue

            if response.status_code >= 400:
                raise LLMError(
                    f"Proveedor OpenAI-compat devolvió {response.status_code}: {response.text}",
                    provider=self.name,
                    status_code=response.status_code,
                )
            return response

    async def _sleep_backoff(self, attempt: int, *, response: httpx.Response | None = None) -> None:
        delay = self._retry_base_delay * (2 ** (attempt - 1))
        if response is not None:
            retry_after = response.headers.get("retry-after")
            if retry_after:
                try:
                    delay = max(delay, float(retry_after))
                except ValueError:
                    pass
        logger.warning("Reintentando llamada a %s en %.2fs (intento %d)", self.name, delay, attempt)
        await asyncio.sleep(delay)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        body = self._build_body(req, stream=True)
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._client.stream(
                    "POST", "/chat/completions", json=body, headers=self._headers()
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt >= self._max_retries:
                            raise RateLimitedError(
                                f"Proveedor OpenAI-compat devolvió {response.status_code} "
                                f"tras {attempt} intento(s)",
                                provider=self.name,
                                status_code=response.status_code,
                            )
                        await self._sleep_backoff(attempt, response=response)
                        continue
                    if response.status_code >= 400:
                        detail = await response.aread()
                        raise LLMError(
                            f"Proveedor OpenAI-compat devolvió {response.status_code}: "
                            f"{detail.decode(errors='replace')}",
                            provider=self.name,
                            status_code=response.status_code,
                        )
                    async for chunk in _iter_openai_sse(response):
                        yield chunk
                    return
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderDownError(
                        f"No se pudo conectar con {self._client.base_url}: {exc}",
                        provider=self.name,
                    ) from exc
                await self._sleep_backoff(attempt)


def _to_openai_messages(req: CompletionRequest) -> list[dict]:
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    for message in req.messages:
        if message.role == "system":
            messages.append({"role": "system", "content": _text_of(message.content)})
        elif message.role == "tool":
            messages.extend(_tool_result_messages(message.content))
        elif message.role == "assistant" and isinstance(message.content, list):
            messages.append(_assistant_blocks_to_openai(message.content))
        elif message.role == "user" and isinstance(message.content, list):
            messages.append({"role": "user", "content": _user_blocks_to_openai(message.content)})
        else:
            content = (
                message.content if isinstance(message.content, str) else _text_of(message.content)
            )
            messages.append({"role": message.role, "content": content})
    return messages


def _user_blocks_to_openai(blocks: list[dict]) -> list[dict]:
    content: list[dict] = []
    for block in blocks:
        if block.get("type") == "text" and block.get("text"):
            content.append({"type": "text", "text": str(block["text"])})
            continue
        source = image_source(block)
        if source is not None:
            mime, data = source
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{data}", "detail": "auto"},
                }
            )
    return content or [{"type": "text", "text": text_blocks(blocks)}]


def _assistant_blocks_to_openai(blocks: list[dict]) -> dict:
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    tool_use_blocks = [b for b in blocks if b.get("type") == "tool_use"]
    entry: dict = {"role": "assistant", "content": text or None}
    if tool_use_blocks:
        entry["tool_calls"] = [
            {
                "id": b.get("id", ""),
                "type": "function",
                "function": {
                    "name": b.get("name", ""),
                    "arguments": json.dumps(b.get("input") or {}, ensure_ascii=False),
                },
            }
            for b in tool_use_blocks
        ]
    return entry


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def _tool_result_messages(content: str | list[dict]) -> list[dict]:
    """Convierte un mensaje `role="tool"` a uno o más mensajes `tool` de OpenAI.

    `edecan_core.agent.Agent` empaqueta el resultado de CADA tool call de una
    misma vuelta (tool use paralelo) como un bloque `tool_result` dentro de un
    único `ChatMessage(role="tool")` (ver convención en `base.py`), igual que
    espera Anthropic (`anthropic._as_blocks` dobla la lista entera a un solo
    mensaje `user`). La API de Chat Completions, en cambio, exige un mensaje
    `role="tool"` independiente por cada `tool_call_id` que aparezca en el
    `tool_calls` del mensaje `assistant` anterior, así que cada bloque se
    traduce a su propio mensaje (leer solo `content[0]` perdería en silencio
    los resultados de la 2da llamada en adelante).
    """
    if isinstance(content, str):
        return [{"role": "tool", "tool_call_id": "", "content": content}]
    if not content:
        return [{"role": "tool", "tool_call_id": "", "content": ""}]
    messages: list[dict] = []
    for block in content:
        tool_call_id = (
            block.get("tool_use_id") or block.get("tool_call_id") or block.get("id") or ""
        )
        inner = block.get("content", "")
        text = inner if isinstance(inner, str) else _text_of(inner)
        messages.append({"role": "tool", "tool_call_id": tool_call_id, "content": text})
    return messages


def _parse_completion(data: dict) -> CompletionResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    text = message.get("content") or ""
    tool_calls = [
        ToolCall(
            id=tc.get("id", ""),
            name=(tc.get("function") or {}).get("name", ""),
            arguments=_safe_json_loads((tc.get("function") or {}).get("arguments", "")),
        )
        for tc in message.get("tool_calls") or []
    ]
    usage_data = data.get("usage") or {}
    usage = Usage(
        input_tokens=usage_data.get("prompt_tokens", 0),
        output_tokens=usage_data.get("completion_tokens", 0),
    )
    stop_reason = _FINISH_REASON_MAP.get(choice.get("finish_reason"), "end")
    return CompletionResponse(
        text=text, tool_calls=tool_calls, usage=usage, stop_reason=stop_reason
    )


def _safe_json_loads(raw: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


async def _iter_openai_sse(response: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Traduce el SSE (líneas `data: ...`, terminado en `data: [DONE]`) a `StreamChunk`."""
    tool_calls: dict[int, dict] = {}

    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        if payload == "[DONE]":
            break
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue

        usage_data = data.get("usage")
        if usage_data:
            yield StreamChunk(
                type="usage",
                usage=Usage(
                    input_tokens=usage_data.get("prompt_tokens", 0),
                    output_tokens=usage_data.get("completion_tokens", 0),
                ),
            )

        choices = data.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        if delta.get("content"):
            yield StreamChunk(type="text", text=delta["content"])
        for tc_delta in delta.get("tool_calls") or []:
            index = tc_delta.get("index", 0)
            entry = tool_calls.setdefault(index, {"id": "", "name": "", "parts": []})
            if tc_delta.get("id"):
                entry["id"] = tc_delta["id"]
            function_delta = tc_delta.get("function") or {}
            if function_delta.get("name"):
                entry["name"] = function_delta["name"]
            if function_delta.get("arguments"):
                entry["parts"].append(function_delta["arguments"])

    for index in sorted(tool_calls):
        entry = tool_calls[index]
        yield StreamChunk(
            type="tool_call",
            tool_call=ToolCall(
                id=entry["id"],
                name=entry["name"],
                arguments=_safe_json_loads("".join(entry["parts"])),
            ),
        )
    yield StreamChunk(type="stop")
