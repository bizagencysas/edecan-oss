"""Bridge efímero localhost: FyDesign usa el router LLM de Edecán sin claves."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Any

from edecan_core import ToolContext
from edecan_llm.base import ChatMessage, CompletionRequest

_MAX_HEADERS = 16 * 1024
_MAX_BODY = 24 * 1024 * 1024


def _tenant_flags(ctx: ToolContext) -> dict[str, Any]:
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    flags = extras.get("flags")
    return flags if isinstance(flags, dict) else {}


class EdecanLLMBridge:
    """Servidor de vida igual a una ejecución y autenticado por capacidad aleatoria."""

    def __init__(self, ctx: ToolContext) -> None:
        self._ctx = ctx
        self._token = secrets.token_urlsafe(32)
        self._server: asyncio.Server | None = None
        self.environment: dict[str, str] = {}

    async def __aenter__(self) -> dict[str, str]:
        if self._ctx.llm is None:
            return {}
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=_MAX_BODY)
        socket = self._server.sockets[0]
        port = int(socket.getsockname()[1])
        self.environment = {
            "FYDESIGN_LLM_BRIDGE_URL": f"http://127.0.0.1:{port}/complete",
            "FYDESIGN_LLM_BRIDGE_TOKEN": self._token,
        }
        return dict(self.environment)

    async def __aexit__(self, _exc_type: Any, _exc: Any, _tb: Any) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self.environment = {}
        self._token = ""

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode()
        reason = "OK" if status == 200 else "Error"
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            headers_raw = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
            if len(headers_raw) > _MAX_HEADERS:
                await self._respond(writer, 431, {"error": "headers"})
                return
            lines = headers_raw.decode("latin-1").split("\r\n")
            request = lines[0].split()
            headers = {
                key.strip().lower(): value.strip()
                for line in lines[1:]
                if ":" in line
                for key, value in [line.split(":", 1)]
            }
            if request[:2] != ["POST", "/complete"]:
                await self._respond(writer, 404, {"error": "not found"})
                return
            if headers.get("authorization") != f"Bearer {self._token}":
                await self._respond(writer, 401, {"error": "unauthorized"})
                return
            length = int(headers.get("content-length", "0"))
            if length <= 0 or length > _MAX_BODY:
                await self._respond(writer, 413, {"error": "body"})
                return
            raw = await asyncio.wait_for(reader.readexactly(length), timeout=30)
            payload = json.loads(raw)
            prompt = str(payload.get("prompt") or "")
            system = str(payload.get("system") or "")
            if not prompt or len(prompt) > 1_000_000 or len(system) > 250_000:
                await self._respond(writer, 400, {"error": "prompt"})
                return
            content: str | list[dict[str, Any]] = prompt
            images = payload.get("images")
            if isinstance(images, list) and images:
                blocks: list[dict[str, Any]] = []
                for image in images[:8]:
                    if not isinstance(image, dict):
                        continue
                    mime = str(image.get("mimeType") or "")
                    data = str(image.get("data") or "")
                    if mime in {"image/png", "image/jpeg", "image/webp", "image/gif"} and data:
                        blocks.append(
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": mime,
                                    "data": data,
                                },
                            }
                        )
                blocks.append({"type": "text", "text": prompt})
                content = blocks
            response = await self._ctx.llm.complete(
                "principal",
                _tenant_flags(self._ctx),
                CompletionRequest(
                    model="principal",
                    system=system or None,
                    messages=[ChatMessage(role="user", content=content)],
                    max_tokens=max(256, min(int(payload.get("maxTokens") or 8192), 32_000)),
                    temperature=max(0.0, min(float(payload.get("temperature") or 0.7), 1.0)),
                ),
            )
            text = response.text.strip()
            if not text:
                raise ValueError("empty model output")
            await self._respond(writer, 200, {"text": text})
        except Exception:  # noqa: BLE001 - jamás reflejar prompts, tokens o errores del proveedor
            if not writer.is_closing():
                await self._respond(writer, 500, {"error": "completion failed"})


__all__ = ["EdecanLLMBridge"]
