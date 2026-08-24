"""Llamada telefónica en tiempo real (Media Streams), sin Pipecat.

Twilio manda μ-law 8 kHz por WebSocket. Aquí: Deepgram Live → LLM → ElevenLabs
μ-law, con transcripción en memoria, susurros y barge-in. El router de phone
persiste turnos y cierra el resumen al colgar.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import httpx
from edecan_core.speech_tags import SPEECH_TAG_RE

from edecan_voice.expression import plain_text_for_speech

logger = logging.getLogger(__name__)

# Eco de la propia voz en el inbound: si cortamos al primer frame "con energía",
# el caller oye "Hol" y se acaba. Hay que ignorar el arranque y exigir voz sostenida.
_BARGE_IN_IGNORE_S = 0.7
_BARGE_IN_FRAMES = 10
_BARGE_IN_ENERGY = 18.0

DEEPGRAM_LIVE_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?encoding=mulaw&sample_rate=8000&channels=1&model=nova-2"
    "&language=es&punctuate=true&interim_results=true"
    "&endpointing=300&utterance_end_ms=1200"
)
ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
FLASH_MODEL = "eleven_flash_v2_5"
_MULAW_FRAME = 160  # 20 ms a 8 kHz
_LIVE: dict[str, LiveCall] = {}


@dataclass
class TranscriptTurn:
    quien: str
    texto: str
    ts: float = field(default_factory=time.time)


@dataclass
class LiveCall:
    call_id: str
    provider_sid: str = ""
    numero: str = ""
    direccion: str = "out"
    status: str = "en_curso"
    iniciada: float = field(default_factory=time.time)
    transcript: list[TranscriptTurn] = field(default_factory=list)
    whispers: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "activa": True,
            "call_id": self.call_id,
            "sid": self.provider_sid,
            "numero": self.numero,
            "direccion": self.direccion,
            "status": self.status,
            "iniciada": self.iniciada,
            "transcript": [
                {"quien": turn.quien, "texto": turn.texto, "ts": turn.ts}
                for turn in self.transcript
            ],
        }


def live_call(call_id: str) -> LiveCall | None:
    return _LIVE.get(str(call_id))


def active_live_calls() -> list[LiveCall]:
    horizon = time.time() - 2 * 60 * 60
    return [call for call in _LIVE.values() if call.iniciada >= horizon]


def register_live_call(call: LiveCall) -> LiveCall:
    _LIVE[str(call.call_id)] = call
    return call


def drop_live_call(call_id: str) -> None:
    _LIVE.pop(str(call_id), None)


def enqueue_whisper(call_id: str, texto: str) -> bool:
    live = _LIVE.get(str(call_id))
    if live is None:
        return False
    limpio = " ".join(texto.split()).strip()
    if not limpio:
        return False
    live.whispers.append(limpio)
    return True


def pop_whispers(call_id: str) -> list[str]:
    live = _LIVE.get(str(call_id))
    if live is None or not live.whispers:
        return []
    pending = list(live.whispers)
    live.whispers.clear()
    return pending


def https_to_wss(base_url: str) -> str:
    value = (base_url or "").strip().rstrip("/")
    if value.startswith("https://"):
        return "wss://" + value[len("https://") :]
    if value.startswith("http://"):
        return "ws://" + value[len("http://") :]
    return value


def twilio_media_message(stream_sid: str, mulaw: bytes) -> dict[str, Any]:
    return {
        "event": "media",
        "streamSid": stream_sid,
        "media": {"payload": base64.b64encode(mulaw).decode("ascii")},
    }


def twilio_clear_message(stream_sid: str) -> dict[str, Any]:
    return {"event": "clear", "streamSid": stream_sid}


def chunk_mulaw(audio: bytes, frame: int = _MULAW_FRAME) -> list[bytes]:
    if not audio:
        return []
    return [audio[i : i + frame] for i in range(0, len(audio), frame)]


async def synthesize_ulaw_8000(
    *,
    api_key: str,
    voice_id: str,
    text: str,
    model_id: str = FLASH_MODEL,
) -> bytes:
    """ElevenLabs en μ-law 8 kHz, el formato nativo de Twilio Media Streams."""
    spoken = SPEECH_TAG_RE.sub(" ", plain_text_for_speech(text))
    spoken = " ".join(spoken.split()).strip()
    if not spoken:
        return b""
    url = ELEVENLABS_TTS_URL.format(voice_id=voice_id)
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(
            url,
            params={"output_format": "ulaw_8000"},
            headers={"xi-api-key": api_key},
            json={"text": spoken, "model_id": model_id},
        )
        response.raise_for_status()
        return response.content


class DeepgramLive:
    """Cliente mínimo del WebSocket live de Deepgram para μ-law 8 kHz."""

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key
        self._ws: Any = None

    async def connect(self) -> None:
        try:
            from websockets.asyncio.client import connect
        except ImportError:  # pragma: no cover - uvicorn trae websockets
            from websockets.client import connect  # type: ignore[no-redef]
        headers = {"Authorization": f"Token {self._api_key}"}
        try:
            self._ws = await connect(DEEPGRAM_LIVE_URL, additional_headers=headers)
        except TypeError:
            self._ws = await connect(DEEPGRAM_LIVE_URL, extra_headers=headers)

    async def send_mulaw(self, payload: bytes) -> None:
        if self._ws is None or not payload:
            return
        await self._ws.send(payload)

    async def finish(self) -> None:
        if self._ws is None:
            return
        try:
            await self._ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass
        try:
            await self._ws.close()
        except Exception:
            pass
        self._ws = None

    async def utterances(self) -> Any:
        if self._ws is None:
            return
        async for raw in self._ws:
            if isinstance(raw, bytes):
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            alt = ((data.get("channel") or {}).get("alternatives") or [{}])[0]
            text = str(alt.get("transcript") or "").strip()
            if text and data.get("is_final"):
                yield text


ReplyFn = Callable[[str], Awaitable[str]]
SpeakFn = Callable[[str], Awaitable[bytes]]
PersistFn = Callable[[str, str], Awaitable[None]]


async def pump_twilio_audio(
    websocket: Any,
    *,
    stream_sid: str,
    deepgram: DeepgramLive,
    reply: ReplyFn,
    speak: SpeakFn,
    persist: PersistFn,
    live: LiveCall,
    opening: str,
) -> None:
    """Loop principal: oye, contesta, habla, admite interrupciones."""

    playing = asyncio.Event()
    play_task: asyncio.Task[None] | None = None
    play_started = 0.0
    voice_run = 0

    async def _play(audio: bytes) -> None:
        nonlocal play_started, voice_run
        playing.set()
        play_started = time.monotonic()
        voice_run = 0
        try:
            for frame in chunk_mulaw(audio):
                if not playing.is_set():
                    return
                await websocket.send_json(twilio_media_message(stream_sid, frame))
                await asyncio.sleep(0.02)
        finally:
            playing.clear()
            voice_run = 0

    async def _speak(text: str, quien: str) -> None:
        nonlocal play_task
        limpio = " ".join(text.split()).strip()
        if not limpio:
            return
        live.transcript.append(TranscriptTurn(quien=quien, texto=limpio))
        await persist(quien, limpio)
        audio = await speak(limpio)
        if play_task and not play_task.done():
            playing.clear()
            play_task.cancel()
        play_task = asyncio.create_task(_play(audio))

    async def _from_twilio() -> None:
        nonlocal voice_run
        while True:
            message = await websocket.receive_text()
            data = json.loads(message)
            event = data.get("event")
            if event == "media":
                payload = base64.b64decode((data.get("media") or {}).get("payload") or "")
                if payload:
                    if playing.is_set() and (
                        time.monotonic() - play_started
                    ) >= _BARGE_IN_IGNORE_S:
                        if _parece_voz(payload, umbral=_BARGE_IN_ENERGY):
                            voice_run += 1
                            if voice_run >= _BARGE_IN_FRAMES:
                                playing.clear()
                                voice_run = 0
                                await websocket.send_json(twilio_clear_message(stream_sid))
                        else:
                            voice_run = 0
                    await deepgram.send_mulaw(payload)
            elif event in {"stop", "closed"}:
                return

    async def _from_deepgram() -> None:
        async for text in deepgram.utterances():
            live.transcript.append(TranscriptTurn(quien="cliente", texto=text))
            await persist("cliente", text)
            extras = pop_whispers(live.call_id)
            speech = text
            if extras:
                speech = (
                    text
                    + "\n\n[NOTA URGENTE DEL SEÑOR — incorpórala con naturalidad "
                    "en tu respuesta]: "
                    + " | ".join(extras)
                )
            answer = await reply(speech)
            await _speak(answer, "agente")

    twilio_task = asyncio.create_task(_from_twilio())
    stt_task = asyncio.create_task(_from_deepgram())
    if opening.strip():
        try:
            await _speak(opening, "agente")
        except Exception:
            logger.warning("phone_opening_failed call_id=%s", live.call_id, exc_info=True)
    try:
        await twilio_task
    finally:
        stt_task.cancel()
        if play_task:
            play_task.cancel()
        await deepgram.finish()
        live.status = "finalizada"


def _parece_voz(mulaw: bytes, *, umbral: float = _BARGE_IN_ENERGY) -> bool:
    """True si el frame no es silencio (μ-law 0xFF). Evita cortar el TTS por ruido."""
    if len(mulaw) < 40:
        return False
    desviacion = sum(abs(byte - 0xFF) for byte in mulaw) / len(mulaw)
    return desviacion > umbral


def parse_stream_start(payload: dict[str, Any]) -> dict[str, str]:
    start = payload.get("start") or {}
    params = start.get("customParameters") or {}
    return {
        "stream_sid": str(start.get("streamSid") or payload.get("streamSid") or ""),
        "call_sid": str(start.get("callSid") or ""),
        "call_id": str(params.get("call_id") or ""),
        "from": str(params.get("from") or params.get("from_number") or ""),
        "inbound": "1" if str(params.get("inbound") or "") in {"1", "true", "inbound"} else "",
    }


def as_call_uuid(value: str) -> UUID | None:
    try:
        return UUID(str(value))
    except (ValueError, TypeError):
        return None
