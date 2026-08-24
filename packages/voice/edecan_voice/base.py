"""Contratos base de `edecan_voice`: STT/TTS intercambiables.

Firmas EXACTAS pinned en `ARCHITECTURE.md` §10.9 — cualquier implementación
(`DeepgramSTT`, `ElevenLabsTTS`, `PollyTTS`, los stubs, o un proveedor nuevo)
debe respetar estos contratos al pie de la letra, porque `apps/api` y
`edecan_voice.pipeline` se escriben contra ellos.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
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

    async def transcribe_stream(
        self,
        audio_chunks: AsyncIterator[bytes],
        *,
        mime: str,
    ) -> AsyncIterator[str]:
        """Transcribe `audio_chunks` a medida que llegan, rindiendo (yield)
        transcripciones PARCIALES antes de tener el audio completo.

        El PORQUÉ (realtime, PHASE2.md §2-6): para una UX de voz en vivo no
        alcanza con esperar a que termine de grabarse el audio y transcribir
        al final — el usuario ve texto intermedio conforme habla, y la
        aplicación puede reaccionar (p. ej. detectar fin de turno) antes de
        que el micrófono se cierre.

        Implementación por defecto compatible con los stubs: acumula TODOS
        los chunks y hace UNA sola transcripción al final (rinde el texto una
        vez, o nada si quedó vacío). Los proveedores reales (p. ej.
        `DeepgramSTT`) la sobreescriben para rendir transcripciones parciales
        mientras el audio aún se está recibiendo. No es abstracta a propósito:
        así `StubSTT` y cualquier proveedor futuro siguen siendo instanciables
        sin tocar el resto del paquete.
        """
        audio = b"".join([chunk async for chunk in audio_chunks])
        transcript = await self.transcribe(audio, mime)
        if transcript.text:
            yield transcript.text


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

    async def synthesize_stream(
        self,
        text: str,
        *,
        voice_id: str | None = None,
        model_id: str,
        mime: str,
    ) -> AsyncIterator[bytes]:
        """Sintetiza `text` y rinde (yield) los bytes de audio conforme se
        generan, en lugar de esperar a tener el audio completo.

        El PORQUÉ (time-to-first-audio, PHASE2.md §9): en voz web, el audio
        empieza a reproducirse en el cliente en cuanto llega el primer chunk,
        en vez de esperar a que ElevenLabs/Polly terminen de sintetizar el
        texto entero — para frases largas eso es la diferencia entre
        "responde al instante" y "pantalla en silencio varios segundos".

        `mime` describe el formato del audio rindiendo (p. ej.
        `"audio/mpeg"`); `model_id` es el modelo de síntesis del proveedor.
        Implementación por defecto compatible con los stubs: sintetiza el
        audio completo y lo rinde como UN único chunk. Los proveedores que
        soportan streaming real (`ElevenLabsTTS`) la sobreescriben. No es
        abstracta a propósito: así `StubTTS`/`PollyTTS` siguen siendo
        instanciables sin tocar el resto del paquete.
        """
        audio = await self.synthesize(text, voice_id=voice_id)
        yield audio
