"""Regresión anti-fuga dedicada de `_stt_para_tenant`/`_tts_para_tenant`
(`edecan_api.routers.voice`) — Barrido de seguridad v5.

`test_voice.py` ya cubre el comportamiento end-to-end de `/v1/voice/transcribe`
`/speak` (incluida la sección "no reusa credencial de plataforma" al final de
ese archivo). Este archivo agrega dos cosas que ese no tiene:

1. Tests UNITARIOS directos de `_stt_para_tenant`/`_tts_para_tenant` (sin pasar
   por HTTP), con nombre estilo `test_X_sin_credencial_no_filtra_la_de_plataforma`
   pedido por el paquete de trabajo — más rápidos y más precisos sobre el TIPO
   exacto de proveedor resuelto.
2. Una inspección de la request HTTP REAL (capturada con `respx`) cuando el
   tenant SÍ tiene su propia credencial conectada Y, a la vez, `Settings` trae
   un valor centinela "de plataforma" — probando con el header/URL exactos
   que la petición que sale hacia Deepgram/ElevenLabs jamás lleva el
   centinela, solo la credencial del tenant. `test_voice.py` ya prueba que la
   RESPUESTA es la esperada; acá se prueba directamente el REQUEST saliente.
"""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import respx
from edecan_schemas import TokenBundle
from edecan_voice.deepgram import DEEPGRAM_LISTEN_URL, DeepgramSTT
from edecan_voice.elevenlabs import ELEVENLABS_TTS_URL_TEMPLATE, ElevenLabsTTS
from edecan_voice.polly import PollyTTS
from edecan_voice.stubs import StubSTT, StubTTS

from edecan_api.config import Settings
from edecan_api.deps import VOICE_STT_CONNECTOR_KEY, VOICE_TTS_CONNECTOR_KEY
from edecan_api.routers.voice import _stt_para_tenant, _tts_para_tenant

_SENTINEL = "FUGA_DE_PLATAFORMA_NO_DEBE_APARECER"


def _settings_con_sentinela(**overrides: Any) -> Settings:
    return Settings(
        JWT_SECRET="test-jwt-secret-solo-para-tests-32-bytes-o-mas",
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        VOICE_STT_PROVIDER="deepgram",
        DEEPGRAM_API_KEY=_SENTINEL,
        VOICE_TTS_PROVIDER="elevenlabs",
        ELEVENLABS_API_KEY=_SENTINEL,
        **overrides,
    )


class _FakeRepoConAcccount:
    """Doble mínimo de `Repo` con UNA `connector_account` ya "conectada"
    (mismo shape que `list_connector_accounts`)."""

    def __init__(self, connector_key: str, account_id: uuid.UUID) -> None:
        self._connector_key = connector_key
        self._account_id = account_id

    async def list_connector_accounts(self, *, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return [{"id": self._account_id, "connector_key": self._connector_key}]


class _FakeRepoVacio:
    """El tenant nunca conectó nada."""

    async def list_connector_accounts(self, *, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
        return []


class _FakeVault:
    def __init__(self, bundle: TokenBundle) -> None:
        self._bundle = bundle
        self.llamadas: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        self.llamadas.append((tenant_id, account_id))
        return self._bundle


# ---------------------------------------------------------------------------
# 1. Unitarios directos de _stt_para_tenant/_tts_para_tenant.
# ---------------------------------------------------------------------------


async def test_stt_para_tenant_sin_credencial_no_filtra_la_de_plataforma():
    """Sin cuenta conectada (repo vacío) — SIEMPRE StubSTT, aunque `settings`
    traiga un proveedor Deepgram real + api_key (el centinela)."""
    proveedor = await _stt_para_tenant(
        vault=None,  # ni siquiera hace falta un vault: repo vacío ya basta
        repo=_FakeRepoVacio(),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )
    assert isinstance(proveedor, StubSTT)


async def test_stt_para_tenant_con_credencial_propia_nunca_usa_el_centinela():
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps({"provider": "deepgram", "api_key": "clave-del-tenant"}),
        token_type="config",
    )
    proveedor = await _stt_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_STT_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )
    assert isinstance(proveedor, DeepgramSTT)
    assert proveedor._api_key == "clave-del-tenant"  # type: ignore[attr-defined]
    assert proveedor._api_key != _SENTINEL  # type: ignore[attr-defined]


async def test_tts_para_tenant_sin_credencial_no_filtra_la_de_plataforma():
    proveedor = await _tts_para_tenant(
        vault=None,
        repo=_FakeRepoVacio(),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )
    assert isinstance(proveedor, StubTTS)


async def test_tts_para_tenant_con_credencial_propia_nunca_usa_el_centinela():
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-del-tenant", "voice_id": "voz-1"}
        ),
        token_type="config",
    )
    proveedor = await _tts_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_TTS_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )
    assert isinstance(proveedor, ElevenLabsTTS)
    assert proveedor._api_key == "clave-del-tenant"  # type: ignore[attr-defined]
    assert proveedor._api_key != _SENTINEL  # type: ignore[attr-defined]


async def test_tts_para_tenant_polly_del_tenant_usa_region_de_plataforma_pero_sin_secreto():
    """Polly no tiene campo de API key (`docs/credenciales.md`): `region_name`/
    `endpoint_url` SÍ vienen de `settings` (infra no-secreta, legítimo — ver
    `ARCHITECTURE.md` §0 y el docstring de `_tts_para_tenant`), nunca una
    credencial secreta de plataforma. Requiere `EDECAN_LOCAL_MODE=True`: sin
    eso, Polly compartiría la identidad AWS del PROCESO entre tenants
    (hallazgo "riesgo-legal-tos", `HOTFIXES_PENDIENTES.md`; ver el test
    `test_tts_para_tenant_polly_sin_local_mode_cae_a_stub` de abajo)."""
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps({"provider": "polly", "voice": "Lupe"}), token_type="config"
    )
    proveedor = await _tts_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_TTS_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(AWS_REGION="us-east-1", EDECAN_LOCAL_MODE=True),
    )
    assert isinstance(proveedor, PollyTTS)
    # Polly no expone ningún atributo "_api_key"/"_access_key" — no hay nada
    # secreto que pudiera haberse filtrado desde `settings` en primer lugar.
    assert not hasattr(proveedor, "_api_key")


async def test_tts_para_tenant_polly_sin_local_mode_cae_a_stub():
    """Sin `EDECAN_LOCAL_MODE=True` (default, hosted compartido) — el tenant
    SÍ tiene una fila `polly` guardada, pero igual cae a `StubTTS`: la
    identidad AWS del proceso no es "la del tenant" fuera de self-host de un
    único tenant (hallazgo "riesgo-legal-tos", `HOTFIXES_PENDIENTES.md`)."""
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps({"provider": "polly", "voice": "Lupe"}), token_type="config"
    )
    proveedor = await _tts_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_TTS_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),  # EDECAN_LOCAL_MODE por defecto es False
    )
    assert isinstance(proveedor, StubTTS)


# ---------------------------------------------------------------------------
# 2. Request HTTP real (respx): tenant conectado + centinela de plataforma
#    presente a la vez -> el header saliente es SIEMPRE el del tenant.
# ---------------------------------------------------------------------------


@respx.mock
async def test_stt_para_tenant_request_real_solo_lleva_la_clave_del_tenant():
    route = respx.post(DEEPGRAM_LISTEN_URL).mock(
        return_value=httpx.Response(
            200,
            json={"results": {"channels": [{"alternatives": [{"transcript": "ok"}]}]}},
        )
    )
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps({"provider": "deepgram", "api_key": "clave-real-del-tenant"}),
        token_type="config",
    )
    proveedor = await _stt_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_STT_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )

    await proveedor.transcribe(b"audio-falso", "audio/webm")

    assert route.called
    auth_header = route.calls.last.request.headers["Authorization"]
    assert auth_header == "Token clave-real-del-tenant"
    assert _SENTINEL not in auth_header


@respx.mock
async def test_tts_para_tenant_request_real_solo_lleva_la_clave_del_tenant():
    url = ELEVENLABS_TTS_URL_TEMPLATE.format(voice_id="voz-1")
    route = respx.post(url).mock(return_value=httpx.Response(200, content=b"mp3-real"))
    account_id = uuid.uuid4()
    bundle = TokenBundle(
        access_token=json.dumps(
            {"provider": "elevenlabs", "api_key": "clave-real-del-tenant", "voice_id": "voz-1"}
        ),
        token_type="config",
    )
    proveedor = await _tts_para_tenant(
        vault=_FakeVault(bundle),
        repo=_FakeRepoConAcccount(VOICE_TTS_CONNECTOR_KEY, account_id),
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )

    await proveedor.synthesize("hola")

    assert route.called
    header = route.calls.last.request.headers["xi-api-key"]
    assert header == "clave-real-del-tenant"
    assert _SENTINEL not in header


# ---------------------------------------------------------------------------
# 3. vault=None con settings sentinela (ver docstring de _stt_para_tenant:
#    "vault puede ser None") — mismo resultado que "repo vacío".
# ---------------------------------------------------------------------------


async def test_stt_para_tenant_sin_vault_ni_repo_utilizable_cae_a_stub():
    proveedor = await _stt_para_tenant(
        vault=None,
        repo=SimpleNamespace(),  # nunca se llega a usar: vault=None corta antes
        tenant_id=uuid.uuid4(),
        settings=_settings_con_sentinela(),
    )
    assert isinstance(proveedor, StubSTT)
