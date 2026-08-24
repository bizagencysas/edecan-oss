"""Orquesta un turno de voz completo: STT → turno del agente → TTS.

`apps/api` se apoya en `voice_turn` para las rutas `/v1/voice/*`
(`ARCHITECTURE.md` §10.12: hoy `POST /v1/voice/transcribe` y
`POST /v1/voice/speak` como pasos separados; este helper es el que
compone ambos extremos alrededor de un turno de texto del agente
—`edecan_core.agent.Agent.run_turn` colapsado a texto— para cualquier
endpoint futuro que quiera hacer los tres pasos en una sola llamada, y
para la voz del *companion*).
"""

from __future__ import annotations

import logging
import time
from asyncio import CancelledError
from collections.abc import Awaitable, Callable

from edecan_voice.base import STTProvider, TTSProvider
from edecan_voice.realtime import RealtimeVoiceSession

logger = logging.getLogger(__name__)


async def voice_turn(
    stt: STTProvider,
    tts: TTSProvider,
    run_agent_text: Callable[[str], Awaitable[str]],
    audio: bytes,
    mime: str,
    realtime_session: RealtimeVoiceSession | None = None,
) -> tuple[str, str, bytes]:
    """Ejecuta un turno de voz completo.

    Retorna `(texto_usuario, texto_respuesta, audio_respuesta)`.

    1. Transcribe `audio` (tipo MIME `mime`) a texto de usuario vía `stt`.
    2. Ejecuta `run_agent_text(texto_usuario)` para obtener el texto de
       respuesta del agente (típicamente una envoltura sobre el loop de
       tool-use normal, colapsado de eventos SSE a un único string).
    3. Sintetiza el texto de respuesta a audio vía `tts`.

    Registra un log estructurado de latencia por fase (PHASE2.md §8):
    ``stt_ms`` (transcripción), ``orchestrator_ms`` (del texto del usuario al
    texto del agente) y ``tts_ms`` (síntesis al primer byte de audio). La
    "speech detection latency" vive en el cliente y no se mide acá, pero se
    deja el hueco en el log para correlacionar de un solo vistazo.
    """

    t_start = time.perf_counter()
    session = realtime_session
    turn_id: int | None = None
    if session is not None:
        turn_id = session.begin_listening()
        session.append_audio(len(audio))
        if not session.commit_audio(turn_id):
            raise RuntimeError("El turno de voz perdió su sesión antes de procesar el audio")

    try:
        transcript = await stt.transcribe(audio, mime)
        user_text = transcript.text
        t_after_stt = time.perf_counter()

        agent_text = await run_agent_text(user_text)
        t_after_agent = time.perf_counter()

        if session is not None and not session.begin_speaking(turn_id):
            raise CancelledError
        audio_response = await tts.synthesize(agent_text)
        t_after_tts = time.perf_counter()
        if session is not None:
            session.finish(turn_id)
    except CancelledError:
        if session is not None:
            session.interrupt("cancelled")
        raise

    logger.info(
        "[voice_turn] latency_ms stt=%.1f orchestrator=%.1f tts=%.1f total=%.1f "
        "speech_detection=client-side user_text_len=%d agent_text_len=%d",
        (t_after_stt - t_start) * 1000.0,
        (t_after_agent - t_after_stt) * 1000.0,
        (t_after_tts - t_after_agent) * 1000.0,
        (t_after_tts - t_start) * 1000.0,
        len(user_text),
        len(agent_text),
    )

    return user_text, agent_text, audio_response
