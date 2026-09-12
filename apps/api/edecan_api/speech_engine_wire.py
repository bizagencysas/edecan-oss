"""Adaptador mínimo del wire protocol de Speech Engine (ElevenLabs).

Implementa el protocolo documentado por el SDK oficial de ElevenLabs
(`elevenlabs.speech_engine.types`, verificado contra la fuente del paquete
2.68.0):

- Entrante: `init {conversation_id}`, `user_transcript {user_transcript:
  [{role: user|agent, content}], event_id: int}`, `ping`, `close`, `error`.
- Saliente: `agent_response {content, event_id, is_final}` y `pong`.

Se usa este adaptador (en vez de `SpeechEngineSession`) por UNA razón de
contrato: `SpeechEngineSession` no expone el `event_id` del turno a los
handlers (`session._current_event_id` es privado), y Edecán lo necesita para
los claims durables `(session_id, event_id)` contra replays
(`docs/speech-engine.md`). La semántica (cancelación del turno anterior ante
transcript nuevo, dedupe del mismo `event_id`, pong, ignore de tipos
desconocidos) es un espejo línea a línea del comportamiento verificado del
SDK.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


class TranscriptMessage:
    """Un mensaje del transcript completo que envía el proveedor por turno."""

    def __init__(self, *, role: str, content: str) -> None:
        self.role = role
        self.content = content


InitHandler = Callable[[str], Awaitable[None] | None]
TranscriptHandler = Callable[[list[TranscriptMessage], int], Awaitable[None] | None]
CloseHandler = Callable[[], Awaitable[None] | None]
ErrorHandler = Callable[[Exception], Awaitable[None] | None]


class SpeechEngineWireSession:
    """Loop de recepción + handlers por evento sobre un WebSocket ASGI/websockets.

    Acepta cualquier objeto con `receive_text()`/`send_text()`/`close()` (el
    `WebSocket` de Starlette/FastAPI, verificado: `wrap_websocket` del SDK
    oficial aplica la misma adaptación) o `recv()`/`send()` (websockets).
    """

    def __init__(self, ws: Any, *, debug: bool = False) -> None:
        self._ws = ws
        self._debug = debug
        self._closed = False
        self._conversation_id: str | None = None
        self._current_event_id: int | None = None
        self._current_task: asyncio.Task[Any] | None = None
        self._handlers: dict[str, list[Callable[..., Any]]] = {}
        # Detección de transporte idéntica a `wrap_websocket` del SDK oficial:
        # un WebSocket de Starlette/FastAPI NO tiene `recv` (su primitivo es
        # `receive_text`/`send_text`; el `send` que sí expone es el callable
        # ASGI que espera dicts de mensaje, no texto). Los de la librería
        # `websockets` tienen `recv`/`send` de texto.
        self._asgi_style = hasattr(ws, "receive_text") and not hasattr(ws, "recv")

    @property
    def conversation_id(self) -> str | None:
        return self._conversation_id

    @property
    def is_open(self) -> bool:
        return not self._closed

    def on(self, event: str, handler: Callable[..., Any]) -> SpeechEngineWireSession:
        self._handlers.setdefault(event, []).append(handler)
        return self

    # ------------------------------------------------------------------ loop

    async def run(self) -> None:
        try:
            while not self._closed:
                try:
                    raw = await self._recv()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._log("WebSocket connection lost")
                    break
                try:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8")
                    message = json.loads(raw)
                except (ValueError, TypeError, UnicodeDecodeError) as exc:
                    await self._emit("error", exc)
                    continue
                if not isinstance(message, dict):
                    await self._emit(
                        "error", ValueError(f"expected JSON object, got {type(message).__name__}")
                    )
                    continue
                await self._handle_message(message)
        except asyncio.CancelledError:
            raise
        finally:
            if not self._closed:
                self._closed = True
                await self._cancel_current_and_wait()
                await self._emit("disconnected")

    async def _recv(self) -> Any:
        if self._asgi_style:
            return await self._ws.receive_text()
        return await self._ws.recv()

    async def _send(self, message: dict[str, Any]) -> None:
        if self._closed:
            return
        payload = json.dumps(message)
        try:
            if self._asgi_style:
                await self._ws.send_text(payload)
            else:
                await self._ws.send(payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass

    async def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "init":
            self._conversation_id = message.get("conversation_id")
            self._log("session initialized, conversation_id=%s", self._conversation_id)
            await self._emit("init", self._conversation_id)
        elif message_type == "user_transcript":
            incoming_event_id = message.get("event_id")
            if (
                isinstance(incoming_event_id, int)
                and incoming_event_id == self._current_event_id
                and self._current_task is not None
                and not self._current_task.done()
            ):
                self._log("skipping duplicate transcript, event_id=%s", incoming_event_id)
                return
            was_active = self._current_task is not None and not self._current_task.done()
            await self._cancel_current_and_wait()
            if was_active:
                self._log(
                    "interrupted: cancelling previous response "
                    "(event_id=%s) for new transcript (event_id=%s)",
                    self._current_event_id,
                    incoming_event_id,
                )
            if not isinstance(incoming_event_id, int):
                await self._emit(
                    "error", ValueError(f"expected int event_id, got {incoming_event_id!r}")
                )
                return
            self._current_event_id = incoming_event_id
            raw_transcript = message.get("user_transcript") or []
            try:
                transcript = [
                    TranscriptMessage(role=item["role"], content=item["content"])
                    for item in raw_transcript
                ]
            except (KeyError, TypeError) as exc:
                await self._emit("error", exc)
                return
            self._log(
                "received transcript, event_id=%s, messages=%d",
                self._current_event_id,
                len(transcript),
            )
            handlers = list(self._handlers.get("user_transcript", []))
            if handlers:
                self._current_task = asyncio.create_task(
                    self._run_transcript_handlers(handlers, transcript, incoming_event_id)
                )
                await asyncio.sleep(0)
        elif message_type == "ping":
            await self._send({"type": "pong"})
        elif message_type == "close":
            self._closed = True
            await self._cancel_current_and_wait()
            await self._emit("close")
        elif message_type == "error":
            await self._emit("error", Exception(str(message.get("message", ""))))
        # Tipos desconocidos se ignoran (compatibilidad hacia adelante).

    async def _run_transcript_handlers(
        self,
        handlers: list[Callable[..., Any]],
        transcript: list[TranscriptMessage],
        event_id: int,
    ) -> None:
        try:
            for handler in handlers:
                result = handler(transcript, event_id)
                if asyncio.iscoroutine(result):
                    await result
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit("error", exc)

    # ------------------------------------------------------- envío de respuesta

    async def send_agent_response(self, content: str, *, is_final: bool) -> None:
        """Envía un delta de respuesta para el `event_id` del turno actual."""
        await self._send(
            {
                "type": "agent_response",
                "content": content,
                "event_id": self._current_event_id,
                "is_final": is_final,
            }
        )

    async def send_response_stream(self, chunks: Any) -> None:
        """Drena un iterable async de strings como deltas + cierre `is_final`.

        Cada elemento puede ser un `str` plano (que es lo que produce el
        servicio de turnos de Edecán) — el SDK oficial también acepta eventos
        de OpenAI/Anthropic/Gemini, pero acá el contrato interno es texto.
        """
        emitted = 0
        try:
            async for chunk in chunks:
                if self._closed:
                    return
                text = chunk if isinstance(chunk, str) else ""
                if text:
                    emitted += 1
                    await self.send_agent_response(text, is_final=False)
            if not self._closed:
                await self.send_agent_response("", is_final=True)
                self._log("stream complete: %d chunks sent", emitted)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._emit("error", exc)
            if not self._closed:
                await self.send_agent_response("", is_final=True)

    # ------------------------------------------------------------ internos

    async def _cancel_current_and_wait(self) -> None:
        task = self._current_task
        self._current_task = None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _emit(self, event: str, *args: Any) -> None:
        for handler in list(self._handlers.get(event, [])):
            try:
                result = handler(*args)
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if event != "error":
                    await self._emit("error", exc)
                else:
                    logger.exception("unhandled error in error handler: %s", exc)

    def _log(self, message: str, *args: Any) -> None:
        if self._debug:
            logger.info("[SpeechEngineWire] " + message, *args)