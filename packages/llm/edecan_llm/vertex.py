"""Adaptador Vertex AI / Gemini — dos modos (`DIRECCION_ACTUAL.md`, "API
normal de Vertex AI", WP-V3-03):

- `mode="api_key"` (default — el camino simple que prioriza
  `DIRECCION_ACTUAL.md`: "preferir por defecto el camino más simple que
  funcione"): la API pública de Gemini
  (`https://generativelanguage.googleapis.com`), autenticada con un solo
  parámetro `?key=`. Sin proyecto GCP, sin service account.
- `mode="service_account"` (avanzado, bring-your-own proyecto GCP): el
  endpoint real de Vertex AI
  (`https://{region}-aiplatform.googleapis.com/.../publishers/google/models/
  {model}:generateContent`), autenticado con un access token OAuth2 obtenido
  de una clave de cuenta de servicio vía `google-auth` — extra opcional, ver
  `pyproject.toml` (`edecan-llm[vertex]`) y `docs/proveedores-llm.md`. El
  import de `google.oauth2` es diferido y guardeado: si el extra no está
  instalado, `LLMError` con instrucciones claras en vez de un traceback
  críptico.

Ambos modos hablan el mismo wire format (`generateContent`/
`streamGenerateContent`) y comparten toda la traducción de mensajes/tools.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
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
from .config import LLMProviderConfig
from .errors import LLMError, ProviderDownError

logger = logging.getLogger(__name__)

DEFAULT_REGION = "us-central1"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
_VERTEX_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]
_TOKEN_EXPIRY_SKEW_SECONDS = 60
_DEFAULT_TOKEN_TTL_SECONDS = 3600

TokenProvider = Callable[[], Awaitable[str]]

_FINISH_REASON_MAP = {"MAX_TOKENS": "max_tokens"}


class VertexAIProvider(LLMProvider):
    """Proveedor Google Vertex AI / Gemini — ver docstring del módulo."""

    name = "vertex"

    def __init__(
        self,
        config: LLMProviderConfig,
        *,
        http_client: httpx.AsyncClient | None = None,
        token_provider: TokenProvider | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._mode = config.extra.get("mode") or "api_key"
        if self._mode not in ("api_key", "service_account"):
            raise LLMError(
                f"Vertex AI: extra.mode desconocido {self._mode!r} "
                "(usa 'api_key' o 'service_account').",
                provider=self.name,
            )

        self._region = config.extra.get("region") or DEFAULT_REGION
        self._project_id = config.extra.get("project_id")
        self._api_key = config.api_key
        self._client = http_client or httpx.AsyncClient(timeout=timeout)
        self._token_provider = token_provider
        self._credentials: object | None = None
        self._cached_token: str | None = None
        self._cached_token_expiry: float = 0.0

        if self._mode == "api_key" and not self._api_key:
            raise LLMError(
                "Vertex AI en modo 'api_key' requiere api_key (la API key de "
                "Gemini/Vertex AI Studio).",
                provider=self.name,
            )
        if self._mode == "service_account":
            if not self._project_id:
                raise LLMError(
                    "Vertex AI en modo 'service_account' requiere extra.project_id.",
                    provider=self.name,
                )
            if self._token_provider is None:
                self._credentials = _build_credentials(config.extra.get("service_account_json"))

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    async def complete(self, req: CompletionRequest) -> CompletionResponse:
        url, headers = await self._endpoint(req.model, streaming=False)
        body = _build_body(req)
        try:
            response = await self._client.post(url, json=body, headers=headers)
        except httpx.TransportError as exc:
            raise ProviderDownError(
                f"No se pudo conectar con Vertex AI/Gemini: {exc}", provider=self.name
            ) from exc
        if response.status_code >= 400:
            raise LLMError(
                f"Vertex AI/Gemini devolvió {response.status_code}: {response.text}",
                provider=self.name,
                status_code=response.status_code,
            )
        return _parse_completion(response.json())

    async def stream(self, req: CompletionRequest) -> AsyncIterator[StreamChunk]:
        url, headers = await self._endpoint(req.model, streaming=True)
        body = _build_body(req)
        try:
            async with self._client.stream("POST", url, json=body, headers=headers) as response:
                if response.status_code >= 400:
                    detail = await response.aread()
                    raise LLMError(
                        f"Vertex AI/Gemini devolvió {response.status_code}: "
                        f"{detail.decode(errors='replace')}",
                        provider=self.name,
                        status_code=response.status_code,
                    )
                async for chunk in _iter_sse(response):
                    yield chunk
        except httpx.TransportError as exc:
            raise ProviderDownError(
                f"No se pudo conectar con Vertex AI/Gemini: {exc}", provider=self.name
            ) from exc

    async def _endpoint(self, model: str, *, streaming: bool) -> tuple[str, dict[str, str]]:
        method = "streamGenerateContent" if streaming else "generateContent"

        if self._mode == "service_account":
            token = await self._get_token()
            url = (
                f"https://{self._region}-aiplatform.googleapis.com/v1/projects/"
                f"{self._project_id}/locations/{self._region}/publishers/google/"
                f"models/{model}:{method}"
            )
            if streaming:
                url += "?alt=sse"
            headers = {"authorization": f"Bearer {token}", "content-type": "application/json"}
            return url, headers

        url = f"{GEMINI_API_BASE}/v1beta/models/{model}:{method}?key={self._api_key}"
        if streaming:
            url += "&alt=sse"
        return url, {"content-type": "application/json"}

    async def _get_token(self) -> str:
        if self._token_provider is not None:
            return await self._token_provider()

        now = time.time()
        if self._cached_token and now < self._cached_token_expiry:
            return self._cached_token

        if self._credentials is None:  # pragma: no cover - garantizado por __init__
            raise LLMError(
                "Vertex AI en modo 'service_account' sin credenciales construidas.",
                provider=self.name,
            )

        await asyncio.to_thread(self._credentials.refresh, _HttpxAuthRequest())  # type: ignore[attr-defined]
        self._cached_token = self._credentials.token  # type: ignore[attr-defined]
        expiry = getattr(self._credentials, "expiry", None)
        self._cached_token_expiry = (
            expiry.timestamp() - _TOKEN_EXPIRY_SKEW_SECONDS
            if expiry is not None
            else now + _DEFAULT_TOKEN_TTL_SECONDS - _TOKEN_EXPIRY_SKEW_SECONDS
        )
        return self._cached_token


def _build_credentials(service_account_json: str | None) -> object:
    if not service_account_json:
        raise LLMError(
            "Vertex AI en modo 'service_account' requiere extra.service_account_json "
            "(el JSON completo de la clave de la cuenta de servicio).",
            provider="vertex",
        )
    try:
        from google.oauth2 import service_account
    except ImportError as exc:
        raise LLMError(
            "Vertex AI en modo 'service_account' requiere el extra opcional de "
            'google-auth: instala con `uv pip install "edecan-llm[vertex]"`.',
            provider="vertex",
        ) from exc
    try:
        info = json.loads(service_account_json)
    except json.JSONDecodeError as exc:
        raise LLMError(
            "extra.service_account_json de Vertex AI no es JSON válido.", provider="vertex"
        ) from exc
    try:
        return service_account.Credentials.from_service_account_info(info, scopes=_VERTEX_SCOPES)
    except (TypeError, ValueError) as exc:
        # `google-auth` usa `MalformedError(ValueError)` cuando el JSON existe
        # pero no es una credencial de cuenta de servicio completa. Exponer esa
        # excepción interna rompe el contrato común de proveedores y termina
        # mostrando un traceback técnico en Ajustes.
        raise LLMError(
            "extra.service_account_json de Vertex AI no contiene una credencial "
            "de cuenta de servicio válida.",
            provider="vertex",
        ) from exc


class _HttpxAuthRequest:
    """Adaptador mínimo de `google.auth.transport.Request` sobre `httpx`
    síncrono — evita que este proveedor dependa también de `requests`/
    `urllib3` (el único extra opcional pinned es `google-auth`, ver
    `pyproject.toml`). Solo se instancia dentro de `asyncio.to_thread`
    (`Credentials.refresh` es una llamada bloqueante de `google-auth`), y
    nunca la ejercitan los tests de este paquete (mockean `token_provider`).
    """

    def __call__(
        self,
        url: str,
        method: str = "GET",
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
        **_kwargs: object,
    ) -> _HttpxAuthResponse:
        with httpx.Client(timeout=timeout or 60.0) as client:
            response = client.request(method, url, content=body, headers=headers)
        return _HttpxAuthResponse(response)


class _HttpxAuthResponse:
    """Adaptador de `google.auth.transport.Response` sobre `httpx.Response`."""

    def __init__(self, response: httpx.Response) -> None:
        self.status = response.status_code
        self.headers = dict(response.headers)
        self.data = response.content


def _build_body(req: CompletionRequest) -> dict:
    system_instruction, contents = _to_gemini_contents(req)
    body: dict = {
        "contents": contents,
        "generationConfig": {
            "maxOutputTokens": req.max_tokens,
            "temperature": req.temperature,
        },
    }
    if system_instruction is not None:
        body["systemInstruction"] = system_instruction
    if req.tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {"name": t.name, "description": t.description, "parameters": t.input_schema}
                    for t in req.tools
                ]
            }
        ]
    return body


def _to_gemini_contents(req: CompletionRequest) -> tuple[dict | None, list[dict]]:
    """`system` → `systemInstruction`; `user`/`assistant` → `contents` con
    `role` `"user"`/`"model"`; `tool` → `role="function"` con una parte
    `functionResponse` por bloque. Gemini correlaciona `functionResponse`
    con su `functionCall` por NOMBRE (no por id, a diferencia de Anthropic/
    OpenAI) — `call_names` recuerda `tool_use_id -> nombre` a medida que se
    recorren los `assistant` con `tool_use`, para poder resolver el nombre
    correcto cuando llega el `tool_result` correspondiente más adelante.
    """
    system_parts: list[str] = [req.system] if req.system else []
    contents: list[dict] = []
    call_names: dict[str, str] = {}

    for message in req.messages:
        if message.role == "system":
            text = _text_of(message.content)
            if text:
                system_parts.append(text)
        elif message.role == "user":
            contents.append({"role": "user", "parts": [{"text": _text_of(message.content)}]})
        elif message.role == "assistant":
            contents.append(
                {"role": "model", "parts": _assistant_parts(message.content, call_names)}
            )
        elif message.role == "tool":
            contents.append(
                {"role": "function", "parts": _tool_result_parts(message.content, call_names)}
            )

    system_instruction = {"parts": [{"text": "\n\n".join(system_parts)}]} if system_parts else None
    return system_instruction, contents


def _text_of(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    return "".join(b.get("text", "") for b in content if b.get("type") == "text")


def _assistant_parts(content: str | list[dict], call_names: dict[str, str]) -> list[dict]:
    if isinstance(content, str):
        return [{"text": content}]
    parts: list[dict] = []
    for block in content:
        if block.get("type") == "text" and block.get("text"):
            parts.append({"text": block["text"]})
        elif block.get("type") == "tool_use":
            name = block.get("name", "")
            call_id = block.get("id")
            if call_id:
                call_names[call_id] = name
            parts.append({"functionCall": {"name": name, "args": block.get("input") or {}}})
    return parts or [{"text": ""}]


def _tool_result_parts(content: str | list[dict], call_names: dict[str, str]) -> list[dict]:
    if isinstance(content, str):
        return [{"functionResponse": {"name": "", "response": {"result": content}}}]
    parts: list[dict] = []
    for block in content:
        inner = block.get("content", "")
        text = inner if isinstance(inner, str) else _text_of(inner)
        tool_use_id = block.get("tool_use_id") or block.get("tool_call_id") or ""
        name = call_names.get(tool_use_id, "")
        parts.append({"functionResponse": {"name": name, "response": {"result": text}}})
    return parts


def _parse_completion(data: dict) -> CompletionResponse:
    candidates = data.get("candidates") or []
    candidate = candidates[0] if candidates else {}
    parts = (candidate.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if "text" in p)
    tool_calls = [
        ToolCall(
            id=str(uuid4()),
            name=(p.get("functionCall") or {}).get("name", ""),
            arguments=(p.get("functionCall") or {}).get("args") or {},
        )
        for p in parts
        if "functionCall" in p
    ]
    usage_data = data.get("usageMetadata") or {}
    usage = Usage(
        input_tokens=usage_data.get("promptTokenCount", 0),
        output_tokens=usage_data.get("candidatesTokenCount", 0),
    )
    stop_reason = "tool_use" if tool_calls else _FINISH_REASON_MAP.get(
        candidate.get("finishReason"), "end"
    )
    return CompletionResponse(
        text=text, tool_calls=tool_calls, usage=usage, stop_reason=stop_reason
    )


async def _iter_sse(response: httpx.Response) -> AsyncIterator[StreamChunk]:
    """Traduce el SSE de `:streamGenerateContent?alt=sse` a `StreamChunk`:
    cada evento `data:` trae un candidato parcial (texto y/o `functionCall`);
    el último suele traer además `usageMetadata`.
    """
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

        candidates = event.get("candidates") or []
        candidate = candidates[0] if candidates else {}
        parts = (candidate.get("content") or {}).get("parts") or []
        for part in parts:
            if part.get("text"):
                yield StreamChunk(type="text", text=part["text"])
            elif "functionCall" in part:
                function_call = part["functionCall"]
                yield StreamChunk(
                    type="tool_call",
                    tool_call=ToolCall(
                        id=str(uuid4()),
                        name=function_call.get("name", ""),
                        arguments=function_call.get("args") or {},
                    ),
                )

        usage_data = event.get("usageMetadata")
        if usage_data:
            yield StreamChunk(
                type="usage",
                usage=Usage(
                    input_tokens=usage_data.get("promptTokenCount", 0),
                    output_tokens=usage_data.get("candidatesTokenCount", 0),
                ),
            )

    yield StreamChunk(type="stop")
