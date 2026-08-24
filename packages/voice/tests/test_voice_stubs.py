"""Tests de los stubs offline StubSTT/StubTTS (ARCHITECTURE.md §10.9)."""

from __future__ import annotations

import io
import wave

import pytest
from edecan_voice.stubs import StubSTT, StubTTS


async def test_stub_stt_returns_fixed_transcript():
    stt = StubSTT()
    transcript = await stt.transcribe(b"cualquier-audio", mime="audio/wav")
    assert transcript.text == "(transcripción de prueba)"
    assert transcript.language == "es"
    assert transcript.confidence is None


async def test_stub_stt_is_deterministic_regardless_of_input():
    stt = StubSTT()
    first = await stt.transcribe(b"a", mime="audio/wav", language="en")
    second = await stt.transcribe(b"b" * 1000, mime="audio/mpeg", language=None)
    assert first == second


async def test_stub_tts_produces_valid_wav_riff_header():
    tts = StubTTS()
    audio = await tts.synthesize("hola mundo")
    assert audio[0:4] == b"RIFF"
    assert audio[8:12] == b"WAVE"


async def test_stub_tts_ignores_fmt_and_always_returns_wav_header():
    tts = StubTTS()
    audio = await tts.synthesize("hola", voice_id="cualquiera", fmt="mp3")
    assert audio[0:4] == b"RIFF"


async def test_stub_tts_wav_is_half_second_of_silence():
    tts = StubTTS()
    audio = await tts.synthesize("hola")

    with wave.open(io.BytesIO(audio), "rb") as wav_file:
        n_channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        frame_rate = wav_file.getframerate()
        n_frames = wav_file.getnframes()
        raw = wav_file.readframes(n_frames)

    assert n_channels == 1
    assert sample_width == 2
    assert n_frames / frame_rate == pytest.approx(0.5)
    assert raw == b"\x00" * len(raw)
    assert len(raw) > 0
