"""ElevenLabs como proveedor TTS (`ARCHITECTURE.md` §10.9).

Requiere `ELEVENLABS_API_KEY`; `ELEVENLABS_VOICE_ID` (ver `ARCHITECTURE.md`
§10.2 y `.env.example`) se usa como voz por defecto cuando la llamada no
especifica `voice_id`.
"""

from __future__ import annotations

import logging
from typing import Literal

import httpx

from edecan_voice.base import TTSProvider

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_TIMEOUT_SECONDS = 30.0


class ElevenLabsTTS(TTSProvider):
    """Sintetiza voz con la API de ElevenLabs (modelo multilingüe).

    ElevenLabs siempre devuelve audio mp3 en este endpoint: `fmt` se acepta
    por compatibilidad con la interfaz `TTSProvider`, pero se ignora (con
    aviso por logging si se pide algo distinto de `"mp3"`).
    """

    def __init__(
        self,
        api_key: str,
        default_voice_id: str | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._default_voice_id = default_voice_id
        self._timeout = timeout

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        fmt: Literal["mp3", "wav"] = "mp3",
    ) -> bytes:
        resolved_voice_id = voice_id or self._default_voice_id
        if not resolved_voice_id:
            raise ValueError(
                "ElevenLabsTTS requiere voice_id: pásalo por llamada o configura "
                "ELEVENLABS_VOICE_ID."
            )
        if fmt != "mp3":
            logger.warning("ElevenLabsTTS solo produce mp3; se ignora fmt=%r", fmt)

        headers = {"xi-api-key": self._api_key}
        payload = {"text": text, "model_id": DEFAULT_MODEL_ID}
        url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=resolved_voice_id)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

        return response.content
