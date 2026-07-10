"""Deepgram como proveedor STT (`ARCHITECTURE.md` §10.9).

Usa el endpoint de transcripción *prerecorded* de Deepgram con el modelo
`nova-2`. Requiere `DEEPGRAM_API_KEY` (ver `ARCHITECTURE.md` §10.2 y
`.env.example`).
"""

from __future__ import annotations

import logging

import httpx

from edecan_voice.base import STTProvider, Transcript

logger = logging.getLogger(__name__)

DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"
DEFAULT_LANGUAGE = "es"
DEFAULT_TIMEOUT_SECONDS = 30.0


class DeepgramSTT(STTProvider):
    """Transcribe audio con la API de Deepgram (`POST /v1/listen`)."""

    def __init__(self, api_key: str, *, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._api_key = api_key
        self._timeout = timeout

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
