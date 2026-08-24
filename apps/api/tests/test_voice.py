"""`/v1/voice/transcribe` y `/v1/voice/speak` (ARCHITECTURE.md §10.12, §4, §10.9).

`test_settings` (conftest.py) no fija `VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`,
así que caen al default `"stub"` (`edecan_voice.registry`): los tests de la
primera sección corren 100% offline contra `StubSTT`/`StubTTS` — no
sobreescriben `get_vault`, así que heredan el `None` por defecto del fixture
`app` (`conftest.py`), que `_stt_para_tenant`/`_tts_para_tenant` tratan igual
que "sin vault disponible" y saltan directo al paso 2 (plataforma → stub).

La segunda sección (WP-V3-02, bring-your-own de voz por tenant, ver el
docstring de `edecan_api.routers.voice`) SÍ instala un `FakeVault` propio
(`put`+`get` en memoria, mismo patrón que `test_connectors_credentials_v2.py`/
`test_credentials_router.py`) vía `app.dependency_overrides[get_vault]` y
ejercita las tres prioridades documentadas: tenant con su propia credencial
(Deepgram/ElevenLabs reales, mockeados con `respx` — nunca red de verdad),
tenant sin nada conectado (cae a `StubSTT`/`StubTTS`, igual que antes), y un
vault que lanza (tampoco rompe la request, cae al mismo stub).
"""

from __future__ import annotations

import base64
import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import respx
from conftest import TEST_JWT_SECRET, auth_headers
from edecan_schemas import TokenBundle
from edecan_voice.base import Transcript, TTSProvider
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import edecan_api.deps as edecan_deps
import edecan_api.routers.voice as voice_module
from edecan_api.config import Settings, get_settings
from edecan_api.security import create_access_token


async def test_transcribe_without_voice_web_flag_returns_403(client) -> None:
    # plan_key desconocido -> flags_for_plan devuelve {} -> voice.web es False.
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe"
    )
    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )
    assert response.status_code == 403


async def test_transcribe_returns_stub_transcript_and_records_usage(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"text": "(transcripción de prueba)"}
    kinds = [e["kind"] for e in fake_repo.usage_events]
    assert kinds.count("voice_seconds") == 1


async def test_speak_without_voice_web_flag_returns_403(client) -> None:
    headers = auth_headers(
        user_id=uuid.uuid4(), tenant_id=uuid.uuid4(), plan_key="plan_no_existe"
    )
    response = await client.post(
        "/v1/voice/speak", json={"text": "Hola mundo"}, headers=headers
    )
    assert response.status_code == 403


def test_realtime_voice_rechaza_handshake_sin_autenticacion(app) -> None:
    with TestClient(app) as test_client:
        with test_client.websocket_connect("/v1/voice/realtime") as websocket:
            websocket.send_json({"type": "ping"})
            try:
                websocket.receive_json()
            except WebSocketDisconnect as exc:
                assert exc.code == 4401
            else:
                raise AssertionError("el transporte realtime no debe aceptar sin auth")


def test_realtime_voice_emite_audio_y_cancela_sintesis_en_vuelo(
    app, test_settings, monkeypatch
) -> None:
    class _FakeStreamingTTS(TTSProvider):
        async def synthesize(self, text, voice_id=None, fmt="mp3"):  # noqa: ANN001
            return b"audio"

        async def synthesize_stream(
            self, text, *, voice_id=None, model_id, mime  # noqa: ANN001
        ):
            yield b"chunk-1"
            await asyncio.sleep(3600)

    import asyncio

    @asynccontextmanager
    async def fake_session(_tenant_id):  # noqa: ANN001
        yield object()

    async def no_quota(*_args, **_kwargs):  # noqa: ANN001
        return None

    async def no_usage(*_args, **_kwargs):  # noqa: ANN001
        return None

    monkeypatch.setattr(voice_module, "get_settings", lambda: test_settings)
    monkeypatch.setattr(voice_module, "get_session", fake_session)
    monkeypatch.setattr(voice_module, "build_key_provider", lambda _settings: object())
    async def fake_tts(*_args):  # noqa: ANN001
        return _FakeStreamingTTS()

    async def fake_stt(*_args):  # noqa: ANN001
        class _FakeSTT:
            async def transcribe(self, _audio, _mime):  # noqa: ANN001
                return Transcript(text="texto transcrito", language="es")

        return _FakeSTT()

    import edecan_api.voice_turn_service as voice_service

    async def fake_agent_turn(*_args, **_kwargs):  # noqa: ANN001
        return voice_service.VoiceAgentTurnResult(
            text="respuesta del agente",
            events=[{"type": "text_delta", "text": "respuesta del agente"}],
        )

    monkeypatch.setattr(voice_module, "_tts_para_tenant", fake_tts)
    monkeypatch.setattr(voice_module, "_stt_para_tenant", fake_stt)
    monkeypatch.setattr(voice_service, "execute_voice_text_turn", fake_agent_turn)
    monkeypatch.setattr(voice_module, "_check_voice_quota", no_quota)
    monkeypatch.setattr(voice_module.SqlRepo, "add_usage_event", no_usage)

    token = create_access_token(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        plan_key="hosted_basic",
        secret=test_settings.JWT_SECRET,
    )
    with TestClient(app) as test_client:
        with test_client.websocket_connect("/v1/voice/realtime") as websocket:
            conversation_id = uuid.uuid4()
            websocket.send_json(
                {
                    "type": "authenticate",
                    "token": token,
                    "conversation_id": str(conversation_id),
                }
            )
            ready = websocket.receive_json()
            assert ready["type"] == "ready"

            websocket.send_json(
                {
                    "type": "image",
                    "mime": "image/jpeg",
                    "data": base64.b64encode(b"jpeg-bytes").decode("ascii"),
                }
            )
            assert websocket.receive_json() == {
                "type": "image.accepted",
                "mime": "image/jpeg",
            }

            websocket.send_json(
                {
                    "type": "audio",
                    "mime": "audio/wav",
                    "data": base64.b64encode(b"audio-input").decode("ascii"),
                }
            )
            accepted = websocket.receive_json()
            assert accepted["type"] == "audio.accepted"
            websocket.send_json({"type": "commit"})
            transcript = websocket.receive_json()
            assert transcript["type"] == "transcript"
            assert transcript["text"] == "texto transcrito"

            agent_event = websocket.receive_json()
            assert agent_event["type"] == "agent_event"
            assert agent_event["event"]["text"] == "respuesta del agente"
            agent_audio = websocket.receive_json()
            assert agent_audio["type"] == "audio"
            websocket.send_json({"type": "interrupt"})
            assert websocket.receive_json()["type"] == "interrupted"

            websocket.send_json({"type": "speak", "text": "Hola"})
            assert websocket.receive_json()["type"] == "speaking"
            audio = websocket.receive_json()
            assert audio["type"] == "audio"
            assert audio["data"]

            websocket.send_json({"type": "interrupt"})
            interrupted = websocket.receive_json()
            assert interrupted == {"type": "interrupted", "state": "interrupted"}


async def test_speak_returns_stub_wav_audio_and_records_usage(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak", json={"text": "Hola, ¿cómo estás?"}, headers=headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"  # cabecera WAV
    kinds = [e["kind"] for e in fake_repo.usage_events]
    assert kinds.count("voice_seconds") == 1


# ---------------------------------------------------------------------------
# Contrato de veracidad (`edecan_core.veracidad`) — `/speak`/`/transcribe`
# devuelven bytes/JSON de forma directa (no pasan por `Agent.run_turn`), así
# que el único canal para avisar "esto es simulado" es una cabecera HTTP —
# ver `apps/api/edecan_api/routers/voice.py::_set_headers_de_fidelidad`.
# ---------------------------------------------------------------------------


async def test_speak_stub_lleva_cabecera_de_fidelidad_demo_con_motivo(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak", json={"text": "Hola"}, headers=headers
    )

    assert response.status_code == 200
    assert response.headers["x-edecan-fidelidad"] == "demo"
    assert "ELEVENLABS_API_KEY" in response.headers["x-edecan-motivo"]


async def test_transcribe_stub_lleva_cabecera_de_fidelidad_demo(client, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200
    # El body NO cambia (mismos clientes existentes lo siguen parseando igual):
    assert response.json() == {"text": "(transcripción de prueba)"}
    assert response.headers["x-edecan-fidelidad"] == "demo"
    assert "DEEPGRAM_API_KEY" in response.headers["x-edecan-motivo"]


# ---------------------------------------------------------------------------
# `_strip_markdown_for_speech` -- sin esto, el TTS lee "asterisco asterisco
# texto asterisco asterisco" en vez del énfasis que representa.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("markdown", "esperado"),
    [
        ("Hola **mundo**, ¿cómo estás?", "Hola mundo, ¿cómo estás?"),
        ("Esto es *cursiva* y esto __negrita__.", "Esto es cursiva y esto negrita."),
        ("# Título\n\nTexto normal", "Título\n\nTexto normal"),
        ("- item uno\n- item dos", "item uno\nitem dos"),
        ("Mira [este link](https://example.com) por favor", "Mira este link por favor"),
        ("```python\ncode\n```\nTexto", "Texto"),
        ("Código `inline` aquí", "Código inline aquí"),
        ("~~tachado~~", "tachado"),
    ],
)
def test_strip_markdown_for_speech(markdown, esperado) -> None:
    assert voice_module._strip_markdown_for_speech(markdown).strip() == esperado.strip()


async def test_speak_le_quita_el_markdown_antes_de_mandarlo_al_tts_stub(
    client, fake_repo
) -> None:
    """El WAV del stub no deja inspeccionar el texto sintetizado directamente,
    pero `_estimate_seconds_from_text` (que sí corre sobre el texto ya
    limpio) es un proxy indirecto -- el test de integración real, con el
    texto capturado tal cual llega al proveedor, es
    `test_speak_usa_elevenlabs_del_tenant_cuando_esta_configurado` (abajo)."""
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak", json={"text": "**Hola** _mundo_"}, headers=headers
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Bring-your-own de voz por tenant (WP-V3-02) — ver docstring del módulo.
# ---------------------------------------------------------------------------


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault`: `put`+`get` en memoria (mismo
    patrón que `test_credentials_router.py`, duplicado a propósito — cada
    archivo de test trae su propio doble mínimo, ver `test_connectors.py`)."""

    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


class RaisingVault:
    """Vault que revienta al leer — para probar que `_stt_para_tenant`/
    `_tts_para_tenant` degradan a plataforma/stub en vez de tumbar la
    request (ver sus docstrings, "nunca lanza")."""

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        raise RuntimeError("vault caído de verdad")


async def _conectar_voz_tenant(
    app: Any,
    fake_repo: Any,
    fake_vault: FakeVault,
    *,
    tenant_id: uuid.UUID,
    connector_key: str,
    config: dict[str, Any],
) -> None:
    """Instala `fake_vault` como `get_vault` y le "conecta" al tenant una
    credencial de voz (STT o TTS), mismo shape que guarda
    `PUT /v1/credentials/voice/{stt,tts}` (`routers/credentials.py`)."""
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=connector_key,
        external_account_id=connector_key,
        display_name=connector_key,
        scopes=[],
    )
    await fake_vault.put(
        tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config), token_type="config"),
    )


@respx.mock
async def test_transcribe_usa_deepgram_del_tenant_cuando_esta_configurado(
    client, app, fake_repo
) -> None:
    respx.post("https://api.deepgram.com/v1/listen").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": {
                    "channels": [
                        {
                            "alternatives": [
                                {
                                    "transcript": "hola desde deepgram del tenant",
                                    "confidence": 0.99,
                                }
                            ]
                        }
                    ]
                }
            },
        )
    )
    fake_vault = FakeVault()
    tenant_id = uuid.uuid4()
    await _conectar_voz_tenant(
        app,
        fake_repo,
        fake_vault,
        tenant_id=tenant_id,
        connector_key="voice_stt",
        config={"provider": "deepgram", "api_key": "dg_tenant_key"},
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"text": "hola desde deepgram del tenant"}
    kinds = [e["kind"] for e in fake_repo.usage_events]
    assert kinds.count("voice_seconds") == 1


@respx.mock
async def test_transcribe_tenant_sin_config_de_voz_cae_a_stub(client, app, fake_repo) -> None:
    """Vault disponible (a diferencia de los tests de la primera sección,
    donde `get_vault` es `None`) pero el tenant nunca conectó nada — sin
    rutas `respx` registradas: si el código intentara pegarle a Deepgram de
    todos modos, este test fallaría en vez de colarse a la red real."""
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"text": "(transcripción de prueba)"}


@respx.mock
async def test_transcribe_vault_roto_cae_a_stub_sin_romper(client, app, fake_repo) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: RaisingVault()
    tenant_id = uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="voice_stt",
        external_account_id="voice_stt",
        display_name="voice_stt",
        scopes=[],
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"x", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200  # no lanza: degrada a stub
    assert response.json() == {"text": "(transcripción de prueba)"}


@respx.mock
async def test_speak_usa_elevenlabs_del_tenant_cuando_esta_configurado(
    client, app, fake_repo
) -> None:
    respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-tenant").mock(
        return_value=httpx.Response(200, content=b"FAKE-MP3-BYTES-ELEVENLABS")
    )
    fake_vault = FakeVault()
    tenant_id = uuid.uuid4()
    await _conectar_voz_tenant(
        app,
        fake_repo,
        fake_vault,
        tenant_id=tenant_id,
        connector_key="voice_tts",
        config={"provider": "elevenlabs", "api_key": "el_tenant_key", "voice_id": "voz-tenant"},
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak", json={"text": "Hola desde el tenant"}, headers=headers
    )

    assert response.status_code == 200
    # ElevenLabsTTS (a diferencia de StubTTS) siempre produce mp3 — ver
    # docstring de `speak` en `routers/voice.py`.
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"FAKE-MP3-BYTES-ELEVENLABS"
    kinds = [e["kind"] for e in fake_repo.usage_events]
    assert kinds.count("voice_seconds") == 1
    assert response.headers["x-edecan-fidelidad"] == "live"
    assert "x-edecan-motivo" not in response.headers


@respx.mock
async def test_speak_manda_a_elevenlabs_el_texto_ya_sin_markdown(client, app, fake_repo) -> None:
    """Regresión real: antes de `_strip_markdown_for_speech`, ElevenLabs
    recibía el Markdown crudo del chat (`**negrita**`) y lo leía en voz alta
    literalmente ("asterisco asterisco...")."""
    ruta = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-tenant").mock(
        return_value=httpx.Response(200, content=b"FAKE-MP3-BYTES")
    )
    fake_vault = FakeVault()
    tenant_id = uuid.uuid4()
    await _conectar_voz_tenant(
        app,
        fake_repo,
        fake_vault,
        tenant_id=tenant_id,
        connector_key="voice_tts",
        config={"provider": "elevenlabs", "api_key": "el_tenant_key", "voice_id": "voz-tenant"},
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak",
        json={"text": "Esto es **muy** importante: revisa el [reporte](https://x.com)."},
        headers=headers,
    )

    assert response.status_code == 200
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado["text"] == "Esto es muy importante: revisa el reporte."


@respx.mock
async def test_speak_usa_expresion_v3_configurada_desde_el_chat(client, app, fake_repo) -> None:
    """La app movil usa este endpoint, por lo que la expresion debe sobrevivir
    al resolver HTTP y no existir solo en las tools internas del agente."""

    ruta = respx.post("https://api.elevenlabs.io/v1/text-to-speech/voz-tenant").mock(
        return_value=httpx.Response(200, content=b"FAKE-MP3-BYTES")
    )
    fake_vault = FakeVault()
    tenant_id = uuid.uuid4()
    await _conectar_voz_tenant(
        app,
        fake_repo,
        fake_vault,
        tenant_id=tenant_id,
        connector_key="voice_tts",
        config={
            "provider": "elevenlabs",
            "api_key": "el_tenant_key",
            "voice_id": "voz-tenant",
            "model_id": "eleven_v3",
            "expressive": True,
        },
    )
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak",
        json={"text": "**Listo**, quedó configurado."},
        headers=headers,
    )

    assert response.status_code == 200
    enviado = json.loads(ruta.calls.last.request.content)
    assert enviado == {
        "text": "[calmly] Listo, quedó configurado.",
        "model_id": "eleven_v3",
    }


@respx.mock
async def test_speak_tenant_sin_config_de_voz_cae_a_stub(client, app, fake_repo) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/speak", json={"text": "Hola"}, headers=headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"


# ---------------------------------------------------------------------------
# Regresión (riesgo-legal-tos, ver docstring del módulo): un tenant sin
# credencial propia NUNCA debe reutilizar una credencial real de voz
# configurada a nivel de PLATAFORMA (`VOICE_STT_PROVIDER`/`DEEPGRAM_API_KEY`,
# `VOICE_TTS_PROVIDER`/`ELEVENLABS_API_KEY` de `Settings`) — a diferencia de
# los tests "cae a stub" de arriba (que corren con la plataforma SIN ninguna
# credencial real configurada, `test_settings` de `conftest.py`), estos dos
# fijan una `Settings` con un proveedor de voz real + api_key "de plataforma"
# a propósito, para probar que igual cae a stub. `@respx.mock` sin ninguna
# ruta registrada: si el código intentara pegarle a Deepgram/ElevenLabs con
# esa key de plataforma, el test fallaría en vez de colarse a la red real.
# ---------------------------------------------------------------------------


def _settings_con_credencial_de_voz_de_plataforma() -> Settings:
    return Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY="dg_credencial_compartida_de_plataforma_NUNCA_SE_USA",
        VOICE_TTS_PROVIDER="elevenlabs",
        ELEVENLABS_API_KEY="el_credencial_compartida_de_plataforma_NUNCA_SE_USA",
    )


@respx.mock
async def test_transcribe_tenant_sin_config_no_reusa_credencial_de_plataforma(
    client, app, fake_repo
) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    app.dependency_overrides[get_settings] = _settings_con_credencial_de_voz_de_plataforma
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post(
        "/v1/voice/transcribe",
        files={"audio": ("nota.webm", b"contenido-falso-de-audio", "audio/webm")},
        headers=headers,
    )

    assert response.status_code == 200
    assert response.json() == {"text": "(transcripción de prueba)"}


@respx.mock
async def test_speak_tenant_sin_config_no_reusa_credencial_de_plataforma(
    client, app, fake_repo
) -> None:
    app.dependency_overrides[edecan_deps.get_vault] = lambda: FakeVault()
    app.dependency_overrides[get_settings] = _settings_con_credencial_de_voz_de_plataforma
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id, plan_key="hosted_basic")

    response = await client.post("/v1/voice/speak", json={"text": "Hola"}, headers=headers)

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"
