"""Speech Engine — voz gestionada por ElevenLabs (`docs/speech-engine.md`).

Cobertura con dobles deterministas del proveedor (NUNCA red real de
ElevenLabs, NUNCA la key de nadie): preferencias, sesiones, callback WSS,
claims anti-replay, aislamiento multi-tenant y ruteo rápido/delegado.

El JWT del proveedor se ACUÑA en los tests con la MISMA firma que verifica
`verify_speech_engine_jwt` del SDK oficial (HS256 con SHA256(api_key)) — se
usa una key de prueba local, jamás una real.

Lo que esta suite NO prueba: audio real, WebRTC real, interrupciones
acústicas. Eso es verificación de hardware/proveedor del operador, no un test
unitario.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import auth_headers
from edecan_schemas import TokenBundle
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import edecan_api.deps as edecan_deps
import edecan_api.routers.speech_engine as speech_engine_module

_TENANT_KEY = "clave-de-prueba-del-tenant-para-speech-engine"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _provider_jwt(api_key: str, *, exp_delta: int = 600) -> str:
    """JWT HS256 válido estilo ElevenLabs Speech Engine (issuer/sub oficiales)."""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url(
        json.dumps(
            {
                "iss": "https://api.elevenlabs.io/convai/speech-engine",
                "sub": "convai_speech_engine_upstream",
                "iat": now,
                "exp": now + exp_delta,
            }
        ).encode()
    )
    secret = hashlib.sha256(api_key.encode()).digest()
    signature = hmac.new(secret, f"{header}.{payload}".encode(), hashlib.sha256).digest()
    return f"{header}.{payload}.{_b64url(signature)}"


class _FakeVault:
    """Vault en memoria con config por connector_key (mismo patrón que
    test_voice_byo.py)."""

    def __init__(self, bundles: dict[str, dict[str, Any]] | None = None) -> None:
        self._bundles = bundles or {}
        self.puts: list[tuple[str, str]] = []

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        cfg = self._bundles.get(str(account_id))
        if cfg is None:
            return None
        return TokenBundle(access_token=json.dumps(cfg), token_type="config")

    async def put(
        self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle
    ) -> None:
        self.puts.append((str(account_id), bundle.access_token))
        try:
            self._bundles[str(account_id)] = json.loads(bundle.access_token)
        except (TypeError, ValueError):
            self._bundles[str(account_id)] = {}


def _connect_speech_engine_credential(fake_repo, tenant_id: uuid.UUID, api_key: str = _TENANT_KEY):
    """Crea la connector_account `speech_engine` del tenant en el FakeRepo."""
    account = None
    for row in fake_repo.connector_accounts.values():
        if row["tenant_id"] == tenant_id and row["connector_key"] == "speech_engine":
            account = row
    if account is None:
        from api_fakes import _now

        account = {
            "id": uuid.uuid4(),
            "tenant_id": tenant_id,
            "connector_key": "speech_engine",
            "external_account_id": "speech_engine",
            "display_name": "Speech Engine",
            "status": "active",
            "scopes": ["elevenlabs"],
            "created_at": _now(),
            "updated_at": _now(),
        }
        fake_repo.connector_accounts[account["id"]] = account
    return account


def _vault_para(fake_repo, tenant_id: uuid.UUID, api_key: str = _TENANT_KEY) -> _FakeVault:
    account = _connect_speech_engine_credential(fake_repo, tenant_id, api_key)
    return _FakeVault(
        {str(account["id"]): {"provider": "elevenlabs", "api_key": api_key}}
    )


class _FakeEngineResponse:
    speech_engine_id = "seng_fake_123"


class _FakeTokenResponse:
    token = "webrtc-token-efimero"


class _FakeProviderClient:
    """Doble determinista del cliente AsyncElevenLabs."""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []

    class speech_engine:
        client_ref: _FakeProviderClient | None = None

        @staticmethod
        def _ref():
            return _FakeProviderClient.speech_engine.client_ref  # type: ignore[attr-defined]

        @classmethod
        async def create(cls, **kwargs: Any) -> _FakeEngineResponse:
            ref = cls.client_ref
            assert ref is not None
            ref.created.append(kwargs)
            return _FakeEngineResponse()

        @classmethod
        async def delete(cls, engine_id: str) -> None:
            ref = cls.client_ref
            assert ref is not None
            ref.deleted.append(engine_id)

    class conversational_ai:
        class conversations:
            @staticmethod
            async def get_webrtc_token(*, agent_id: str, **_: Any) -> _FakeTokenResponse:
                return _FakeTokenResponse()


def _install_fake_provider(monkeypatch, vault):
    client = _FakeProviderClient()
    _FakeProviderClient.speech_engine.client_ref = client
    monkeypatch.setattr(speech_engine_module, "_build_provider_client", lambda key: client)
    return client


async def _make_active_session(
    fake_repo,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID | None = None,
    status: str = "active",
    plan_key: str = "hosted_basic",
) -> dict[str, Any]:
    if conversation_id is None:
        conversation = await fake_repo.resolve_main_conversation(
            tenant_id=tenant_id, user_id=user_id
        )
        conversation_id = conversation["id"]
    row = await fake_repo.create_speech_engine_session(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=conversation_id,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        max_duration_seconds=900,
        plan_key=plan_key,
    )
    return await fake_repo.update_speech_engine_session(
        session_id=row["id"],
        fields={"status": status, "provider_engine_id": "seng_fake_123"},
    )


@asynccontextmanager
async def _fake_platform_store(settings, fake_repo, vault):
    yield fake_repo, vault, None


# ---------------------------------------------------------------------------
# Preferencias
# ---------------------------------------------------------------------------


async def test_preferences_defaults_sin_credencial(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.get("/v1/voice/preferences", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["preference"] is None
    assert body["credential_connected"] is False
    assert isinstance(body["catalogs"]["voice_models"], list)
    assert body["catalogs"]["voice_models"]
    assert body["defaults"]["tts_model_id"]
    assert "factura" in body["paid_provider_notice"].lower() or "pago" in body["paid_provider_notice"].lower()


async def test_preferences_validan_catalogo_esfuerzo_y_proveedor(client) -> None:
    tenant_id = uuid.uuid4()
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    base = {"enabled": False, "voice_model_id": None, "delegation_model_id": None}

    mal_modelo = {**base, "voice_model_id": "@cf/no/existe"}
    assert (await client.put("/v1/voice/preferences", json=mal_modelo, headers=headers)).status_code == 422

    mal_esfuerzo = {**base, "delegation_effort": "gigante"}
    assert (await client.put("/v1/voice/preferences", json=mal_esfuerzo, headers=headers)).status_code == 422

    mal_proveedor = {**base, "provider": "otro"}
    assert (await client.put("/v1/voice/preferences", json=mal_proveedor, headers=headers)).status_code == 422

    mal_duracion = {**base, "max_duration_seconds": 10}
    assert (await client.put("/v1/voice/preferences", json=mal_duracion, headers=headers)).status_code == 422


async def test_preferences_enabled_sin_credencial_400(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.put(
        "/v1/voice/preferences", json={"enabled": True}, headers=headers
    )
    assert response.status_code == 400


async def test_preferences_enabled_con_credencial_ok(client, app, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    response = await client.put(
        "/v1/voice/preferences",
        json={
            "enabled": True,
            "voice_model_id": "@cf/meta/llama-4-scout-17b-16e-instruct",
            "delegation_model_id": None,
            "voice_id": "voz-1",
            "tts_model_id": "eleven_turbo_v2_5",
            "max_duration_seconds": 600,
        },
        headers=headers,
    )
    assert response.status_code == 200
    pref = response.json()["preference"]
    assert pref["enabled"] is True
    assert pref["voice_model_id"].endswith("scout-17b-16e-instruct")
    assert pref["max_duration_seconds"] == 600

    got = await client.get("/v1/voice/preferences", headers=headers)
    assert got.json()["preference"]["enabled"] is True
    assert got.json()["credential_connected"] is True


async def test_preferences_son_por_usuario_y_tenant(client, app, fake_repo) -> None:
    tenant_a = uuid.uuid4()
    user_a = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_a)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    headers_a = auth_headers(user_id=user_a, tenant_id=tenant_a)
    await client.put(
        "/v1/voice/preferences",
        json={"enabled": True, "voice_id": "voz-de-a"},
        headers=headers_a,
    )

    tenant_b = uuid.uuid4()
    user_b = uuid.uuid4()
    headers_b = auth_headers(user_id=user_b, tenant_id=tenant_b)
    got_b = await client.get("/v1/voice/preferences", headers=headers_b)
    assert got_b.json()["preference"] is None

    otro_user_de_a = uuid.uuid4()
    headers_otro = auth_headers(user_id=otro_user_de_a, tenant_id=tenant_a)
    got_otro = await client.get("/v1/voice/preferences", headers=headers_otro)
    assert got_otro.json()["preference"] is None


# ---------------------------------------------------------------------------
# Credencial del proveedor gestionado
# ---------------------------------------------------------------------------


async def test_credentials_speech_engine_guardado_y_expuesto_masked(client, app, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/voice/speech-engine",
        json={"provider": "elevenlabs", "api_key": _TENANT_KEY, "validate": False},
        headers=headers,
    )
    assert response.status_code == 204

    got = await client.get("/v1/credentials", headers=headers)
    se = got.json()["speech_engine"]
    assert se["provider"] == "elevenlabs"
    assert _TENANT_KEY not in se["masked"]


async def test_credentials_speech_engine_rechaza_otro_provider(client) -> None:
    headers = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.put(
        "/v1/credentials/voice/speech-engine",
        json={"provider": "deepgram", "api_key": "x", "validate": False},
        headers=headers,
    )
    assert response.status_code == 400


# ---------------------------------------------------------------------------
# Sesiones
# ---------------------------------------------------------------------------


async def test_sessions_exige_consentimiento_pagado(client, app, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await client.put(
        "/v1/voice/preferences", json={"enabled": True}, headers=headers
    )

    response = await client.post(
        "/v1/voice/speech-engine/sessions", json={"paid_consent": False}, headers=headers
    )
    assert response.status_code == 400


async def test_sessions_exige_preferencia_activada(client, app, fake_repo) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)

    response = await client.post(
        "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
    )
    assert response.status_code == 403


async def test_sessions_crea_conversacion_canonica_y_devuelve_token_efimero(
    client, app, fake_repo, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    _install_fake_provider(monkeypatch, vault)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await client.put(
        "/v1/voice/preferences",
        json={"enabled": True, "voice_id": "voz-1", "tts_model_id": "eleven_turbo_v2_5"},
        headers=headers,
    )

    response = await client.post(
        "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
    )
    assert response.status_code == 200, response.text
    body = response.json()
    # Token efímero, jamás la API key.
    assert body["conversation_token"] == "webrtc-token-efimero"
    assert _TENANT_KEY not in json.dumps(body)
    # La conversación canónica existe y es la misma.
    conversation = await fake_repo.get_conversation(
        tenant_id=tenant_id,
        user_id=user_id,
        conversation_id=uuid.UUID(body["conversation_id"]),
    )
    assert conversation is not None
    assert conversation["is_main"] is True
    # La sesión quedó activa con el engine del proveedor.
    row = await fake_repo.get_speech_engine_session(
        session_id=uuid.UUID(body["session_id"])
    )
    assert row["status"] == "active"
    assert row["provider_engine_id"] == "seng_fake_123"
    # Privacy y límites del provisionamiento.
    created = _FakeProviderClient.speech_engine.client_ref.created[0]  # type: ignore[attr-defined]
    assert created["privacy"].record_voice is False
    assert created["call_limits"].bursting_enabled is False
    assert created["call_limits"].agent_concurrency_limit == 1
    assert created["tts"].model_id == "eleven_turbo_v2_5"
    assert created["tts"].voice_id == "voz-1"
    assert f"/v1/voice/speech-engine/ws/{body['session_id']}" in created["speech_engine"].ws_url


async def test_sessions_conversacion_ajena_404(client, app, fake_repo, monkeypatch) -> None:
    tenant_a = uuid.uuid4()
    user_a = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_a)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    _install_fake_provider(monkeypatch, vault)
    headers_a = auth_headers(user_id=user_a, tenant_id=tenant_a)
    await client.put("/v1/voice/preferences", json={"enabled": True}, headers=headers_a)

    tenant_b = uuid.uuid4()
    user_b = uuid.uuid4()
    conversation_b = await fake_repo.create_conversation(
        tenant_id=tenant_b, user_id=user_b, title="chat de B", channel="web"
    )
    response = await client.post(
        "/v1/voice/speech-engine/sessions",
        json={"paid_consent": True, "conversation_id": str(conversation_b["id"])},
        headers=headers_a,
    )
    assert response.status_code == 404


async def test_sessions_fallo_de_provisionamiento_marca_failed(
    client, app, fake_repo, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault

    class _Explota:
        class speech_engine:
            @staticmethod
            async def create(**kwargs: Any) -> Any:
                raise RuntimeError("proveedor caído")

    monkeypatch.setattr(speech_engine_module, "_build_provider_client", lambda key: _Explota())
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await client.put("/v1/voice/preferences", json={"enabled": True}, headers=headers)

    response = await client.post(
        "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
    )
    assert response.status_code == 502
    sesiones = [row for row in fake_repo.speech_engine_sessions.values()]
    assert sesiones and sesiones[0]["status"] == "failed"


async def test_sessions_limite_activas_429(client, app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    _install_fake_provider(monkeypatch, vault)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await client.put("/v1/voice/preferences", json={"enabled": True}, headers=headers)

    for _ in range(3):
        response = await client.post(
            "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
        )
        assert response.status_code == 200
    response = await client.post(
        "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
    )
    assert response.status_code == 429


async def test_sessions_end_idempotente_y_limpia_proveedor(
    client, app, fake_repo, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    provider_client = _install_fake_provider(monkeypatch, vault)
    headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
    await client.put("/v1/voice/preferences", json={"enabled": True}, headers=headers)
    body = (
        await client.post(
            "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
        )
    ).json()

    end1 = await client.post(
        f"/v1/voice/speech-engine/sessions/{body['session_id']}/end", headers=headers
    )
    assert end1.status_code == 200
    end2 = await client.post(
        f"/v1/voice/speech-engine/sessions/{body['session_id']}/end", headers=headers
    )
    assert end2.status_code == 200
    assert provider_client.deleted.count("seng_fake_123") == 1

    row = await fake_repo.get_speech_engine_session(
        session_id=uuid.UUID(body["session_id"])
    )
    assert row["status"] == "ended"


async def test_sessions_status_ajeno_404(client, fake_repo) -> None:
    tenant_a = uuid.uuid4()
    user_a = uuid.uuid4()
    session = await _make_active_session(fake_repo, tenant_id=tenant_a, user_id=user_a)
    headers_b = auth_headers(user_id=uuid.uuid4(), tenant_id=uuid.uuid4())
    response = await client.get(
        f"/v1/voice/speech-engine/sessions/{session['id']}", headers=headers_b
    )
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# Callback WSS
# ---------------------------------------------------------------------------


def _ws_fixtures(
    app,
    fake_repo,
    monkeypatch,
    *,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID,
    session_row: dict[str, Any],
    vault,
) -> TestClient:
    @asynccontextmanager
    async def fake_store(settings: Any):
        yield fake_repo, vault, None

    monkeypatch.setattr(speech_engine_module, "_platform_store", fake_store)
    return TestClient(app)


def test_callback_rechaza_sin_jwt(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with test_client.websocket_connect(
                f"/v1/voice/speech-engine/ws/{session['id']}"
            ):
                pass
        assert exc.value.code == 4401


def test_callback_rechaza_jwt_de_otra_key(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)  # la key real del tenant
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with test_client.websocket_connect(
                f"/v1/voice/speech-engine/ws/{session['id']}",
                headers={
                    "x-elevenlabs-speech-engine-authorization": _provider_jwt(
                        "clave-de-OTRO-tenant"
                    )
                },
            ):
                pass
        assert exc.value.code == 4401


def test_callback_rechaza_sesion_no_activa_o_expirada(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(
            fake_repo, tenant_id=tenant_id, user_id=user_id, status="ended"
        )
    )
    vault = _vault_para(fake_repo, tenant_id)
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with test_client.websocket_connect(
                f"/v1/voice/speech-engine/ws/{session['id']}",
                headers={
                    "x-elevenlabs-speech-engine-authorization": _provider_jwt(_TENANT_KEY)
                },
            ):
                pass
        assert exc.value.code == 4403


class _TurnoFalso:
    """Doble del servicio de turnos: registra llamadas y emite deltas."""

    def __init__(self, *, texto: str, delegado: bool = False, tardio: bool = False) -> None:
        self.texto = texto
        self.delegado = delegado
        self.tardio = tardio
        self.llamadas_fast: list[dict[str, Any]] = []
        self.llamadas_delegated: list[dict[str, Any]] = []

    async def fast(self, *, user_text: str, fast_model_id, on_text_delta=None, **kwargs: Any):
        self.llamadas_fast.append(
            {"user_text": user_text, "fast_model_id": fast_model_id}
        )
        if self.tardio and len(self.llamadas_fast) == 1:
            await asyncio.sleep(3600)
        for delta in ["hola", " ", "¿cómo", " ", "estás?"]:
            if on_text_delta is not None:
                await on_text_delta(delta)
        # Doble fiel del servicio: persiste el enunciado UNA vez (salvo que el
        # router ya lo haya persistido en transacción corta) y la respuesta,
        # como hace `execute_fast_interlocutor_turn` real.
        repo = kwargs.get("repo")
        if repo is not None and not kwargs.get("already_persisted_input"):
            await repo.add_message(
                tenant_id=kwargs["current_user"].tenant_id,
                conversation_id=kwargs["conversation_id"],
                role="user",
                content={"text": user_text},
            )
            await repo.add_message(
                tenant_id=kwargs["current_user"].tenant_id,
                conversation_id=kwargs["conversation_id"],
                role="assistant",
                content={"text": "hola ¿cómo estás?"},
            )
        return SimpleNamespace(
            text="hola ¿cómo estás?",
            delegated=self.delegado,
            confirmation_required=None,
        )

    async def delegated(self, *, user_text: str, delegation_seleccion=None, on_text_delta=None, **kwargs: Any):
        self.llamadas_delegated.append(
            {"user_text": user_text, "delegation_seleccion": delegation_seleccion}
        )
        if on_text_delta is not None:
            await on_text_delta("encargado")
        return SimpleNamespace(
            text="encargado", delegated=True, confirmation_required=None
        )


def _install_fake_turns(monkeypatch, turno: _TurnoFalso) -> _TurnoFalso:
    monkeypatch.setattr(speech_engine_module, "execute_fast_interlocutor_turn", turno.fast)
    monkeypatch.setattr(speech_engine_module, "execute_delegated_turn", turno.delegated)
    return turno


def _ws_headers() -> dict[str, str]:
    return {"x-elevenlabs-speech-engine-authorization": _provider_jwt(_TENANT_KEY)}


def test_callback_turno_rapido_streama_y_persiste_una_vez(
    app, fake_repo, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    turno = _install_fake_turns(monkeypatch, _TurnoFalso(texto="hola ¿cómo estás?"))

    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(
                {
                    "type": "user_transcript",
                    "event_id": 1,
                    "user_transcript": [{"role": "user", "content": "hola ¿cómo estás?"}],
                }
            )
            received = []
            while True:
                message = websocket.receive_json()
                received.append(message)
                if message["type"] == "agent_response" and message["is_final"]:
                    break
    # Deltas llegaron ANTES del final (streaming, no acumulación).
    deltas = [m for m in received if m["type"] == "agent_response" and not m["is_final"]]
    assert deltas, received
    assert "".join(m["content"] for m in deltas) == "hola ¿cómo estás?"
    # El enunciado se persiste UNA vez en la conversación canónica.
    user_messages = [
        m for m in fake_repo.messages.get(session["conversation_id"], []) if m["role"] == "user"
    ]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == {"text": "hola ¿cómo estás?"}
    # Claim persistido.
    event = asyncio.run(
        fake_repo.get_speech_engine_event(session_id=session["id"], event_id=1)
    )
    assert event["status"] == "persisted"
    assert event["user_text"] == "hola ¿cómo estás?"
    # Ruta rápida usó el modelo configurado (ninguno aquí) sin delegar.
    assert turno.llamadas_fast
    assert not turno.llamadas_delegated


def test_callback_replay_no_duplica_turno(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    turno = _install_fake_turns(monkeypatch, _TurnoFalso(texto="hola"))
    transcript = {"type": "user_transcript", "event_id": 7, "user_transcript": [{"role": "user", "content": "hola"}]}

    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(transcript)
            while True:
                message = websocket.receive_json()
                if message.get("is_final"):
                    break
    assert len(turno.llamadas_fast) == 1

    # Replay del MISMO event_id en una conexión nueva: no re-ejecuta.
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(transcript)
            while True:
                message = websocket.receive_json()
                if message.get("is_final"):
                    break
    assert len(turno.llamadas_fast) == 1
    user_messages = [
        m for m in fake_repo.messages.get(session["conversation_id"], []) if m["role"] == "user"
    ]
    assert len(user_messages) == 1


def test_callback_delegacion_determinista_salta_interlocutor(
    app, fake_repo, monkeypatch
) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    turno = _install_fake_turns(monkeypatch, _TurnoFalso(texto="encargado"))

    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(
                {
                    "type": "user_transcript",
                    "event_id": 2,
                    "user_transcript": [
                        {"role": "user", "content": "dile al Developer que revise el deploy"}
                    ],
                }
            )
            while True:
                if websocket.receive_json().get("is_final"):
                    break
    assert not turno.llamadas_fast
    assert turno.llamadas_delegated


def test_callback_ping_responde_pong(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json({"type": "ping"})
            assert websocket.receive_json() == {"type": "pong"}


# ---------------------------------------------------------------------------
# Turnos (servicio) — ruteo de modelos sin pisar chat_model
# ---------------------------------------------------------------------------


def test_modelo_valido_para_turno_solo_catalogo() -> None:
    from edecan_api.speech_engine_turn_service import _modelo_valido_para_turno

    assert _modelo_valido_para_turno(None) is None
    assert _modelo_valido_para_turno("@cf/no/existe") is None
    seleccion = _modelo_valido_para_turno("@cf/meta/llama-4-scout-17b-16e-instruct")
    assert seleccion is not None
    assert seleccion.modelo.endswith("scout-17b-16e-instruct")


def test_require_delegation_detecta_verbos_y_no_sobredetecta() -> None:
    from edecan_api.speech_engine_turn_service import require_delegation

    assert require_delegation("dile al Developer que revise el deploy")
    assert require_delegation("encarga una misión que investigue el mercado")
    assert not require_delegation("hola, ¿cómo estás?")
    assert not require_delegation("¿qué hora es?")


def test_delegate_tool_solo_pide_delegar() -> None:
    import asyncio as _asyncio

    from edecan_core.tools import ToolContext

    from edecan_api.speech_engine_turn_service import DelegateToEdecanTool

    tool = DelegateToEdecanTool()
    ctx = ToolContext(
        tenant_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        session=None,
        settings=None,
        llm=None,
        vault=None,
        extras={},
    )
    result = _asyncio.run(tool.run(ctx, {}))
    assert tool.invoked is True
    assert result.content
    assert tool.input_schema["additionalProperties"] is False


def test_callback_interrupcion_marca_interrupted(app, fake_repo, monkeypatch) -> None:
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = asyncio.run(
        _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    )
    vault = _vault_para(fake_repo, tenant_id)
    _install_fake_turns(monkeypatch, _TurnoFalso(texto="...", tardio=True))

    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(
                {
                    "type": "user_transcript",
                    "event_id": 1,
                    "user_transcript": [{"role": "user", "content": "primero"}],
                }
            )
            # Interrupción: transcript nuevo cancela el turno anterior.
            websocket.send_json(
                {
                    "type": "user_transcript",
                    "event_id": 2,
                    "user_transcript": [{"role": "user", "content": "segundo"}],
                }
            )
            while True:
                message = websocket.receive_json()
                if message.get("is_final"):
                    break
    event1 = asyncio.run(
        fake_repo.get_speech_engine_event(session_id=session["id"], event_id=1)
    )
    assert event1["status"] == "interrupted"


# ---------------------------------------------------------------------------
# Wire adapter (protocolo)
# ---------------------------------------------------------------------------


class _WSFake:
    def __init__(self) -> None:
        self.inbox: asyncio.Queue[Any] = asyncio.Queue()
        self.sent: list[str] = []
        self.cerrado = False

    async def receive_text(self) -> str:
        item = await self.inbox.get()
        if isinstance(item, Exception):
            raise item
        return item

    async def send_text(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        self.cerrado = True


# ---------------------------------------------------------------------------
# Turno REAL del interlocutor rápido (Agent falso) — persistencia y selección
# ---------------------------------------------------------------------------


class _FakeAgentEvent:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


class _FakeAgent:
    """Doble de `edecan_core.agent.Agent`: registra `seleccion`/`extra_tools`
    y emite un turno determinista."""

    ultimo: dict[str, Any] = {}

    def __init__(self, llm_router, registry, **kwargs: Any) -> None:
        self.registry = registry
        self.kwargs = kwargs

    async def run_turn(self, **kwargs: Any) -> Any:
        _FakeAgent.ultimo = kwargs
        for item in ["hola", " ", "mundo"]:
            yield _FakeAgentEvent(type="text_delta", text=item)
        yield _FakeAgentEvent(
            type="done",
            usage={"input_tokens": 10, "output_tokens": 5},
            attribution={"model": "modelo-de-prueba"},
        )


def _fake_request(app) -> SimpleNamespace:
    state = SimpleNamespace(
        tool_registry=app.state.tool_registry,
        provider_health=None,
        llm_router=app.state.llm_router,
        companion_manager=app.state.companion_manager,
    )
    return SimpleNamespace(app=SimpleNamespace(state=state))


async def test_interlocutor_real_persiste_una_vez_y_pasa_seleccion(
    app, fake_repo, monkeypatch, test_settings
) -> None:
    import edecan_api.speech_engine_turn_service as turn_service
    from edecan_api.speech_engine_turn_service import execute_fast_interlocutor_turn

    monkeypatch.setattr(turn_service, "Agent", _FakeAgent)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation = await fake_repo.resolve_main_conversation(
        tenant_id=tenant_id, user_id=user_id
    )
    await fake_repo.create_persona_default(tenant_id=tenant_id, user_id=user_id)
    from edecan_api.deps import CurrentUser, TenantCtx, flags_for_plan

    current_user = CurrentUser(
        user_id=user_id,
        tenant=TenantCtx(
            tenant_id=tenant_id, plan_key="hosted_basic", flags=flags_for_plan("hosted_basic")
        ),
    )
    outcome = await execute_fast_interlocutor_turn(
        request=_fake_request(app),
        session=None,
        repo=fake_repo,
        vault=None,
        current_user=current_user,
        settings=test_settings,
        llm_router=app.state.llm_router,
        conversation_id=conversation["id"],
        user_text="hola mundo",
        fast_model_id="@cf/meta/llama-4-scout-17b-16e-instruct",
    )
    assert outcome.text == "hola mundo"
    assert outcome.delegated is False
    # Selección efectiva con el modelo rápido configurado (no se tocó
    # conversations.chat_model: solo viaja en el turno).
    seleccion = _FakeAgent.ultimo["seleccion"]
    assert seleccion.modelo.endswith("scout-17b-16e-instruct")
    conversation_despues = await fake_repo.get_conversation(
        tenant_id=tenant_id, user_id=user_id, conversation_id=conversation["id"]
    )
    assert conversation_despues["chat_model"] is None
    # Mensajes persistidos UNA vez.
    user_messages = [
        m for m in fake_repo.messages.get(conversation["id"], []) if m["role"] == "user"
    ]
    assistant_messages = [
        m for m in fake_repo.messages.get(conversation["id"], []) if m["role"] == "assistant"
    ]
    assert len(user_messages) == 1
    assert user_messages[0]["content"] == {"text": "hola mundo"}
    assert len(assistant_messages) == 1
    # Uso LLM registrado.
    assert any(e["kind"] == "llm_tokens" for e in fake_repo.usage_events)


async def test_interlocutor_real_delega_cuando_la_tool_se_invoca(
    app, fake_repo, monkeypatch, test_settings
) -> None:
    import edecan_api.speech_engine_turn_service as turn_service
    from edecan_api.speech_engine_turn_service import (
        DelegateToEdecanTool,
        execute_fast_interlocutor_turn,
    )

    class _FakeAgentQueDelega(_FakeAgent):
        async def run_turn(self, **kwargs: Any) -> Any:
            _FakeAgent.ultimo = kwargs
            # La tool estrecha está en extra_tools; el modelo la "invoca".
            delegate_tool = next(
                (t for t in kwargs["extra_tools"] if isinstance(t, DelegateToEdecanTool)),
                None,
            )
            assert delegate_tool is not None
            from edecan_core.tools import ToolContext

            result = await delegate_tool.run(
                ToolContext(
                    tenant_id=kwargs["ctx"].tenant_id,
                    user_id=kwargs["ctx"].user_id,
                    session=None,
                    settings=None,
                    llm=None,
                    vault=None,
                    extras={},
                ),
                {},
            )
            yield _FakeAgentEvent(type="text_delta", text="(delegando)")
            yield _FakeAgentEvent(type="tool_end", tool=result.content)
            yield _FakeAgentEvent(
                type="done", usage={"input_tokens": 3, "output_tokens": 2}, attribution={}
            )

    monkeypatch.setattr(turn_service, "Agent", _FakeAgentQueDelega)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    conversation = await fake_repo.resolve_main_conversation(
        tenant_id=tenant_id, user_id=user_id
    )
    await fake_repo.create_persona_default(tenant_id=tenant_id, user_id=user_id)
    from edecan_api.deps import CurrentUser, TenantCtx, flags_for_plan

    current_user = CurrentUser(
        user_id=user_id,
        tenant=TenantCtx(
            tenant_id=tenant_id, plan_key="hosted_basic", flags=flags_for_plan("hosted_basic")
        ),
    )
    outcome = await execute_fast_interlocutor_turn(
        request=_fake_request(app),
        session=None,
        repo=fake_repo,
        vault=None,
        current_user=current_user,
        settings=test_settings,
        llm_router=app.state.llm_router,
        conversation_id=conversation["id"],
        user_text="revisa mi correo y actúa",
        fast_model_id=None,
    )
    # La tool pidió delegar: el texto rápido se descartó y el llamador
    # (el callback) correrá el flujo delegado SIN volver a persistir.
    assert outcome.delegated is True
    assert outcome.text == ""


async def test_wire_ping_pong_y_tipos_desconocidos_ignorados() -> None:
    from edecan_api.speech_engine_wire import SpeechEngineWireSession

    ws = _WSFake()
    session = SpeechEngineWireSession(ws)
    await ws.inbox.put(json.dumps({"type": "ping"}))
    await ws.inbox.put(json.dumps({"type": "algo_futuro", "x": 1}))
    await ws.inbox.put(json.dumps({"type": "ping"}))
    task = asyncio.create_task(session.run())
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pongs = [json.loads(s) for s in ws.sent]
    assert pongs == [{"type": "pong"}, {"type": "pong"}]


async def test_wire_event_id_duplicado_no_reemite_handler() -> None:
    from edecan_api.speech_engine_wire import SpeechEngineWireSession

    ws = _WSFake()
    session = SpeechEngineWireSession(ws)
    llamadas: list[int] = []
    primera = asyncio.Event()

    async def handler(transcript, event_id: int) -> None:
        llamadas.append(event_id)
        primera.set()
        await asyncio.sleep(0.2)  # el turno sigue "en vuelo"
        await session.send_agent_response("ok", is_final=True)

    session.on("user_transcript", handler)
    message = {
        "type": "user_transcript",
        "event_id": 3,
        "user_transcript": [{"role": "user", "content": "hola"}],
    }
    await ws.inbox.put(json.dumps(message))
    task = asyncio.create_task(session.run())
    await primera.wait()  # el handler YA corre: un duplicado ahora se salta
    await ws.inbox.put(json.dumps(message))
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert llamadas == [3]
    # El fast-path de dedupe es solo mientras el handler corre; el anti-replay
    # DURABLE contra reconexiones es el claim en DB (test_callback_replay_*).


async def test_callback_expira_por_fecha_y_cierra_4403(
    app, fake_repo, monkeypatch
) -> None:
    """Expiración DURANTE la sesión (no solo al conectar): un turno que llega
    después de `expires_at` cierra el callback y no ejecuta nada."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = await _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    await fake_repo.update_speech_engine_session(
        session_id=session["id"],
        fields={"expires_at": datetime.now(UTC) - timedelta(seconds=5)},
    )
    vault = _vault_para(fake_repo, tenant_id)
    turno = _install_fake_turns(monkeypatch, _TurnoFalso(texto="hola"))
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with pytest.raises(WebSocketDisconnect) as exc:
            with test_client.websocket_connect(
                f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
            ) as websocket:
                websocket.send_json(
                    {
                        "type": "user_transcript",
                        "event_id": 9,
                        "user_transcript": [{"role": "user", "content": "hola"}],
                    }
                )
                websocket.receive_json()
    assert exc.value.code == 4403
    assert not turno.llamadas_fast
    fila = fake_repo.speech_engine_sessions[session["id"]]
    assert fila["status"] == "expired"


async def test_sessions_fallo_de_token_elimina_engine_y_marca_failed(
    client, app, fake_repo, monkeypatch
) -> None:
    """Si el engine se creó pero el token WebRTC falla, el recurso pagado se
    elimina del proveedor y la sesión queda `failed` (sin facturar huérfanos)."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    vault = _vault_para(fake_repo, tenant_id)
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    proveedor = _install_fake_provider(monkeypatch, vault)

    class _TokenExplota:
        @staticmethod
        async def get_webrtc_token(*, agent_id: str, **_: Any) -> Any:
            raise RuntimeError("token caído")

    original_conversations = _FakeProviderClient.conversational_ai.conversations
    _FakeProviderClient.conversational_ai.conversations = _TokenExplota
    try:
        headers = auth_headers(user_id=user_id, tenant_id=tenant_id)
        await client.put("/v1/voice/preferences", json={"enabled": True}, headers=headers)
        response = await client.post(
            "/v1/voice/speech-engine/sessions", json={"paid_consent": True}, headers=headers
        )
    finally:
        _FakeProviderClient.conversational_ai.conversations = original_conversations

    assert response.status_code == 502
    assert proveedor.deleted, "el engine creado debe eliminarse tras el fallo del token"
    sesiones = list(fake_repo.speech_engine_sessions.values())
    assert sesiones and sesiones[0]["status"] == "failed"


async def test_delegacion_hereda_modelo_y_esfuerzo_del_chat(
    app, fake_repo, monkeypatch
) -> None:
    """Sin `delegation_model_id` en preferencias, el turno delegado recibe el
    `SeleccionDeModelo` con el chat_model/chat_effort de la conversación."""
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    session = await _make_active_session(fake_repo, tenant_id=tenant_id, user_id=user_id)
    fake_repo.conversations[session["conversation_id"]]["chat_model"] = (
        "@cf/meta/llama-4-scout-17b-16e-instruct"
    )
    fake_repo.conversations[session["conversation_id"]]["chat_effort"] = "alto"
    vault = _vault_para(fake_repo, tenant_id)
    turno = _install_fake_turns(monkeypatch, _TurnoFalso(texto="encargado"))
    with _ws_fixtures(
        app, fake_repo, monkeypatch,
        tenant_id=tenant_id, user_id=user_id, session_row=session, vault=vault,
    ) as test_client:
        with test_client.websocket_connect(
            f"/v1/voice/speech-engine/ws/{session['id']}", headers=_ws_headers()
        ) as websocket:
            websocket.send_json(
                {
                    "type": "user_transcript",
                    "event_id": 4,
                    "user_transcript": [
                        {"role": "user", "content": "dile al Developer que revise el deploy"}
                    ],
                }
            )
            while True:
                if websocket.receive_json().get("is_final"):
                    break
    assert turno.llamadas_delegated
    seleccion = turno.llamadas_delegated[0]["delegation_seleccion"]
    assert seleccion.modelo == "@cf/meta/llama-4-scout-17b-16e-instruct"
    assert seleccion.esfuerzo == "alto"