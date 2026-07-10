"""Tests de edecan_voice.registry: selección de proveedor y fallback seguro a stub."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from edecan_voice.deepgram import DeepgramSTT
from edecan_voice.elevenlabs import ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.registry import get_stt, get_tts
from edecan_voice.stubs import StubSTT, StubTTS


def _settings(**kwargs: object) -> SimpleNamespace:
    return SimpleNamespace(**kwargs)


# --- get_stt -----------------------------------------------------------------


def test_get_stt_defaults_to_stub_without_configuration():
    stt = get_stt(_settings())
    assert isinstance(stt, StubSTT)


def test_get_stt_uses_deepgram_when_provider_and_key_are_set():
    stt = get_stt(_settings(VOICE_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY="k"))
    assert isinstance(stt, DeepgramSTT)


def test_get_stt_falls_back_to_stub_when_deepgram_key_missing(caplog):
    with caplog.at_level(logging.WARNING):
        stt = get_stt(_settings(VOICE_STT_PROVIDER="deepgram", DEEPGRAM_API_KEY=""))
    assert isinstance(stt, StubSTT)
    assert "DEEPGRAM_API_KEY" in caplog.text


def test_get_stt_falls_back_to_stub_when_deepgram_key_attribute_absent(caplog):
    with caplog.at_level(logging.WARNING):
        stt = get_stt(_settings(VOICE_STT_PROVIDER="deepgram"))
    assert isinstance(stt, StubSTT)
    assert "DEEPGRAM_API_KEY" in caplog.text


def test_get_stt_falls_back_to_stub_for_unknown_provider(caplog):
    with caplog.at_level(logging.WARNING):
        stt = get_stt(_settings(VOICE_STT_PROVIDER="whisper-local"))
    assert isinstance(stt, StubSTT)
    assert "whisper-local" in caplog.text


def test_get_stt_provider_match_is_case_insensitive():
    stt = get_stt(_settings(VOICE_STT_PROVIDER="Deepgram", DEEPGRAM_API_KEY="k"))
    assert isinstance(stt, DeepgramSTT)


# --- get_tts -----------------------------------------------------------------


def test_get_tts_defaults_to_stub_without_configuration():
    tts = get_tts(_settings())
    assert isinstance(tts, StubTTS)


def test_get_tts_uses_elevenlabs_when_provider_and_key_are_set():
    tts = get_tts(
        _settings(
            VOICE_TTS_PROVIDER="elevenlabs",
            ELEVENLABS_API_KEY="k",
            ELEVENLABS_VOICE_ID="v",
        )
    )
    assert isinstance(tts, ElevenLabsTTS)


def test_get_tts_falls_back_to_stub_when_elevenlabs_key_missing(caplog):
    with caplog.at_level(logging.WARNING):
        tts = get_tts(_settings(VOICE_TTS_PROVIDER="elevenlabs"))
    assert isinstance(tts, StubTTS)
    assert "ELEVENLABS_API_KEY" in caplog.text


def test_get_tts_uses_polly_when_provider_is_polly():
    tts = get_tts(_settings(VOICE_TTS_PROVIDER="polly", POLLY_VOICE="Lupe"))
    assert isinstance(tts, PollyTTS)


def test_get_tts_polly_does_not_require_a_single_api_key_setting():
    # A diferencia de Deepgram/ElevenLabs, Polly usa la cadena estándar de
    # credenciales AWS (no una única variable "API key"), así que
    # seleccionarlo no debe caer a stub aunque no haya AWS_* configurado aquí.
    tts = get_tts(_settings(VOICE_TTS_PROVIDER="polly"))
    assert isinstance(tts, PollyTTS)


def test_get_tts_falls_back_to_stub_for_unknown_provider(caplog):
    with caplog.at_level(logging.WARNING):
        tts = get_tts(_settings(VOICE_TTS_PROVIDER="azure"))
    assert isinstance(tts, StubTTS)
    assert "azure" in caplog.text


def test_get_tts_provider_match_is_case_insensitive():
    tts = get_tts(_settings(VOICE_TTS_PROVIDER="ElevenLabs", ELEVENLABS_API_KEY="k"))
    assert isinstance(tts, ElevenLabsTTS)
