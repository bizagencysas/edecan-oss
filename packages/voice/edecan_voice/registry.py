"""Selección de proveedor STT/TTS según configuración (`ARCHITECTURE.md` §10.2, §10.9).

`get_stt`/`get_tts` leen `settings` (típicamente la configuración
pydantic-settings de la app — cualquier objeto con los atributos listados en
`ARCHITECTURE.md` §10.2 sirve) y **nunca lanzan**: si el proveedor pedido no
tiene la credencial necesaria, caen de forma segura al stub correspondiente
y dejan constancia con `logging.warning`.
"""

from __future__ import annotations

import logging
from typing import Any

from edecan_voice.base import STTProvider, TTSProvider
from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubSTT, StubTTS

logger = logging.getLogger(__name__)


def _get(settings: Any, name: str, default: str | None = None) -> str | None:
    """Lee el atributo `name` de `settings` (p. ej. un objeto pydantic-settings)."""
    return getattr(settings, name, default)


def get_stt(settings: Any) -> STTProvider:
    """Resuelve el `STTProvider` activo según `VOICE_STT_PROVIDER`.

    - `"deepgram"` + `DEEPGRAM_API_KEY` presente → `DeepgramSTT`.
    - Cualquier otro caso (incluido `"deepgram"` sin clave, `"stub"`, u otro
      valor no reconocido) → `StubSTT`, con `logging.warning` si el usuario
      pidió explícitamente un proveedor que no se pudo activar.
    """
    provider = (_get(settings, "VOICE_STT_PROVIDER", "stub") or "stub").strip().lower()

    if provider == "deepgram":
        api_key = _get(settings, "DEEPGRAM_API_KEY")
        if api_key:
            return DeepgramSTT(api_key=api_key)
        logger.warning("VOICE_STT_PROVIDER=deepgram pero falta DEEPGRAM_API_KEY; usando StubSTT.")
        return StubSTT()

    if provider != "stub":
        logger.warning("VOICE_STT_PROVIDER=%r no reconocido; usando StubSTT.", provider)

    return StubSTT()


def get_tts(settings: Any) -> TTSProvider:
    """Resuelve el `TTSProvider` activo según `VOICE_TTS_PROVIDER`.

    - `"elevenlabs"` + `ELEVENLABS_API_KEY` presente → `ElevenLabsTTS`
      (con `ELEVENLABS_VOICE_ID` como voz por defecto, puede ser `None`).
    - `"polly"` → `PollyTTS` (usa `POLLY_VOICE`, `AWS_REGION` y
      `AWS_ENDPOINT_URL` si están definidos; las credenciales AWS se
      resuelven con la cadena estándar vía `allow_ambient_credentials=True`
      — legítimo AQUÍ porque `get_tts` es el resolver de un único tenant en
      self-host, donde `settings`/`.env` pertenece al propio operador; ver
      `edecan_voice.polly` y `docs/credenciales.md`. Multi-tenant
      (`edecan_voice.tenant`/`apps/api/edecan_api/routers/voice.py`) SOLO
      pasa ese mismo flag dentro de su propio chequeo
      `EDECAN_LOCAL_MODE=True`; sin eso cae a `StubTTS`).
    - Cualquier otro caso (incluido `"elevenlabs"` sin clave, `"stub"`, u
      otro valor no reconocido) → `StubTTS`, con `logging.warning` si el
      usuario pidió explícitamente un proveedor que no se pudo activar.
    """
    provider = (_get(settings, "VOICE_TTS_PROVIDER", "stub") or "stub").strip().lower()

    if provider == "elevenlabs":
        api_key = _get(settings, "ELEVENLABS_API_KEY")
        if api_key:
            return ElevenLabsTTS(
                api_key=api_key,
                default_voice_id=_get(settings, "ELEVENLABS_VOICE_ID"),
            )
        logger.warning(
            "VOICE_TTS_PROVIDER=elevenlabs pero falta ELEVENLABS_API_KEY; usando StubTTS."
        )
        return StubTTS()

    if provider == "polly":
        # `allow_ambient_credentials=True`: único caller legítimo (self-host
        # de UN tenant, ver docstring de esta función y de `edecan_voice.polly`).
        return PollyTTS(
            voice_id=_get(settings, "POLLY_VOICE", "Lupe") or "Lupe",
            region_name=_get(settings, "AWS_REGION"),
            endpoint_url=_get(settings, "AWS_ENDPOINT_URL"),
            allow_ambient_credentials=True,
        )

    if provider != "stub":
        logger.warning("VOICE_TTS_PROVIDER=%r no reconocido; usando StubTTS.", provider)

    return StubTTS()
