"""`/v1/setup/*` — `apps/api/edecan_api/routers/setup.py` (WP-V3-05,
`ARCHITECTURE.md` §12.a/§12.d).

Mismas convenciones que `test_credentials_router.py`/`test_smarthome_router.py`:
`client`/`app`/`fake_repo`/`auth_headers` de `conftest.py` (NO se toca ese
archivo), `FakeVault` local con `put`/`get` en memoria. `GET /v1/setup/detect`
importa `edecan_llm.detect.detect_local_providers` DENTRO de la función (import
perezoso con guardia, ver docstring de `setup.py`) — se monkeypatchea el
símbolo real de `edecan_llm.detect` (no algo dentro de `setup.py`, que no lo
tiene como atributo de módulo) para controlar su resultado, y se simula
"WP-V3-03 todavía no aterrizó" con `monkeypatch.setitem(sys.modules,
"edecan_llm.detect", None)` (mismo truco que `test_run_campaign_step.py`
usa para `edecan_premium`).
"""

from __future__ import annotations

import sys
import uuid
from typing import Any

import pytest
from conftest import TEST_JWT_SECRET, auth_headers
from edecan_schemas import TokenBundle

import edecan_api.deps as edecan_deps
from edecan_api import __version__
from edecan_api.config import Settings, get_settings


class FakeVault:
    """Doble de `edecan_db.vault.TokenVault` con `put` + `get` en memoria
    (mismo patrón que `test_credentials_router.py`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._store: dict[tuple[uuid.UUID, uuid.UUID], TokenBundle] = {}

    async def put(self, tenant_id: uuid.UUID, account_id: uuid.UUID, bundle: TokenBundle) -> None:
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
    """`EDECAN_LOCAL_MODE=True` (mismo helper que `test_credentials_router.py`)."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        JWT_SECRET=TEST_JWT_SECRET,
        WEB_BASE_URL="http://localhost:3000",
        PUBLIC_BASE_URL="http://localhost:8000",
        EDECAN_LOCAL_MODE=True,
    )


async def _conectar_llm(fake_repo: Any, fake_vault: FakeVault, tenant_id: uuid.UUID) -> None:
    account = await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="llm",
        external_account_id="llm",
        display_name="Proveedor LLM",
        scopes=["anthropic"],
    )
    await fake_vault.put(
        tenant_id,
        account["id"],
        TokenBundle(access_token='{"kind": "anthropic"}', token_type="config"),
    )


# ---------------------------------------------------------------------------
# GET /v1/setup/status
# ---------------------------------------------------------------------------


async def test_get_status_requires_authentication(client) -> None:
    response = await client.get("/v1/setup/status")
    assert response.status_code == 401


async def test_get_status_por_defecto_no_local_ni_llm_configurado(client, app) -> None:
    _install_vault(app)
    response = await client.get("/v1/setup/status", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {
        "local_mode": False,
        "llm_configured": False,
        "onboarding_completed": False,
        "lifetime_updates": False,
        "version": __version__,
    }


async def test_get_status_onboarding_completed_false_por_defecto(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant = await fake_repo.create_tenant(name="Acme", slug="acme", plan_key="hosted_pro")
    response = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant["id"])
    )
    assert response.status_code == 200
    assert response.json()["onboarding_completed"] is False


async def test_get_status_lifetime_updates_false_por_defecto_true_tras_compra(
    client, app, fake_repo
) -> None:
    _install_vault(app)
    tenant = await fake_repo.create_tenant(name="Acme", slug="acme", plan_key="hosted_pro")
    headers = _headers(user_id=uuid.uuid4(), tenant_id=tenant["id"])

    antes = await client.get("/v1/setup/status", headers=headers)
    assert antes.json()["lifetime_updates"] is False

    await fake_repo.update_tenant_lifetime_updates(tenant["id"])

    despues = await client.get("/v1/setup/status", headers=headers)
    assert despues.json()["lifetime_updates"] is True


async def test_put_setup_complete_marca_onboarding_completado(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant = await fake_repo.create_tenant(name="Acme", slug="acme", plan_key="hosted_pro")
    headers = _headers(user_id=uuid.uuid4(), tenant_id=tenant["id"])

    put_response = await client.put("/v1/setup/complete", headers=headers)
    assert put_response.status_code == 204

    status_response = await client.get("/v1/setup/status", headers=headers)
    assert status_response.json()["onboarding_completed"] is True


async def test_put_setup_complete_requires_authentication(client) -> None:
    response = await client.put("/v1/setup/complete")
    assert response.status_code == 401


async def test_put_setup_complete_no_mezcla_tenants(client, app, fake_repo) -> None:
    _install_vault(app)
    tenant_a = await fake_repo.create_tenant(name="A", slug="a", plan_key="hosted_pro")
    tenant_b = await fake_repo.create_tenant(name="B", slug="b", plan_key="hosted_pro")

    await client.put(
        "/v1/setup/complete", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_a["id"])
    )

    resp_a = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_a["id"])
    )
    resp_b = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_b["id"])
    )
    assert resp_a.json()["onboarding_completed"] is True
    assert resp_b.json()["onboarding_completed"] is False


async def test_get_status_sin_vault_instalado_no_revienta(client) -> None:
    """`app` (fixture de `conftest.py`) deja `get_vault` -> `None` por
    defecto -- `get_setup_status` debe tratarlo como "sin LLM configurado",
    nunca reventar con un `AttributeError`."""
    response = await client.get("/v1/setup/status", headers=_headers())
    assert response.status_code == 200
    assert response.json()["llm_configured"] is False


async def test_get_status_local_mode_true(client, app) -> None:
    _install_vault(app)
    _use_local_mode(app)
    response = await client.get("/v1/setup/status", headers=_headers())
    assert response.status_code == 200
    assert response.json()["local_mode"] is True


async def test_get_status_llm_configurado_true_tras_conectar(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_id = uuid.uuid4()
    await _conectar_llm(fake_repo, fake_vault, tenant_id)

    response = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    )
    assert response.status_code == 200
    assert response.json()["llm_configured"] is True


async def test_get_status_cuenta_sin_bundle_en_el_vault_sigue_false(client, app, fake_repo) -> None:
    """`connector_account` creada pero sin `vault.put` (p. ej. una carrera o
    un estado inconsistente) -- se trata igual que "no configurado"."""
    _install_vault(app)
    tenant_id = uuid.uuid4()
    await fake_repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key="llm",
        external_account_id="llm",
        display_name="Proveedor LLM",
        scopes=[],
    )

    response = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_id)
    )
    assert response.status_code == 200
    assert response.json()["llm_configured"] is False


async def test_get_status_no_mezcla_tenants(client, app, fake_repo) -> None:
    fake_vault = _install_vault(app)
    tenant_con_llm = uuid.uuid4()
    tenant_sin_llm = uuid.uuid4()
    await _conectar_llm(fake_repo, fake_vault, tenant_con_llm)

    con = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_con_llm)
    )
    sin = await client.get(
        "/v1/setup/status", headers=_headers(user_id=uuid.uuid4(), tenant_id=tenant_sin_llm)
    )
    assert con.json()["llm_configured"] is True
    assert sin.json()["llm_configured"] is False


# ---------------------------------------------------------------------------
# GET /v1/setup/detect
# ---------------------------------------------------------------------------

_EMPTY_SHAPE = {
    "claude_cli": {"installed": False, "path": None, "version": None},
    "codex_cli": {"installed": False, "path": None, "version": None},
    "ollama": {"running": False, "base_url": "", "models": []},
}


async def test_get_detect_requires_authentication(client) -> None:
    response = await client.get("/v1/setup/detect")
    assert response.status_code == 401


async def test_get_detect_sin_local_mode_devuelve_shape_vacio_sin_detectar(
    client, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`EDECAN_LOCAL_MODE=False` (default): NO debe siquiera llamar a
    `detect_local_providers` -- si lo hiciera, este monkeypatch la haría
    fallar y el test lo atraparía."""

    def _no_deberia_llamarse(settings: Any = None) -> dict[str, Any]:
        raise AssertionError("detect_local_providers no debe llamarse fuera de modo local")

    monkeypatch.setattr(
        "edecan_llm.detect.detect_local_providers", _no_deberia_llamarse, raising=True
    )

    response = await client.get("/v1/setup/detect", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"local_mode": False, **_EMPTY_SHAPE}


async def test_get_detect_local_mode_delega_en_detect_local_providers(
    client, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    _use_local_mode(app)
    detectado = {
        "claude_cli": {"installed": True, "path": "/usr/local/bin/claude", "version": "1.2.3"},
        "codex_cli": {"installed": False, "path": None, "version": None},
        "ollama": {"running": True, "base_url": "http://localhost:11434", "models": ["llama3.1"]},
    }
    monkeypatch.setattr("edecan_llm.detect.detect_local_providers", lambda settings=None: detectado)

    response = await client.get("/v1/setup/detect", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"local_mode": True, **detectado}


async def test_get_detect_local_mode_sin_edecan_llm_detect_no_revienta(
    client, app, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simula que WP-V3-03 todavía no aterrizó `edecan_llm.detect` (mismo
    truco que `test_run_campaign_step.py` usa para `edecan_premium`:
    `sys.modules[...] = None` fuerza `ImportError` determinista)."""
    _use_local_mode(app)
    monkeypatch.setitem(sys.modules, "edecan_llm.detect", None)

    response = await client.get("/v1/setup/detect", headers=_headers())
    assert response.status_code == 200
    assert response.json() == {"local_mode": True, **_EMPTY_SHAPE}
