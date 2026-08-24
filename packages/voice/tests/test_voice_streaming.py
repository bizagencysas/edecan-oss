"""Tests de los métodos de streaming (`synthesize_stream`/`transcribe_stream`)
contra respuestas HTTP simuladas con respx (ARCHITECTURE.md §10.9, PHASE2.md §2-6, §9).

Cubren:

- `ElevenLabsTTS.synthesize_stream`: petición correcta al endpoint `/stream`,
  cabecera `Accept`, body (`text`/`model_id`/`voice_settings`) y rendido de los
  chunks conforme llegan (time-to-first-audio).
- `DeepgramSTT.transcribe_stream`: buffer-and-flush con transcripciones
  parciales y dedup del texto repetido.
- Los defaults de `TTSProvider`/`STTProvider` (heredados por `StubTTS`/
  `StubSTT`): un único chunk / un único texto.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_voice.deepgram import DEEPGRAM_LISTEN_URL, DeepgramSTT
from edecan_voice.elevenlabs import ELEVENLABS_TTS_STREAM_URL_TEMPLATE, ElevenLabsTTS
from edecan_voice.stubs import StubSTT, StubTTS


def _deepgram_json(text: str) -> dict[str, object]:
    return {
        "results": {
            "channels": [{"alternatives": [{"transcript": text, "confidence": 0.9}]}]
        }
    }


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


# ---------------------------------------------------------------------------
# ElevenLabsTTS.synthesize_stream
# ---------------------------------------------------------------------------


@respx.mock
async def test_elevenlabs_synthesize_stream_sends_request_and_streams_chunks() -> None:
    url = ELEVENLABS_TTS_STREAM_URL_TEMPLATE.format(voice_id="voice-123")

    async def _audio_stream():
        yield b"chunk-a"
        yield b"chunk-b"

    route = respx.post(url).mock(return_value=httpx.Response(200, stream=_audio_stream()))

    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key")
    chunks = [
        c
        async for c in tts.synthesize_stream(
            "hola mundo", voice_id="voice-123", model_id="eleven_multilingual_v2",
            mime="audio/mpeg",
        )
    ]

    assert chunks == [b"chunk-a", b"chunk-b"]
    assert route.called
    request = route.calls.last.request
    assert request.headers["xi-api-key"] == "fake-elevenlabs-key"
    assert request.headers["Accept"] == "audio/mpeg"
    assert json.loads(request.content) == {
        "text": "hola mundo",
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {},
    }


@respx.mock
async def test_elevenlabs_synthesize_stream_uses_default_voice_id() -> None:
    url = ELEVENLABS_TTS_STREAM_URL_TEMPLATE.format(voice_id="default-voice")

    async def _audio_stream():
        yield b"x"

    route = respx.post(url).mock(return_value=httpx.Response(200, stream=_audio_stream()))

    tts = ElevenLabsTTS(api_key="fake-key", default_voice_id="default-voice")
    chunks = [
        c async for c in tts.synthesize_stream("hola", model_id="m", mime="audio/mpeg")
    ]

    assert route.called
    assert chunks == [b"x"]


async def test_elevenlabs_synthesize_stream_without_voice_id_raises_value_error() -> None:
    tts = ElevenLabsTTS(api_key="fake-key")
    with pytest.raises(ValueError):
        async for _ in tts.synthesize_stream("hola", model_id="m", mime="audio/mpeg"):
            pass


@respx.mock
async def test_elevenlabs_synthesize_stream_raises_on_http_error() -> None:
    url = ELEVENLABS_TTS_STREAM_URL_TEMPLATE.format(voice_id="voice-123")
    respx.post(url).mock(return_value=httpx.Response(500, content=b"boom"))

    tts = ElevenLabsTTS(api_key="fake-key")
    with pytest.raises(httpx.HTTPStatusError):
        async for _ in tts.synthesize_stream(
            "hola", voice_id="voice-123", model_id="m", mime="audio/mpeg"
        ):
            pass


@respx.mock
async def test_eleven_v3_stream_adds_expression_to_body() -> None:
    url = ELEVENLABS_TTS_STREAM_URL_TEMPLATE.format(voice_id="voice-123")

    async def _audio_stream():
        yield b"x"

    route = respx.post(url).mock(return_value=httpx.Response(200, stream=_audio_stream()))
    tts = ElevenLabsTTS(
        api_key="fake-key", default_voice_id="voice-123", model_id="eleven_v3", expressive=True
    )

    _ = [
        c
        async for c in tts.synthesize_stream(
            "**Listo**, quedó configurado.", model_id="eleven_v3", mime="audio/mpeg"
        )
    ]

    payload = json.loads(route.calls.last.request.content)
    assert payload["model_id"] == "eleven_v3"
    assert payload["voice_settings"] == {}
    assert payload["text"].startswith("[")
    assert "Listo, quedó configurado." in payload["text"]


# ---------------------------------------------------------------------------
# DeepgramSTT.transcribe_stream (buffer-and-flush)
# ---------------------------------------------------------------------------


@respx.mock
async def test_deepgram_transcribe_stream_yields_partial_transcripts_and_dedups() -> None:
    """Con `flush_bytes=4`, cada chunk de 4 bytes dispara un flush del buffer
    acumulado; el flush final repite el texto ya rendido y se descarta."""
    respx.post(DEEPGRAM_LISTEN_URL).mock(
        side_effect=[
            httpx.Response(200, json=_deepgram_json("hola")),
            httpx.Response(200, json=_deepgram_json("hola mun")),
            httpx.Response(200, json=_deepgram_json("hola mundo")),
            httpx.Response(200, json=_deepgram_json("hola mundo")),
        ]
    )

    stt = DeepgramSTT(api_key="fake-key", flush_bytes=4)
    texts = [
        t async for t in stt.transcribe_stream(
            _chunks(b"hola", b" mun", b"do"), mime="audio/webm"
        )
    ]

    assert texts == ["hola", "hola mun", "hola mundo"]
    assert respx.calls.call_count == 4  # 3 flushes en vivo + 1 flush final


@respx.mock
async def test_deepgram_transcribe_stream_buffers_then_final_flush_by_default() -> None:
    """Con el `flush_bytes` por defecto (64 KB), un audio corto NO cruza el
    umbral durante el loop: solo se rinde el flush final."""
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_deepgram_json("hola mundo"))
    )

    stt = DeepgramSTT(api_key="fake-key")
    texts = [
        t async for t in stt.transcribe_stream(_chunks(b"hola mundo"), mime="audio/wav")
    ]

    assert texts == ["hola mundo"]
    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Token fake-key"
    assert request.headers["Content-Type"] == "audio/wav"
    assert request.url.params["model"] == "nova-2"
    assert request.content == b"hola mundo"


@respx.mock
async def test_deepgram_transcribe_stream_yields_nothing_on_empty_input() -> None:
    respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(200, json=_deepgram_json(""))
    )

    stt = DeepgramSTT(api_key="fake-key", flush_bytes=4)
    texts = [t async for t in stt.transcribe_stream(_chunks(), mime="audio/wav")]

    assert texts == []


# ---------------------------------------------------------------------------
# Defaults de los ABC (heredados por los stubs)
# ---------------------------------------------------------------------------


async def test_stub_tts_synthesize_stream_yields_full_wav_as_one_chunk() -> None:
    tts = StubTTS()
    chunks = [
        c
        async for c in tts.synthesize_stream(
            "hola mundo", voice_id=None, model_id="cualquiera", mime="audio/wav"
        )
    ]
    assert len(chunks) == 1
    assert chunks[0][:4] == b"RIFF"


async def test_stub_stt_transcribe_stream_yields_fixed_text_once() -> None:
    stt = StubSTT()
    texts = [
        t async for t in stt.transcribe_stream(_chunks(b"a", b"b"), mime="audio/wav")
    ]
    assert texts == ["(transcripción de prueba)"]