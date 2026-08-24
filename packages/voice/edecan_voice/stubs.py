"""Stubs offline y deterministas de STT/TTS (`ARCHITECTURE.md` §10.9).

Sin dependencias externas ni red: permiten correr todo el flujo de voz
(`edecan_voice.pipeline.voice_turn`) en desarrollo/self-host sin claves, y
son el *fallback* seguro de `edecan_voice.registry` cuando falta
configuración.
"""

from __future__ import annotations

import io
import wave
from typing import Literal

from edecan_core.veracidad import Fidelidad, ProveedorDeclarado

from edecan_voice.base import STTProvider, Transcript, TTSProvider

STUB_TRANSCRIPT_TEXT = "(transcripción de prueba)"
STUB_LANGUAGE = "es"

_SAMPLE_RATE_HZ = 16_000
_SAMPLE_WIDTH_BYTES = 2  # PCM de 16 bits
_CHANNELS = 1
_SILENCE_SECONDS = 0.5


class StubSTT(STTProvider, ProveedorDeclarado):
    """Transcriptor falso: siempre devuelve el mismo texto fijo, sin red.

    Deliberadamente ignora `audio`/`mime`/`language` para ser 100%
    determinista en tests y en self-host sin claves de STT.

    Hereda además `ProveedorDeclarado` (`edecan_core.veracidad`): declara
    `fidelidad=SIMULADO` con un `motivo_simulado` accionable, así que ninguna
    tool que lo use puede reportar la transcripción fija como si fuera lo
    que dijo el interlocutor real sin que el aviso llegue al modelo y a la
    app del dueño (ver `Agent.run_turn` en `edecan_core.agent`).
    """

    familia = "stt"
    fidelidad = Fidelidad.SIMULADO
    fuente = "transcripción fija offline"
    motivo_simulado = (
        "no hay proveedor de voz a texto conectado (falta VOICE_STT_PROVIDER=deepgram + "
        "DEEPGRAM_API_KEY, o la credencial de voz del tenant)"
    )

    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript:
        return Transcript(text=STUB_TRANSCRIPT_TEXT, language=STUB_LANGUAGE)


class StubTTS(TTSProvider, ProveedorDeclarado):
    """Sintetizador falso: genera un WAV PCM válido de 0.5 s de silencio.

    Ver docstring de `StubSTT`: mismo motivo para heredar `ProveedorDeclarado`.
    """

    familia = "tts"
    fidelidad = Fidelidad.SIMULADO
    fuente = "silencio offline (0.5s)"
    motivo_simulado = (
        "no hay proveedor de voz conectado (falta VOICE_TTS_PROVIDER=elevenlabs + "
        "ELEVENLABS_API_KEY, o la credencial de voz del tenant): este audio NO es una voz, "
        "es 0.5 segundos de silencio absoluto"
    )

    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        fmt: Literal["mp3", "wav"] = "mp3",
    ) -> bytes:
        n_frames = int(_SAMPLE_RATE_HZ * _SILENCE_SECONDS)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav_file:
            wav_file.setnchannels(_CHANNELS)
            wav_file.setsampwidth(_SAMPLE_WIDTH_BYTES)
            wav_file.setframerate(_SAMPLE_RATE_HZ)
            wav_file.writeframes(b"\x00" * n_frames * _SAMPLE_WIDTH_BYTES)
        return buffer.getvalue()
