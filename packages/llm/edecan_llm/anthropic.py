"""Adaptador Anthropic — proveedor LLM primario (`ARCHITECTURE.md` §10.6).

`POST {base_url}/v1/messages` vía `httpx.AsyncClient`, con headers `x-api-key`
y `anthropic-version: 2023-06-01`. Reintenta con backoff exponencial (hasta
`max_retries` intentos, 3 por defecto) ante `429` o `5xx`; si se agotan los
reintentos lanza `RateLimitedError`. Ante errores de conexión/timeout, tras
agotar los reintentos, lanza `ProviderDownError`.
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

logger = logging.getLogger(__name__)

ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_BASE_URL = "https://api.anthropic.com"

_STOP_REASON_MAP = {"tool_use": "tool_use", "max_tokens": "max_tokens"}


class AnthropicProvider(LLMProvider):
    """Proveedor Anthropic sobre REST puro (sin el SDK oficial)."""

    name = "anthropic"

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        retry_base_delay: float = 0.5,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._max_retries = max(1, max_retries)
        self._retry_base_delay = retry_base_delay
        self._client = http_client or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "content-type": "application/json",
        }

    def _build_body(self, req: CompletionRequest, *, stream: bool) -> dict:
        system_text, messages = _to_anthropic_messages(req)
        body: dict = {
            "model": req.model,
            "messages": messages,
            "max_tokens": req.max_tokens,
            "temperature": req.temperature,
            "stream": stream,
        }
        if system_text:
            body["system"] = system_text
        if req.tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.input_schema}
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
                    "/v1/messages", json=body, headers=self._headers()
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderDownError(
                        f"No se pudo conectar con Anthropic tras {attempt} intento(s): {exc}",
                        provider=self.name,
                    ) from exc
                await self._sleep_backoff(attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise RateLimitedError(
                        f"Anthropic devolvió {response.status_code} tras {attempt} intento(s)",
                        provider=self.name,
                        status_code=response.status_code,
                    )
                await self._sleep_backoff(attempt, response=response)
                continue

            if response.status_code >= 400:
                raise LLMError(
                    f"Anthropic devolvió {response.status_code}: {response.text}",
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
        logger.warning("Reintentando llamada a Anthropic en %.2fs (intento %d)", delay, attempt)
        await asyncio.sleep(delay)

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        body = self._build_body(req, stream=True)
        attempt = 0
        while True:
            attempt += 1
            try:
                async with self._client.stream(
                    "POST", "/v1/messages", json=body, headers=self._headers()
                ) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt >= self._max_retries:
                            raise RateLimitedError(
                                f"Anthropic devolvió {response.status_code} "
                                f"tras {attempt} intento(s)",
                                provider=self.name,
                                status_code=response.status_code,
                            )
                        await self._sleep_backoff(attempt, response=response)
                        continue
                    if response.status_code >= 400:
                        detail = await response.aread()
                        raise LLMError(
                            f"Anthropic devolvió {response.status_code}: "
                            f"{detail.decode(errors='replace')}",
                            provider=self.name,
                            status_code=response.status_code,
                        )
                    async for chunk in _iter_anthropic_sse(response):
                        yield chunk
                    return
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise ProviderDownError(
                        f"No se pudo conectar con Anthropic tras {attempt} intento(s): {exc}",
                        provider=self.name,
                    ) from exc
                await self._sleep_backoff(attempt)


def _to_anthropic_messages(req: CompletionRequest) -> tuple[str, list[dict]]:
    """Separa `req.system`/mensajes `role="system"` del resto y normaliza `tool`."""
    system_parts = [req.system] if req.system else []
    messages: list[dict] = []
    for message in req.messages:
        if message.role == "system":
            text = _text_of(message.content)
            if text:
                system_parts.append(text)
            continue
        if message.role == "tool":
            # Anthropic no tiene rol "tool": el resultado va como bloques
            # tool_result dentro de un mensaje "user".
            messages.append({"role": "user", "content": _as_blocks(message.content)})
            continue
        messages.append({"role": message.role, "content": message.content})
    return "\n\n".join(system_parts), messages


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if block.get("type") == "text")


def _as_blocks(content: str | list[dict]) -> list[dict]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return content


def _parse_completion(data: dict) -> CompletionResponse:
    blocks = data.get("content") or []
    text = "".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    tool_calls = [
        ToolCall(id=b.get("id", ""), name=b.get("name", ""), arguments=b.get("input") or {})
        for b in blocks
        if b.get("type") == "tool_use"
    ]
    usage_data = data.get("usage") or {}
    usage = Usage(
        input_tokens=usage_data.get("input_tokens", 0),
        output_tokens=usage_data.get("output_tokens", 0),
    )
    stop_reason = _STOP_REASON_MAP.get(data.get("stop_reason"), "end")
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


async def _iter_anthropic_sse(response: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Traduce el SSE de `/v1/messages` a `StreamChunk`.

    Maneja `message_start` (usage.input_tokens inicial), `content_block_start`/
    `content_block_delta`/`content_block_stop` (texto y `tool_use` acumulado vía
    `input_json_delta`), `message_delta` (usage.output_tokens final) y
    `message_stop`. Ignora `ping` y otros tipos desconocidos; propaga `error`
    como excepción.
    """
    input_tokens = 0
    tool_blocks: dict[int, dict] = {}

    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        payload = line[len("data:") :].strip()
        if not payload:
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type")

        if event_type == "message_start":
            input_tokens = event.get("message", {}).get("usage", {}).get("input_tokens", 0)

        elif event_type == "content_block_start":
            index = event.get("index", 0)
            block = event.get("content_block") or {}
            if block.get("type") == "tool_use":
                tool_blocks[index] = {
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "parts": [],
                }

        elif event_type == "content_block_delta":
            index = event.get("index", 0)
            delta = event.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = delta.get("text", "")
                if text:
                    yield StreamChunk(type="text", text=text)
            elif delta_type == "input_json_delta":
                block = tool_blocks.setdefault(index, {"id": "", "name": "", "parts": []})
                block["parts"].append(delta.get("partial_json", ""))

        elif event_type == "content_block_stop":
            index = event.get("index", 0)
            block = tool_blocks.pop(index, None)
            if block is not None:
                arguments = _safe_json_loads("".join(block["parts"]))
                yield StreamChunk(
                    type="tool_call",
                    tool_call=ToolCall(id=block["id"], name=block["name"], arguments=arguments),
                )

        elif event_type == "message_delta":
            usage_delta = event.get("usage") or {}
            output_tokens = usage_delta.get("output_tokens", 0)
            yield StreamChunk(
                type="usage", usage=Usage(input_tokens=input_tokens, output_tokens=output_tokens)
            )

        elif event_type == "message_stop":
            yield StreamChunk(type="stop")

        elif event_type == "error":
            error = event.get("error") or {}
            message = error.get("message", "error desconocido del stream de Anthropic")
            error_type = str(error.get("type", ""))
            if "rate_limit" in error_type or "overload" in error_type:
                raise RateLimitedError(message, provider="anthropic")
            raise LLMError(message, provider="anthropic")

        # "ping" y cualquier otro tipo de evento se ignoran deliberadamente.
