"""ElevenLabs como proveedor TTS (`ARCHITECTURE.md` §10.9).

Requiere `ELEVENLABS_API_KEY`; `ELEVENLABS_VOICE_ID` (ver `ARCHITECTURE.md`
§10.2 y `.env.example`) se usa como voz por defecto cuando la llamada no
especifica `voice_id`.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Literal

import httpx
from edecan_core.veracidad import Fidelidad, ProveedorDeclarado

from edecan_voice.base import TTSProvider
from edecan_voice.expression import expressive_eleven_v3_text, plain_text_for_speech

logger = logging.getLogger(__name__)

ELEVENLABS_TTS_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_TTS_STREAM_URL_TEMPLATE = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
DEFAULT_MODEL_ID = "eleven_multilingual_v2"
DEFAULT_TIMEOUT_SECONDS = 30.0

# `voice_settings` vacío (no nulo) le dice a ElevenLabs "usa los ajustes
# guardados de la voz" — no fijamos stability/similarity/speed acá para no
# pisar la configuración que el dueño guardó para su voz, y para mantener el
# comportamiento idéntico al endpoint no-streaming de `synthesize` (que hoy no
# envía `voice_settings` en absoluto y deja que ElevenLabs aplique sus
# defaults). Se incluye la llave solo porque el contrato del streaming lo
# documenta; el valor vacío es un no-op seguro.
DEFAULT_VOICE_SETTINGS: dict[str, object] = {}


class ElevenLabsTTS(TTSProvider, ProveedorDeclarado):
    """Sintetiza voz con la API de ElevenLabs (modelo multilingüe).

    ElevenLabs siempre devuelve audio mp3 en este endpoint: `fmt` se acepta
    por compatibilidad con la interfaz `TTSProvider`, pero se ignora (con
    aviso por logging si se pide algo distinto de `"mp3"`).

    Declara `fidelidad=REAL` (`edecan_core.veracidad`): solo se construye con
    una `api_key` propia del tenant, nunca como fallback."""

    familia = "tts"
    fidelidad = Fidelidad.REAL
    fuente = "ElevenLabs"

    def __init__(
        self,
        api_key: str,
        default_voice_id: str | None = None,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        expressive: bool = False,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._api_key = api_key
        self._default_voice_id = default_voice_id
        self._model_id = model_id
        self._expressive = expressive and model_id == "eleven_v3"
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
        spoken_text = (
            expressive_eleven_v3_text(text) if self._expressive else plain_text_for_speech(text)
        )
        logger.info(
            "[ElevenLabs] _expressive=%s model_id=%s spoken_preview=%r",
            self._expressive, self._model_id, spoken_text[:300],
        )
        payload = {"text": spoken_text, "model_id": self._model_id}
        url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id=resolved_voice_id)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(url, headers=headers, json=payload)
            response.raise_for_status()

        return response.content

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model_id: str,
        mime: str,
    ) -> AsyncIterator[bytes]:
        """Sintetiza `text` con el endpoint de streaming de ElevenLabs y rinde
        los bytes de audio conforme llegan de la red (time-to-first-audio,
        PHASE2.md §9) — sin esperar a que la síntesis del texto completo
        termine.

        El endpoint `POST /v1/text-to-speech/{voice_id}/stream` devuelve el
        mismo mp3 que el endpoint no-streaming, pero fragmentado: `httpx`
        expone cada trozo vía `response.aiter_bytes()` y acá se reenvía tal
        cual al consumidor. El formato del audio se negocia con la cabecera
        `Accept` (=`mime`), no con `output_format` en la query, para seguir
        el contrato histórico de este endpoint (`Accept: audio/mpeg`)."""
        resolved_voice_id = voice_id or self._default_voice_id
        if not resolved_voice_id:
            raise ValueError(
                "ElevenLabsTTS requiere voice_id: pásalo por llamada o configura "
                "ELEVENLABS_VOICE_ID."
            )

        spoken_text = (
            expressive_eleven_v3_text(text) if self._expressive else plain_text_for_speech(text)
        )
        logger.info(
            "[ElevenLabs][stream] _expressive=%s model_id=%s mime=%s spoken_preview=%r",
            self._expressive, model_id, mime, spoken_text[:300],
        )
        headers = {"xi-api-key": self._api_key, "Accept": mime}
        payload = {
            "text": spoken_text,
            "model_id": model_id,
            "voice_settings": DEFAULT_VOICE_SETTINGS,
        }
        url = ELEVENLABS_TTS_STREAM_URL_TEMPLATE.format(voice_id=resolved_voice_id)

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as response:
                response.raise_for_status()
                async for chunk in response.aiter_bytes():
                    yield chunk
