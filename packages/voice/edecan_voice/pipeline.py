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

from collections.abc import Awaitable, Callable

from edecan_voice.base import STTProvider, TTSProvider


async def voice_turn(
    stt: STTProvider,
    tts: TTSProvider,
    run_agent_text: Callable[[str], Awaitable[str]],
    audio: bytes,
    mime: str,
) -> tuple[str, str, bytes]:
    """Ejecuta un turno de voz completo.

    Retorna `(texto_usuario, texto_respuesta, audio_respuesta)`.

    1. Transcribe `audio` (tipo MIME `mime`) a texto de usuario vía `stt`.
    2. Ejecuta `run_agent_text(texto_usuario)` para obtener el texto de
       respuesta del agente (típicamente una envoltura sobre el loop de
       tool-use normal, colapsado de eventos SSE a un único string).
    3. Sintetiza el texto de respuesta a audio vía `tts`.
    """
    transcript = await stt.transcribe(audio, mime)
    user_text = transcript.text

    agent_text = await run_agent_text(user_text)

    audio_response = await tts.synthesize(agent_text)

    return user_text, agent_text, audio_response
