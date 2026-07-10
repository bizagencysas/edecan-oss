"""Tests de ElevenLabsTTS contra respuestas HTTP simuladas con respx (ARCHITECTURE.md §10.9)."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from edecan_voice.elevenlabs import ELEVENLABS_TTS_URL_TEMPLATE, ElevenLabsTTS

FAKE_MP3_BYTES = b"ID3-fake-mp3-bytes"


@respx.mock
async def test_elevenlabs_synthesize_sends_expected_request():
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="voice-123")
    route = respx.post(url).mock(return_value=httpx.Response(200, content=FAKE_MP3_BYTES))

    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key")
    audio = await tts.synthesize("hola mundo", voice_id="voice-123")

    assert route.called
    request = route.calls.last.request
    assert request.headers["xi-api-key"] == "fake-elevenlabs-key"
    assert json.loads(request.content) == {
        "text": "hola mundo",
        "model_id": "eleven_multilingual_v2",
    }
    assert audio == FAKE_MP3_BYTES


@respx.mock
async def test_elevenlabs_uses_default_voice_id_when_call_omits_it():
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="default-voice")
    route = respx.post(url).mock(return_value=httpx.Response(200, content=FAKE_MP3_BYTES))

    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key", default_voice_id="default-voice")
    audio = await tts.synthesize("hola")

    assert route.called
    assert audio == FAKE_MP3_BYTES


@respx.mock
async def test_elevenlabs_call_voice_id_overrides_default():
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="override-voice")
    route = respx.post(url).mock(return_value=httpx.Response(200, content=FAKE_MP3_BYTES))

    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key", default_voice_id="default-voice")
    await tts.synthesize("hola", voice_id="override-voice")

    assert route.called


async def test_elevenlabs_without_any_voice_id_raises_value_error():
    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key")
    with pytest.raises(ValueError):
        await tts.synthesize("hola")


@respx.mock
async def test_elevenlabs_raises_on_http_error():
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="voice-123")
    respx.post(url).mock(return_value=httpx.Response(500, content=b"boom"))

    tts = ElevenLabsTTS(api_key="fake-elevenlabs-key")
    with pytest.raises(httpx.HTTPStatusError):
        await tts.synthesize("hola", voice_id="voice-123")
