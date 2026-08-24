"""Contratos de credenciales administradas y capacidades BYO."""

from __future__ import annotations

import json
import uuid
from typing import Any

from conftest import TEST_JWT_SECRET, auth_headers
from edecan_llm.workers_ai import MODELO_POR_DEFECTO
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
from edecan_api.config import Settings, get_settings


class FakeVault:
    def __init__(self) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}

    async def put(
        self,
        tenant_id: uuid.UUID,
        account_id: uuid.UUID,
        bundle: TokenBundle,
    ) -> None:
        self._store[(tenant_id, account_id)] = bundle

    async def get(
        self,
        tenant_id: uuid.UUID,
        account_id: uuid.UUID,
    ) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


def _headers(*, tenant_id: uuid.UUID | None = None) -> dict[str, str]:
    return auth_headers(
        user_id=uuid.uuid4(),
        tenant_id=tenant_id or uuid.uuid4(),
        plan_key="hosted_pro",
    )


def _install_vault(app: Any) -> FakeVault:
    vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: vault
    return vault


def _use_local_mode(app: Any) -> None:
    app.dependency_overrides[get_settings] = lambda: Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        EDECAN_LOCAL_MODE=True,
        CLOUDFLARE_ACCOUNT_ID="test-cloudflare-account",
        CLOUDFLARE_API_TOKEN="test-cloudflare-token",
    )


async def test_get_credentials_requires_authentication(client) -> None:
    response = await client.get("/v1/credentials")
    assert response.status_code == 401


async def test_get_credentials_expone_workers_ai_sin_secreto(client, app) -> None:
    _install_vault(app)
    response = await client.get("/v1/credentials", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["llm"] == {
        "kind": "workers_ai",
        "model_principal": MODELO_POR_DEFECTO,
        "model_rapido": MODELO_POR_DEFECTO,
        "model_profundo": MODELO_POR_DEFECTO,
        "reasoning_effort_profundo": None,
        "base_url": None,
        "masked": "Administrado por Edecan",
    }
    assert "token" not in json.dumps(body).lower()


async def test_get_credentials_sin_workers_ai_configurado_marca_llm_none(client, app) -> None:
    _install_vault(app)
    app.dependency_overrides[get_settings] = lambda: Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        CLOUDFLARE_ACCOUNT_ID=None,
        CLOUDFLARE_API_TOKEN=None,
    )
    response = await client.get("/v1/credentials", headers=_headers())
    assert response.status_code == 200
    assert response.json()["llm"] is None


async def test_get_modelos_declara_seleccion_automatica(client, app) -> None:
    _install_vault(app)
    response = await client.get("/v1/credentials/llm/models", headers=_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "workers_ai"
    assert body["models"] == [MODELO_POR_DEFECTO]
    assert body["manual_allowed"] is False
    assert body["capabilities_managed_by_edecan"] is True


async def test_put_llm_legacy_se_rechaza_sin_guardar(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "openai_compat", "api_key": "no-se-guarda"},
        headers=_headers(tenant_id=tenant_id),
    )
    assert response.status_code == 409
    assert "Workers AI" in response.json()["detail"]
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


async def test_patch_modelos_se_rechaza(client, app) -> None:
    _install_vault(app)
    response = await client.patch(
        "/v1/credentials/llm/models",
        json={"model_principal": "modelo-elegido-por-usuario"},
        headers=_headers(),
    )
    assert response.status_code == 409
    assert "automáticamente" in response.json()["detail"]


async def test_delete_llm_limpia_solo_config_legacy(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="llm",
        external_account_id="llm",
        display_name="Proveedor legado",
        scopes=[],
    )
    response = await client.delete(
        "/v1/credentials/llm",
        headers=_headers(tenant_id=tenant_id),
    )
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


async def test_put_voice_stt_guarda_y_enmascara(client, app) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "dg-secreto-1234", "validate": False},
        headers=headers,
    )
    assert response.status_code == 204
    body = (await client.get("/v1/credentials", headers=headers)).json()
    assert body["voice_stt"] == {"provider": "deepgram", "masked": "…1234"}
    assert "dg-secreto" not in json.dumps(body)


async def test_put_voice_tts_elevenlabs_guarda_y_enmascara(client, app) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    response = await client.put(
        "/v1/credentials/voice/tts",
        json={
            "provider": "elevenlabs",
            "api_key": "xi-secreto-5678",
            "voice_id": "voz-principal",
            "validate": False,
        },
        headers=headers,
    )
    assert response.status_code == 204
    body = (await client.get("/v1/credentials", headers=headers)).json()
    assert body["voice_tts"] == {
        "provider": "elevenlabs",
        "voice_id": "voz-principal",
        "masked": "…5678",
    }


async def test_put_voice_tts_polly_solo_en_modo_local(client, app) -> None:
    _install_vault(app)
    rejected = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "polly", "voice_id": "Lupe"},
        headers=_headers(),
    )
    assert rejected.status_code == 400

    _use_local_mode(app)
    accepted = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "polly", "voice_id": "Lupe"},
        headers=_headers(),
    )
    assert accepted.status_code == 204


async def test_put_images_guarda_y_enmascara(client, app) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    response = await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images.example/v1",
            "api_key": "image-secret-9876",
            "model": "image-model",
            "validate": False,
        },
        headers=headers,
    )
    assert response.status_code == 204
    body = (await client.get("/v1/credentials", headers=headers)).json()
    assert body["images"] == {
        "base_url": "https://images.example/v1",
        "model": "image-model",
        "masked": "…9876",
    }


async def test_put_search_guarda_y_enmascara(client, app) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "brave-secret-2468", "validate": False},
        headers=headers,
    )
    assert response.status_code == 204
    body = (await client.get("/v1/credentials", headers=headers)).json()
    assert body["search"] == {"provider": "brave", "masked": "…2468"}


async def test_capacidades_byo_no_se_mezclan_entre_tenants(client, app) -> None:
    _install_vault(app)
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    await client.put(
        "/v1/credentials/search",
        json={"provider": "tavily", "api_key": "tenant-a-key", "validate": False},
        headers=_headers(tenant_id=tenant_a),
    )
    body_b = (
        await client.get("/v1/credentials", headers=_headers(tenant_id=tenant_b))
    ).json()
    assert body_b["search"] is None
    assert body_b["llm"]["kind"] == "workers_ai"
