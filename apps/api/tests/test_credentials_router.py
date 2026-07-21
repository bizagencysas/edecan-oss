"""`/v1/credentials/*` — bring-your-own de LLM y voz (STT/TTS) por tenant
(WP-V3-02, `ARCHITECTURE.md` §10.4/§12.b, `apps/api/edecan_api/routers/credentials.py`,
`docs/credenciales.md`).

Mismas convenciones que `test_connectors_credentials_v2.py`: `client`/`app`/
`fake_repo`/`auth_headers` de `conftest.py`. A diferencia del `FakeVault` de
ese archivo (que solo registra `put`), este necesita también `get` — `GET
/v1/credentials` lee de vuelta lo que guardó un `PUT` anterior — así que trae
su PROPIO `FakeVault` local en memoria (no se toca `conftest.py`).

Cada test lleva `@respx.mock` (incluso los que no esperan tráfico real, p.
ej. `validate=false`): con `assert_all_mocked=True` de fábrica, CUALQUIER
llamada HTTP no interceptada explícitamente hace fallar el test en vez de
pegarle a la red de verdad (guardrail "cero llamadas de red reales en
tests" del paquete de trabajo).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx
import respx
from conftest import TEST_JWT_SECRET, auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
import edecan_api.routers.credentials as credentials_module
from edecan_api.config import Settings, get_settings


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault` con `put` + `get` en memoria."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}
        self.puts: list[tuple[uuid.UUID, uuid.UUID, TokenBundle]] = []

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
        self.puts.append((tenant_id, account_id, bundle))
        self._store[(tenant_id, account_id)] = bundle

    async def get(self, tenant_id: uuid.UUID, account_id: uuid.UUID) -> TokenBundle | None:
        return self._store.get((tenant_id, account_id))


def _headers(**overrides: Any) -> dict[str, str]:
    return auth_headers(
        user_id=overrides.pop("user_id", uuid.uuid4()),
        tenant_id=overrides.pop("tenant_id", uuid.uuid4()),
        plan_key=overrides.pop("plan_key", "hosted_pro"),
    )


def _install_vault(app: Any) -> FakeVault:
    fake_vault = FakeVault()
    app.dependency_overrides[edecan_deps.get_vault] = lambda: fake_vault
    return fake_vault


def _use_local_mode(app: Any) -> None:
    """`EDECAN_LOCAL_MODE=True` — solo así se aceptan `claude_cli`/`codex_cli`/
    `ollama` (ver docstring de `credentials.py`)."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        EDECAN_LOCAL_MODE=True,
    )


class _FakeProcess:
    def __init__(self, *, returncode: int, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:  # pragma: no cover - solo se usa en el camino de timeout
        pass

    async def wait(self) -> None:  # pragma: no cover - idem
        pass


# ---------------------------------------------------------------------------
# GET /v1/credentials
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_credentials_requires_authentication(client) -> None:
    response = await client.get("/v1/credentials")
    assert response.status_code == 401


@respx.mock
async def test_get_credentials_vacio_por_defecto(client, app) -> None:
    _install_vault(app)
    response = await client.get("/v1/credentials", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {
        "llm": None,
        "voice_stt": None,
        "voice_tts": None,
        "images": None,
        "search": None,
    }


@respx.mock
async def test_get_credentials_config_ilegible_no_revienta(client, app, fake_repo) -> None:
    """`_read_config` traga JSON corrupto y lo trata como "nada conectado"
    (ver docstring del módulo `credentials.py`)."""
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="llm",
        external_account_id="llm",
        display_name="Proveedor LLM",
        scopes=[],
    )
    await fake_vault.put(
        tenant_id, account["id"], TokenBundle(access_token="esto no es JSON", token_type="config")
    )

    response = await client.get(
        "/v1/credentials", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    )
    assert response.status_code == 200
    assert response.json()["llm"] is None


# ---------------------------------------------------------------------------
# PUT /v1/credentials/llm — validación de payload (validate=false, sin red)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_llm_requires_authentication(client) -> None:
    response = await client.put("/v1/credentials/llm", json={"kind": "anthropic"})
    assert response.status_code == 401


@respx.mock
async def test_put_llm_kind_desconocido_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm", json={"kind": "no-existe"}, headers=_headers()
    )
    assert response.status_code == 400


@respx.mock
async def test_put_llm_anthropic_sin_api_key_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm", json={"kind": "anthropic", "validate": False}, headers=_headers()
    )
    assert response.status_code == 400


@respx.mock
async def test_put_llm_vertex_sin_api_key_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm", json={"kind": "vertex", "validate": False}, headers=_headers()
    )
    assert response.status_code == 400


@respx.mock
async def test_put_llm_vertex_service_account_no_requiere_api_key(client, app) -> None:
    """`extra.mode == "service_account"` es la excepción a
    `_LLM_KINDS_REQUIEREN_API_KEY`: NO debe pedir `api_key` (a diferencia de
    `test_put_llm_vertex_sin_api_key_400`, que cubre el modo `api_key` por
    defecto) — sí debe pedir `extra.project_id`/`extra.service_account_json`."""
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "vertex",
            "validate": False,
            "extra": {"mode": "service_account"},
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "project_id" in response.json()["detail"]
    assert "service_account_json" in response.json()["detail"]


@respx.mock
async def test_put_llm_openai_compat_sin_base_url_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "openai_compat", "api_key": "sk-x", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_llm_openai_compat_sin_validar_requiere_modelo(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "openai_compat",
            "base_url": "https://miendpoint.example.com/v1",
            "api_key": "sk-x",
            "validate": False,
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "model_principal" in response.json()["detail"]


@respx.mock
async def test_put_llm_ollama_sin_model_principal_400(client, app) -> None:
    _use_local_mode(app)
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm", json={"kind": "ollama", "validate": False}, headers=_headers()
    )
    assert response.status_code == 400


@respx.mock
async def test_put_llm_kinds_locales_rechazados_fuera_de_modo_local(client, app) -> None:
    """`EDECAN_LOCAL_MODE` por defecto es `False` (fixture `test_settings`) —
    `claude_cli`/`codex_cli`/`ollama` deben rechazarse con 400 en hosted."""
    _install_vault(app)
    for kind in ("claude_cli", "codex_cli", "ollama"):
        response = await client.put(
            "/v1/credentials/llm",
            json={"kind": kind, "model_principal": "algo", "validate": False},
            headers=_headers(),
        )
        assert response.status_code == 400, kind
        assert "escritorio" in response.json()["detail"]


@respx.mock
async def test_put_llm_openai_compat_sin_validar_guarda_y_enmascara(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "openai_compat",
            "base_url": "https://api.groq.com/openai/v1",
            "api_key": "gsk_1234567890ABCD",
            "model_principal": "llama-3.3-70b",
            "validate": False,
        },
        headers=headers,
    )
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "llm"
    assert accounts[0]["external_account_id"] == "llm"

    assert len(fake_vault.puts) == 1
    stored_tenant_id, stored_account_id, bundle = fake_vault.puts[0]
    assert stored_tenant_id == tenant_id
    assert stored_account_id == accounts[0]["id"]
    assert bundle.token_type == "config"
    assert bundle.scopes == ["openai_compat"]
    stored = json.loads(bundle.access_token)
    assert stored == {
        "kind": "openai_compat",
        "api_key": "gsk_1234567890ABCD",
        "base_url": "https://api.groq.com/openai/v1",
        "model_principal": "llama-3.3-70b",
        "model_rapido": "llama-3.3-70b",
        "extra": {},
    }

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "credentials.llm.connected" in actions

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["llm"] == {
        "kind": "openai_compat",
        "model_principal": "llama-3.3-70b",
        "model_rapido": "llama-3.3-70b",
        "base_url": "https://api.groq.com/openai/v1",
        "masked": "…ABCD",
    }


@respx.mock
async def test_put_llm_vertex_service_account_sin_validar_guarda_sin_api_key(
    client, app, fake_repo
) -> None:
    """Reproduce EXACTAMENTE el payload que manda `SelectorLLM.tsx` (botón
    "Conectar con cuenta de servicio", modo avanzado): sin `api_key`, con
    `extra.mode == "service_account"`. Antes del fix, esto siempre daba 400
    ("vertex requiere api_key.") sin llegar a guardar nada."""
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "vertex",
            "validate": False,
            "extra": {
                "mode": "service_account",
                "service_account_json": '{"type": "service_account"}',
                "project_id": "mi-proyecto-gcp",
                "region": "us-central1",
            },
        },
        headers=headers,
    )
    assert response.status_code == 204

    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored["kind"] == "vertex"
    assert stored["api_key"] is None
    assert stored["extra"]["mode"] == "service_account"
    assert stored["extra"]["project_id"] == "mi-proyecto-gcp"

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["llm"]["kind"] == "vertex"
    assert get_response.json()["llm"]["masked"] is None


@respx.mock
async def test_put_llm_reconecta_reusa_la_misma_cuenta(client, app, fake_repo) -> None:
    """Singleton por tenant: un segundo `PUT` actualiza la MISMA
    `connector_account`, no crea una segunda (ver docstring de
    `credentials.py`, "_find_or_create_account")."""
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    for api_key in ("sk-ant-primera-key", "sk-ant-segunda-key"):
        response = await client.put(
            "/v1/credentials/llm",
            json={"kind": "anthropic", "api_key": api_key, "validate": False},
            headers=headers,
        )
        assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1


@respx.mock
async def test_put_llm_reconecta_guarda_la_key_mas_reciente(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-AAAA1111", "validate": False},
        headers=headers,
    )
    await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-BBBB2222", "validate": False},
        headers=headers,
    )

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["llm"]["masked"] == "…2222"


# ---------------------------------------------------------------------------
# PUT /v1/credentials/llm — "pegar y validar" (validate=true, respx)
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_llm_anthropic_valida_contra_la_api_real(client, app, fake_repo) -> None:
    respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-real-de-prueba"},
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_llm_anthropic_key_rechazada_no_guarda_nada(client, app, fake_repo) -> None:
    respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-mala"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "401" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_llm_vertex_valida_contra_google_ai(client, app) -> None:
    respx.get("https://generativelanguage.googleapis.com/v1beta/models").mock(
        return_value=httpx.Response(200, json={"models": []})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "vertex", "api_key": "AIzaSyDUMMY"},
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_llm_vertex_service_account_json_invalido_400(client, app) -> None:
    """`validate=true` (default) en modo `service_account` valida la FORMA
    del JSON localmente — sin llamar a la red (si lo hiciera, `@respx.mock`
    sin rutas registradas haría fallar este test, ver
    `test_put_voice_tts_polly_no_hace_ping_de_red`)."""
    fake_vault = _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "vertex",
            "extra": {
                "mode": "service_account",
                "service_account_json": "esto no es JSON",
                "project_id": "mi-proyecto-gcp",
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_llm_vertex_service_account_json_sin_campos_esperados_400(client, app) -> None:
    fake_vault = _install_vault(app)
    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "vertex",
            "extra": {
                "mode": "service_account",
                "service_account_json": '{"type": "service_account"}',
                "project_id": "mi-proyecto-gcp",
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "client_email" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_llm_vertex_service_account_forma_valida_no_pega_a_la_red(
    client, app, fake_repo
) -> None:
    """`validate=true` (default) con un JSON con forma correcta guarda sin
    hacer ninguna llamada real a Google (ver docstring de
    `_ping_vertex_service_account`: el extra `google-auth` es opcional y
    `apps/api` no lo instala por defecto)."""
    fake_vault = _install_vault(app)
    service_account_json = json.dumps(
        {
            "type": "service_account",
            "project_id": "mi-proyecto-gcp",
            "client_email": "svc@mi-proyecto-gcp.iam.gserviceaccount.com",
            "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )

    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "vertex",
            "extra": {
                "mode": "service_account",
                "service_account_json": service_account_json,
                "project_id": "mi-proyecto-gcp",
            },
        },
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_llm_openai_compat_valida_endpoint_propio(client, app) -> None:
    respx.get("https://miendpoint.example.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "modelo-general-2", "created": 2},
                    {"id": "modelo-general-1", "created": 1},
                ]
            },
        )
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={
            "kind": "openai_compat",
            "base_url": "https://miendpoint.example.com/v1",
            "api_key": "sk-x",
        },
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored["model_principal"] == "modelo-general-2"
    assert stored["model_rapido"] == "modelo-general-2"


@respx.mock
async def test_put_llm_descubre_mejor_modelo_anthropic(client, app) -> None:
    respx.get("https://api.anthropic.com/v1/models").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"id": "claude-haiku-4-6-20260601"},
                    {"id": "claude-opus-4-6-20260501"},
                ]
            },
        )
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-real-de-prueba"},
        headers=_headers(),
    )

    assert response.status_code == 204
    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored["model_principal"] == "claude-opus-4-6-20260501"
    assert stored["model_rapido"] == "claude-haiku-4-6-20260601"


@respx.mock
async def test_put_llm_openai_compat_endpoint_caido_502_es_400(client, app) -> None:
    respx.get("https://roto.example.com/v1/models").mock(return_value=httpx.Response(502))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "openai_compat", "base_url": "https://roto.example.com/v1"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_llm_ollama_valida_y_usa_default_base_url_en_modo_local(client, app) -> None:
    respx.get("http://localhost:11434/api/tags").mock(
        return_value=httpx.Response(200, json={"models": [{"name": "llama3.1:8b"}]})
    )
    _use_local_mode(app)
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/llm",
        json={"kind": "ollama", "model_principal": "llama3.1:8b"},
        headers=_headers(),
    )
    assert response.status_code == 204
    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored["base_url"] == "http://localhost:11434"


@respx.mock
async def test_put_llm_claude_cli_valida_subproceso_ok(client, app, monkeypatch) -> None:
    _use_local_mode(app)
    fake_vault = _install_vault(app)

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProcess:
        assert args[0] == "claude"
        assert args[1] == "--version"
        return _FakeProcess(returncode=0, stdout=b"1.2.3\n")

    monkeypatch.setattr(credentials_module.asyncio, "create_subprocess_exec", fake_exec)

    response = await client.put(
        "/v1/credentials/llm", json={"kind": "claude_cli"}, headers=_headers()
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_llm_codex_cli_binario_no_instalado_400(client, app, monkeypatch) -> None:
    _use_local_mode(app)
    fake_vault = _install_vault(app)

    async def fake_exec(*args: Any, **kwargs: Any) -> _FakeProcess:
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(credentials_module.asyncio, "create_subprocess_exec", fake_exec)

    response = await client.put(
        "/v1/credentials/llm", json={"kind": "codex_cli"}, headers=_headers()
    )
    assert response.status_code == 400
    assert "codex" in response.json()["detail"]
    assert fake_vault.puts == []


# ---------------------------------------------------------------------------
# DELETE /v1/credentials/llm
# ---------------------------------------------------------------------------


@respx.mock
async def test_delete_llm_credentials(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-a-borrar", "validate": False},
        headers=headers,
    )

    response = await client.delete("/v1/credentials/llm", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["llm"] is None

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "credentials.llm.disconnected" in actions


@respx.mock
async def test_delete_llm_credentials_es_idempotente(client, app) -> None:
    _install_vault(app)
    response = await client.delete("/v1/credentials/llm", headers=_headers())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# PUT /v1/credentials/voice/stt
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_voice_stt_provider_desconocido_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "whisper-local", "api_key": "x", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_voice_stt_api_key_vacio_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "   ", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_voice_stt_deepgram_valida_contra_la_api_real(client, app, fake_repo) -> None:
    respx.get("https://api.deepgram.com/v1/projects").mock(
        return_value=httpx.Response(200, json={"projects": []})
    )
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "dg_1234567890ABCD"},
        headers=headers,
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1
    _, _, bundle = fake_vault.puts[0]
    assert bundle.scopes == ["deepgram"]

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["voice_stt"] == {"provider": "deepgram", "masked": "…ABCD"}


@respx.mock
async def test_put_voice_stt_deepgram_key_rechazada_no_guarda_nada(client, app) -> None:
    respx.get("https://api.deepgram.com/v1/projects").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "dg_mala"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_delete_voice_stt_credentials(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "dg_x", "validate": False},
        headers=headers,
    )

    response = await client.delete("/v1/credentials/voice/stt", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


@respx.mock
async def test_delete_voice_canal_desconocido_404(client, app) -> None:
    _install_vault(app)
    response = await client.delete("/v1/credentials/voice/video", headers=_headers())
    assert response.status_code == 404


# ---------------------------------------------------------------------------
# PUT /v1/credentials/voice/tts
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_voice_tts_provider_desconocido_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "azure", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_voice_tts_elevenlabs_sin_api_key_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "elevenlabs", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_voice_tts_elevenlabs_valida_contra_la_api_real(client, app, fake_repo) -> None:
    respx.get("https://api.elevenlabs.io/v1/user").mock(
        return_value=httpx.Response(200, json={"subscription": {}})
    )
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/voice/tts",
        json={
            "provider": "elevenlabs",
            "api_key": "el_1234567890ABCD",
            "voice_id": "voz-alfa",
        },
        headers=headers,
    )
    assert response.status_code == 204
    _, _, bundle = fake_vault.puts[0]
    assert json.loads(bundle.access_token) == {
        "provider": "elevenlabs",
        "api_key": "el_1234567890ABCD",
        "voice_id": "voz-alfa",
    }

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["voice_tts"] == {
        "provider": "elevenlabs",
        "voice_id": "voz-alfa",
        "masked": "…ABCD",
    }


@respx.mock
async def test_put_voice_tts_elevenlabs_key_rechazada_no_guarda_nada(client, app) -> None:
    respx.get("https://api.elevenlabs.io/v1/user").mock(return_value=httpx.Response(401))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "elevenlabs", "api_key": "el_mala"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_voice_tts_polly_rechazado_fuera_de_modo_local(client, app) -> None:
    """`EDECAN_LOCAL_MODE` por defecto es `False` (fixture `test_settings`) —
    `polly` usa la identidad AWS del PROCESO, así que se rechaza con 400 en
    hosted (mismo criterio que `test_put_llm_kinds_locales_rechazados_fuera_
    de_modo_local`, ver docstring de `credentials.py`)."""
    fake_vault = _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "polly", "voice_id": "Mia"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert "escritorio" in response.json()["detail"]
    assert fake_vault.puts == []


@respx.mock
async def test_put_voice_tts_polly_no_hace_ping_de_red(client, app, fake_repo) -> None:
    """Polly usa la cadena de credenciales AWS del cliente, no una API key —
    `validate=true` (default) NO debe intentar ninguna llamada HTTP (si lo
    hiciera, `@respx.mock` sin rutas registradas haría fallar este test).
    Requiere `EDECAN_LOCAL_MODE=True` (ver `test_put_voice_tts_polly_
    rechazado_fuera_de_modo_local`)."""
    _use_local_mode(app)
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "polly", "voice_id": "Mia"},
        headers=headers,
    )
    assert response.status_code == 204
    _, _, bundle = fake_vault.puts[0]
    assert json.loads(bundle.access_token) == {"provider": "polly", "voice": "Mia"}

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["voice_tts"] == {
        "provider": "polly",
        "voice_id": "Mia",
        "masked": None,
    }


@respx.mock
async def test_put_voice_tts_polly_usa_voz_default_sin_voice_id(client, app) -> None:
    _use_local_mode(app)
    fake_vault = _install_vault(app)
    response = await client.put(
        "/v1/credentials/voice/tts", json={"provider": "polly"}, headers=_headers()
    )
    assert response.status_code == 204
    _, _, bundle = fake_vault.puts[0]
    assert json.loads(bundle.access_token)["voice"] == "Lupe"


@respx.mock
async def test_delete_voice_tts_credentials(client, app, fake_repo) -> None:
    _use_local_mode(app)
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/credentials/voice/tts", json={"provider": "polly"}, headers=headers
    )

    response = await client.delete("/v1/credentials/voice/tts", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/images (auditoría "riesgo-legal-tos": antes de
# esto, `edecan_creative.providers.get_image_provider` no tenía ningún
# mecanismo bring-your-own — ver docstring de `credentials.py`).
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_images_campos_obligatorios_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/images",
        json={"base_url": "", "api_key": "k", "model": "m", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_images_sin_validar_guarda_y_enmascara(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images.example.com/v1",
            "api_key": "img_1234567890ABCD",
            "model": "mi-modelo",
            "validate": False,
        },
        headers=headers,
    )
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1
    assert accounts[0]["connector_key"] == "images"

    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored == {
        "base_url": "https://images.example.com/v1",
        "api_key": "img_1234567890ABCD",
        "model": "mi-modelo",
    }

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "credentials.images.connected" in actions

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["images"] == {
        "base_url": "https://images.example.com/v1",
        "model": "mi-modelo",
        "masked": "…ABCD",
    }


@respx.mock
async def test_put_images_valida_contra_el_endpoint_propio(client, app) -> None:
    respx.get("https://images.example.com/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images.example.com/v1",
            "api_key": "img-key",
            "model": "mi-modelo",
        },
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_images_key_rechazada_no_guarda_nada(client, app) -> None:
    respx.get("https://images.example.com/v1/models").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images.example.com/v1",
            "api_key": "img-mala",
            "model": "mi-modelo",
        },
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_images_reconecta_reusa_la_misma_cuenta(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    for api_key in ("img-primera", "img-segunda"):
        response = await client.put(
            "/v1/credentials/images",
            json={
                "base_url": "https://images.example.com/v1",
                "api_key": api_key,
                "model": "mi-modelo",
                "validate": False,
            },
            headers=headers,
        )
        assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert len(accounts) == 1


@respx.mock
async def test_delete_images_credentials(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/credentials/images",
        json={
            "base_url": "https://images.example.com/v1",
            "api_key": "img-a-borrar",
            "model": "m",
            "validate": False,
        },
        headers=headers,
    )

    response = await client.delete("/v1/credentials/images", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["images"] is None

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "credentials.images.disconnected" in actions


@respx.mock
async def test_delete_images_credentials_es_idempotente(client, app) -> None:
    _install_vault(app)
    response = await client.delete("/v1/credentials/images", headers=_headers())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/credentials/search (misma auditoría que /images, ver arriba
# — `edecan_toolkit.research.get_search_provider` no tenía mecanismo
# bring-your-own).
# ---------------------------------------------------------------------------


@respx.mock
async def test_put_search_provider_desconocido_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "duckduckgo", "api_key": "x", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_search_api_key_vacio_400(client, app) -> None:
    _install_vault(app)
    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "   ", "validate": False},
        headers=_headers(),
    )
    assert response.status_code == 400


@respx.mock
async def test_put_search_brave_sin_validar_guarda_y_enmascara(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "brave_1234567890ABCD", "validate": False},
        headers=headers,
    )
    assert response.status_code == 204

    accounts = await fake_repo.list_connector_accounts(tenant_id=tenant_id)
    assert accounts[0]["connector_key"] == "search"
    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored == {"provider": "brave", "api_key": "brave_1234567890ABCD"}

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["search"] == {"provider": "brave", "masked": "…ABCD"}


@respx.mock
async def test_put_search_brave_valida_contra_la_api_real(client, app) -> None:
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(200, json={"web": {"results": []}})
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "brave-key"},
        headers=_headers(),
    )
    assert response.status_code == 204
    assert len(fake_vault.puts) == 1


@respx.mock
async def test_put_search_brave_key_rechazada_no_guarda_nada(client, app) -> None:
    respx.get("https://api.search.brave.com/res/v1/web/search").mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "brave-mala"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_put_search_tavily_valida_contra_la_api_real(client, app, fake_repo) -> None:
    respx.post("https://api.tavily.com/search").mock(
        return_value=httpx.Response(200, json={"results": []})
    )
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "tavily", "api_key": "tavily-key"},
        headers=headers,
    )
    assert response.status_code == 204
    stored = json.loads(fake_vault.puts[0][2].access_token)
    assert stored == {"provider": "tavily", "api_key": "tavily-key"}


@respx.mock
async def test_put_search_tavily_key_rechazada_no_guarda_nada(client, app) -> None:
    respx.post("https://api.tavily.com/search").mock(return_value=httpx.Response(401))
    fake_vault = _install_vault(app)

    response = await client.put(
        "/v1/credentials/search",
        json={"provider": "tavily", "api_key": "tavily-mala"},
        headers=_headers(),
    )
    assert response.status_code == 400
    assert fake_vault.puts == []


@respx.mock
async def test_delete_search_credentials(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)
    await client.put(
        "/v1/credentials/search",
        json={"provider": "brave", "api_key": "brave-a-borrar", "validate": False},
        headers=headers,
    )

    response = await client.delete("/v1/credentials/search", headers=headers)
    assert response.status_code == 204
    assert await fake_repo.list_connector_accounts(tenant_id=tenant_id) == []

    get_response = await client.get("/v1/credentials", headers=headers)
    assert get_response.json()["search"] is None

    actions = [entry["action"] for entry in fake_repo.audit_log]
    assert "credentials.search.disconnected" in actions


@respx.mock
async def test_delete_search_credentials_es_idempotente(client, app) -> None:
    _install_vault(app)
    response = await client.delete("/v1/credentials/search", headers=_headers())
    assert response.status_code == 204


# ---------------------------------------------------------------------------
# GET /v1/credentials — foto completa con los tres recursos conectados
# ---------------------------------------------------------------------------


@respx.mock
async def test_get_credentials_con_los_tres_recursos_conectados(client, app) -> None:
    _use_local_mode(app)  # `provider: "polly"` de abajo lo exige, ver docstring de credentials.py
    _install_vault(app)
    tenant_id = uuid.uuid4()
    headers = _headers(tenant_id=tenant_id)

    await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-AAAAZZZZ", "validate": False},
        headers=headers,
    )
    await client.put(
        "/v1/credentials/voice/stt",
        json={"provider": "deepgram", "api_key": "dg_BBBBZZZZ", "validate": False},
        headers=headers,
    )
    await client.put(
        "/v1/credentials/voice/tts",
        json={"provider": "polly", "voice_id": "Mia"},
        headers=headers,
    )

    response = await client.get("/v1/credentials", headers=headers)
    body = response.json()
    assert body["llm"]["kind"] == "anthropic"
    assert body["llm"]["masked"] == "…ZZZZ"
    assert body["voice_stt"] == {"provider": "deepgram", "masked": "…ZZZZ"}
    assert body["voice_tts"] == {"provider": "polly", "voice_id": "Mia", "masked": None}


@respx.mock
async def test_get_credentials_no_mezcla_tenants(client, app) -> None:
    _install_vault(app)
    tenant_a, tenant_b = uuid.uuid4(), uuid.uuid4()

    await client.put(
        "/v1/credentials/llm",
        json={"kind": "anthropic", "api_key": "sk-ant-DE-A", "validate": False},
        headers=_headers(tenant_id=tenant_a),
    )

    response_b = await client.get("/v1/credentials", headers=_headers(tenant_id=tenant_b))
    assert response_b.json() == {
        "llm": None,
        "voice_stt": None,
        "voice_tts": None,
        "images": None,
        "search": None,
    }
