"""Tests de DeepgramSTT contra respuestas HTTP simuladas con respx (ARCHITECTURE.md §10.9)."""

from __future__ import annotations

import httpx
import respx
from edecan_voice.deepgram import DEEPGRAM_LISTEN_URL, DeepgramSTT

DEEPGRAM_SUCCESS_BODY = {
    "results": {
        "channels": [
            {
                "alternatives": [
                    {"transcript": "hola mundo", "confidence": 0.87},
                ]
            }
        ]
    }
}


@respx.mock
async def test_deepgram_transcribe_sends_expected_request():
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(200, json=DEEPGRAM_SUCCESS_BODY)
    )

    stt = DeepgramSTT(api_key="fake-deepgram-key")
    await stt.transcribe(b"RIFF-fake-audio-bytes", mime="audio/wav", language="es")

    assert route.called
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Token fake-deepgram-key"
    assert request.headers["Content-Type"] == "audio/wav"
    assert request.url.params["model"] == "nova-2"
    assert request.url.params["smart_format"] == "true"
    assert request.url.params["language"] == "es"
    assert request.content == b"RIFF-fake-audio-bytes"


@respx.mock
async def test_deepgram_transcribe_parses_transcript_and_confidence():
    respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(200, json=DEEPGRAM_SUCCESS_BODY)
    )

    stt = DeepgramSTT(api_key="fake-deepgram-key")
    transcript = await stt.transcribe(b"audio", mime="audio/wav", language="es")

    assert transcript.text == "hola mundo"
    assert transcript.confidence == 0.87
    assert transcript.language == "es"


@respx.mock
async def test_deepgram_transcribe_defaults_language_to_spanish():
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(200, json=DEEPGRAM_SUCCESS_BODY)
    )

    stt = DeepgramSTT(api_key="fake-deepgram-key")
    transcript = await stt.transcribe(b"audio", mime="audio/wav")

    assert route.calls.last.request.url.params["language"] == "es"
    assert transcript.language == "es"


@respx.mock
async def test_deepgram_transcribe_raises_on_http_error():
    respx.post(DEEPGRAM_LISTEN_URL).mock(return_value=httpx.Response(401, json={"err": "auth"}))

    stt = DeepgramSTT(api_key="bad-key")
    try:
        await stt.transcribe(b"audio", mime="audio/wav")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 401
    else:
        raise AssertionError("se esperaba httpx.HTTPStatusError")
