"""Contratos base de `edecan_voice`: STT/TTS intercambiables.

Firmas EXACTAS pinned en `ARCHITECTURE.md` §10.9 — cualquier implementación
(`DeepgramSTT`, `ElevenLabsTTS`, `PollyTTS`, los stubs, o un proveedor nuevo)
debe respetar estos contratos al pie de la letra, porque `apps/api` y
`edecan_voice.pipeline` se escriben contra ellos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Literal


@dataclass
class Transcript:
    """Resultado de una transcripción de voz a texto (STT)."""

    text: str
    language: str
    confidence: float | None = None


class STTProvider(ABC):
    """Proveedor de *speech-to-text* (voz → texto)."""

    @abstractmethod
    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript:
        """Transcribe `audio` (con tipo MIME `mime`) a texto.

        `language` es un hint IETF/BCP-47 opcional (p. ej. `"es"`); si es
        `None`, cada implementación decide su idioma por defecto.
        """
        raise NotImplementedError


class TTSProvider(ABC):
    """Proveedor de *text-to-speech* (texto → voz)."""

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice_id: str | None = None,
        fmt: Literal["mp3", "wav"] = "mp3",
    ) -> bytes:
        """Sintetiza `text` a audio y retorna los bytes crudos en formato `fmt`.

        `voice_id` es opcional: si no se pasa, cada implementación usa su
        voz por defecto (p. ej. la configurada vía variable de entorno).
        """
        raise NotImplementedError
