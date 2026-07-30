"""Tests de PollyTTS con un cliente/sesión de aioboto3 fake inyectado por constructor.

No se usa red real ni `moto`: se inyecta un doble mínimo de `aioboto3.Session`
que imita la forma async-context-manager de `session.client(...)` y de
`response["AudioStream"]` (ARCHITECTURE.md §10.9).

La sección final (`allow_ambient_credentials`) prueba la segunda capa de
defensa del hallazgo "riesgo-legal-tos" (`HOTFIXES_PENDIENTES.md`): sin
`session=` inyectada NI `allow_ambient_credentials=True` explícito, el
constructor se niega a heredar la cadena de credenciales AWS ambiente del
proceso — ver también `packages/voice/tests/test_voice_byo.py` (test de
firma) y `packages/voice/tests/test_voice_tenant.py`/`apps/api/tests/
test_voice_byo.py` (que confirman que los dos resolvers multi-tenant SOLO
pasan ese flag dentro de su propio chequeo `EDECAN_LOCAL_MODE`).
"""

from __future__ import annotations

from typing import Any

import edecan_voice.polly as polly_module
import pytest
from edecan_voice.polly import PollyTTS


class _FakeAudioStream:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def __aenter__(self) -> _FakeAudioStream:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def read(self) -> bytes:
        return self._data


class _FakePollyClient:
    def __init__(self, audio_bytes: bytes) -> None:
        self._audio_bytes = audio_bytes
        self.synthesize_calls: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakePollyClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def synthesize_speech(self, **kwargs: Any) -> dict[str, Any]:
        self.synthesize_calls.append(kwargs)
        return {"AudioStream": _FakeAudioStream(self._audio_bytes)}


class _FakeSession:
    def __init__(self, client: _FakePollyClient) -> None:
        self._client = client
        self.client_calls: list[tuple[str, dict[str, Any]]] = []

    def client(self, service_name: str, **kwargs: Any) -> _FakePollyClient:
        self.client_calls.append((service_name, kwargs))
        return self._client


async def test_polly_synthesize_returns_audio_bytes_from_fake_client():
    fake_client = _FakePollyClient(audio_bytes=b"fake-polly-mp3")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(session=fake_session)
    audio = await tts.synthesize("hola mundo")

    assert audio == b"fake-polly-mp3"
    assert fake_session.client_calls[0][0] == "polly"


async def test_polly_synthesize_uses_default_voice_lupe_engine_neural_and_mp3():
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(session=fake_session)
    await tts.synthesize("hola")

    call = fake_client.synthesize_calls[0]
    assert call["Text"] == "hola"
    assert call["VoiceId"] == "Lupe"
    assert call["Engine"] == "neural"
    assert call["OutputFormat"] == "mp3"


async def test_polly_synthesize_honors_configured_voice_id():
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(voice_id="Mia", session=fake_session)
    await tts.synthesize("hola")

    assert fake_client.synthesize_calls[0]["VoiceId"] == "Mia"


async def test_polly_synthesize_per_call_voice_id_overrides_default():
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(voice_id="Lupe", session=fake_session)
    await tts.synthesize("hola", voice_id="Miguel")

    assert fake_client.synthesize_calls[0]["VoiceId"] == "Miguel"


async def test_polly_passes_region_and_endpoint_url_to_client_when_set():
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(
        session=fake_session, region_name="us-east-1", endpoint_url="http://localhost:4566"
    )
    await tts.synthesize("hola")

    service_name, kwargs = fake_session.client_calls[0]
    assert service_name == "polly"
    assert kwargs["region_name"] == "us-east-1"
    assert kwargs["endpoint_url"] == "http://localhost:4566"


async def test_polly_omits_region_and_endpoint_url_when_not_set():
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(session=fake_session)
    await tts.synthesize("hola")

    _, kwargs = fake_session.client_calls[0]
    assert kwargs == {}


# ---------------------------------------------------------------------------
# `allow_ambient_credentials` — segunda capa de defensa (hallazgo
# "riesgo-legal-tos", `HOTFIXES_PENDIENTES.md`): sin `session=` inyectada NI
# `allow_ambient_credentials=True` explícito, el constructor NUNCA hereda en
# silencio la identidad AWS ambiente del proceso.
# ---------------------------------------------------------------------------


def test_polly_sin_session_ni_allow_ambient_credentials_lanza_valueerror():
    with pytest.raises(ValueError, match="allow_ambient_credentials"):
        PollyTTS()


def test_polly_session_inyectada_evita_el_chequeo_de_allow_ambient_credentials():
    """`session=` (tests) se usa tal cual, sin pasar por el candado de
    `allow_ambient_credentials` — ver docstring del constructor."""
    fake_client = _FakePollyClient(audio_bytes=b"x")
    fake_session = _FakeSession(fake_client)

    tts = PollyTTS(session=fake_session)  # no revienta aunque allow_ambient_credentials=False

    assert tts._session is fake_session  # type: ignore[attr-defined]


def test_polly_allow_ambient_credentials_true_construye_sesion_ambiente_sin_argumentos(
    monkeypatch,
):
    """Único opt-in legítimo (`edecan_voice.registry.get_tts`, o un resolver
    multi-tenant YA DENTRO de su propio chequeo `EDECAN_LOCAL_MODE`): con
    `allow_ambient_credentials=True` y sin `session=`, construye
    `aioboto3.Session()` SIN ningún argumento — nunca una credencial
    explícita, porque no hay ninguna que pasar (es la cadena ambiente)."""
    llamadas: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class _SesionAmbienteFalsa:
        def __init__(self, *args: object, **kwargs: object) -> None:
            llamadas.append((args, kwargs))

    monkeypatch.setattr(polly_module.aioboto3, "Session", _SesionAmbienteFalsa)

    tts = PollyTTS(allow_ambient_credentials=True)

    assert llamadas == [((), {})]
    assert isinstance(tts._session, _SesionAmbienteFalsa)  # type: ignore[attr-defined]
