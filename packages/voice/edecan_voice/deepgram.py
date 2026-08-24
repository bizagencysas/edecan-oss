"""Deepgram como proveedor STT (`ARCHITECTURE.md` §10.9).

Usa el endpoint de transcripción *prerecorded* de Deepgram con el modelo
`nova-2`. Requiere `DEEPGRAM_API_KEY` (ver `ARCHITECTURE.md` §10.2 y
`.env.example`).
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

import httpx
from edecan_core.veracidad import Fidelidad, ProveedorDeclarado

from edecan_voice.base import STTProvider, Transcript

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
DEFAULT_LANGUAGE = "es"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Tamaño del buffer (en bytes) antes de lanzar un "flush" parcial a Deepgram
# en `transcribe_stream`. ~64 KB equivale a pocos segundos de audio webm/opus
# (el códec comprimido de voz típico del navegador): suficiente para que la
# transcripción parcial sea útil, sin multiplicar llamadas a la API.
DEFAULT_FLUSH_BYTES = 64 * 1024


class DeepgramSTT(STTProvider, ProveedorDeclarado):
    """Transcribe audio con la API de Deepgram (`POST /v1/listen`).

    Declara `fidelidad=REAL` (`edecan_core.veracidad`): solo se construye
    cuando hay `DEEPGRAM_API_KEY` (o la credencial propia del tenant, ver
    `edecan_voice.registry.get_stt`), nunca como fallback."""

    familia = "stt"
    fidelidad = Fidelidad.REAL
    fuente = "Deepgram"

    def __init__(
        self,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        flush_bytes: int = DEFAULT_FLUSH_BYTES,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._flush_bytes = max(1, flush_bytes)

    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript:
        resolved_language = language or DEFAULT_LANGUAGE
        params = {
            "model": "nova-2",
            "smart_format": "true",
            "language": resolved_language,
        }
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Content-Type": mime,
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                DEEPGRAM_LISTEN_URL, params=params, headers=headers, content=audio
            )
            response.raise_for_status()

        payload = response.json()
        alternative = payload["results"]["channels"][0]["alternatives"][0]
        return Transcript(
            text=alternative.get("transcript", ""),
            language=resolved_language,
            confidence=alternative.get("confidence"),
        )

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        mime: str,
    ) -> AsyncIterator[str]:
        """Transcribe `audio_chunks` rindiendo transcripciones PARCIALES a
        medida que llega el audio (realtime, PHASE2.md §2-6).

        Enfoque "buffer-and-flush" (documentado, deliberadamente más simple
        que el WebSocket live de Deepgram): se acumulan los chunks y, cada vez
        que el buffer supera `flush_bytes`, se envía TODO el buffer acumulado a
        `POST /v1/listen` (endpoint prerecorded) y se rinde la transcripción
        actual si cambió respecto a la anterior. Como cada flush incluye todo
        lo recibido hasta el momento, la transcripción rendida es cada vez más
        completa: el consumidor ve texto intermedio mientras el audio sigue
        llegando, y el último flush entrega el resultado final.

        Limitación honesta: esto NO son los `interim_results` del WebSocket
        live — cada flush es una petición prerecorded independiente (sin estado
        entre flushes) y re-transcribe el audio acumulado, así que el costo es
        mayor que un streaming nativo. Si el time-to-first-transcript se vuelve
        crítico, el siguiente paso es migrar al WebSocket
        (`wss://api.deepgram.com/v1/listen` con `interim_results=true`); este
        método conserva la MISMA interfaz `AsyncIterator[str]` para que ese
        cambio no toque a los consumidores."""
        buffer = bytearray()
        last_text = ""

        async for chunk in audio_chunks:
            buffer.extend(chunk)
            if len(buffer) < self._flush_bytes:
                continue
            transcript = await self.transcribe(bytes(buffer), mime)
            if transcript.text and transcript.text != last_text:
                last_text = transcript.text
                yield transcript.text

        if buffer:
            transcript = await self.transcribe(bytes(buffer), mime)
            if transcript.text and transcript.text != last_text:
                yield transcript.text
