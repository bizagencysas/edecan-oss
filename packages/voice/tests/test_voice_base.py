"""Tests de los contratos base de edecan_voice (ARCHITECTURE.md §10.9)."""

from __future__ import annotations

import inspect

import pytest
from edecan_voice.base import STTProvider, Transcript, TTSProvider


def test_transcript_defaults_confidence_to_none():
    transcript = Transcript(text="hola", language="es")
    assert transcript.text == "hola"
    assert transcript.language == "es"
    assert transcript.confidence is None


def test_transcript_accepts_confidence():
    transcript = Transcript(text="hola", language="es", confidence=0.42)
    assert transcript.confidence == 0.42


def test_stt_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        STTProvider()  # type: ignore[abstract]


def test_tts_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        TTSProvider()  # type: ignore[abstract]


def test_stt_provider_transcribe_signature_is_pinned():
    signature = inspect.signature(STTProvider.transcribe)
    assert list(signature.parameters) == ["self", "audio", "mime", "language"]
    assert signature.parameters["language"].default is None


def test_tts_provider_synthesize_signature_is_pinned():
    signature = inspect.signature(TTSProvider.synthesize)
    assert list(signature.parameters) == ["self", "text", "voice_id", "fmt"]
    assert signature.parameters["voice_id"].default is None
    assert signature.parameters["fmt"].default == "mp3"


def test_subclass_must_implement_transcribe_to_be_instantiable():
    class Incomplete(STTProvider):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]


def test_subclass_must_implement_synthesize_to_be_instantiable():
    class Incomplete(TTSProvider):
        pass

    with pytest.raises(TypeError):
        Incomplete()  # type: ignore[abstract]
