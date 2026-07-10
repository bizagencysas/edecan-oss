"""Tests de edecan_voice.pipeline.voice_turn (STT → turno del agente → TTS)."""

from __future__ import annotations

from edecan_voice.base import Transcript
from edecan_voice.pipeline import voice_turn


class _FakeSTT:
    def __init__(self, transcript: Transcript) -> None:
        self._transcript = transcript
        self.calls: list[tuple[bytes, str, str | None]] = []

    async def transcribe(self, audio: bytes, mime: str, language: str | None = None) -> Transcript:
        self.calls.append((audio, mime, language))
        return self._transcript


class _FakeTTS:
    def __init__(self, audio_bytes: bytes) -> None:
        self._audio_bytes = audio_bytes
        self.calls: list[tuple[str, str | None, str]] = []

    async def synthesize(self, text: str, voice_id: str | None = None, fmt: str = "mp3") -> bytes:
        self.calls.append((text, voice_id, fmt))
        return self._audio_bytes


async def test_voice_turn_runs_stt_then_agent_then_tts_in_order():
    stt = _FakeSTT(Transcript(text="hola asistente", language="es"))
    tts = _FakeTTS(b"audio-respuesta")
    call_order: list[str] = []

    async def run_agent_text(user_text: str) -> str:
        call_order.append("agent")
        assert user_text == "hola asistente"
        return "hola, ¿en qué te ayudo?"

    user_text, agent_text, audio = await voice_turn(
        stt, tts, run_agent_text, audio=b"audio-usuario", mime="audio/wav"
    )

    assert user_text == "hola asistente"
    assert agent_text == "hola, ¿en qué te ayudo?"
    assert audio == b"audio-respuesta"
    assert call_order == ["agent"]


async def test_voice_turn_passes_original_audio_and_mime_to_stt():
    stt = _FakeSTT(Transcript(text="texto", language="es"))
    tts = _FakeTTS(b"audio")

    async def run_agent_text(user_text: str) -> str:
        return "respuesta"

    await voice_turn(stt, tts, run_agent_text, audio=b"bytes-crudos", mime="audio/webm")

    assert stt.calls == [(b"bytes-crudos", "audio/webm", None)]


async def test_voice_turn_synthesizes_the_agent_reply_not_the_user_text():
    stt = _FakeSTT(Transcript(text="pregunta del usuario", language="es"))
    tts = _FakeTTS(b"audio-final")

    async def run_agent_text(user_text: str) -> str:
        return "respuesta del agente"

    await voice_turn(stt, tts, run_agent_text, audio=b"audio", mime="audio/wav")

    assert tts.calls == [("respuesta del agente", None, "mp3")]
