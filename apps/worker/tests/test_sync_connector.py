"""Tests del job `sync_connector`: refresca tokens por expirar vía el vault."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import edecan_worker.handlers.sync_connector as sync_connector_module
from edecan_schemas import JobEnvelope
from fakes import FakeRepo, FakeTokenBundle, FakeVault, make_deps


class FakeConnector:
    def __init__(self) -> None:
        self.refresh_calls: list[tuple[FakeTokenBundle, str, str | None]] = []

    async def refresh(
        self, bundle: FakeTokenBundle, http, *, client_id: str, client_secret: str | None
    ) -> FakeTokenBundle:
        self.refresh_calls.append((bundle, client_id, client_secret))
        return FakeTokenBundle(access_token="token-nuevo", refresh_token=bundle.refresh_token)


def _seed_app_config(
    fake_repo: FakeRepo, fake_vault: FakeVault, tenant_id, connector_key: str, client_id: str
) -> uuid.UUID:
    """Simula que el tenant ya pegó su app OAuth propia para `connector_key`
    (fila `"{connector_key}__app_config"` en `connector_accounts` + secreto en
    el vault) -- ver `edecan_api.oauth_app_credentials` y el docstring de
    `sync_connector.py`."""
    account_id = uuid.uuid4()
    fake_repo.connector_accounts.append(
        {
            "id": account_id,
            "tenant_id": tenant_id,
            "connector_key": f"{connector_key}__app_config",
            "external_account_id": client_id,
            "created_at": datetime.now(UTC),
        }
    )
    fake_vault.store[(tenant_id, account_id)] = FakeTokenBundle(
        access_token=f"{connector_key}-client-secret"
    )
    return account_id


async def test_sync_connector_refresca_y_guarda_en_el_vault(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(sync_connector_module, "SqlRepo", lambda session: fake_repo)

    fake_connector = FakeConnector()
    monkeypatch.setattr(sync_connector_module, "CONNECTORS", {"google": fake_connector})

    tenant_id = uuid.uuid4()
    connector_account_id = uuid.uuid4()
    fake_repo.oauth_tokens.append(
        {
            "tenant_id": tenant_id,
            "connector_account_id": connector_account_id,
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            "connector_key": "google",
        }
    )

    fake_vault = FakeVault()
    fake_vault.store[(tenant_id, connector_account_id)] = FakeTokenBundle(
        access_token="token-viejo", refresh_token="refresh-abc"
    )
    _seed_app_config(fake_repo, fake_vault, tenant_id, "google", "google-client-id")

    deps = make_deps(vault=lambda session: fake_vault)
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="sync_connector", payload={})
    await sync_connector_module.handle(env, deps)

    assert len(fake_connector.refresh_calls) == 1
    refreshed_bundle, client_id, client_secret = fake_connector.refresh_calls[0]
    assert refreshed_bundle.access_token == "token-viejo"
    assert client_id == "google-client-id"
    assert client_secret == "google-client-secret"
    assert len(fake_vault.puts) == 1
    saved_tenant, saved_account, saved_bundle = fake_vault.puts[0]
    assert saved_tenant == tenant_id
    assert saved_account == connector_account_id
    assert saved_bundle.access_token == "token-nuevo"


async def test_sync_connector_sin_app_oauth_propia_no_refresca(monkeypatch) -> None:
    """Si el tenant borró su config de app OAuth propia después de conectar la
    cuenta (`DELETE /v1/connectors/{key}/app-credentials`), `sync_connector`
    debe loguear y saltarse esa cuenta -- nunca lanzar `TypeError` por faltarle
    `client_id`/`client_secret` a `connector.refresh`."""
    fake_repo = FakeRepo()
    monkeypatch.setattr(sync_connector_module, "SqlRepo", lambda session: fake_repo)

    fake_connector = FakeConnector()
    monkeypatch.setattr(sync_connector_module, "CONNECTORS", {"google": fake_connector})

    tenant_id = uuid.uuid4()
    connector_account_id = uuid.uuid4()
    fake_repo.oauth_tokens.append(
        {
            "tenant_id": tenant_id,
            "connector_account_id": connector_account_id,
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            "connector_key": "google",
        }
    )
    fake_vault = FakeVault()
    fake_vault.store[(tenant_id, connector_account_id)] = FakeTokenBundle(
        access_token="token-viejo", refresh_token="refresh-abc"
    )
    # A propósito: NINGÚN `_seed_app_config` -- el tenant no tiene (o ya no
    # tiene) su app OAuth propia configurada.

    deps = make_deps(vault=lambda session: fake_vault)
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="sync_connector", payload={})
    await sync_connector_module.handle(env, deps)  # no debe lanzar

    assert fake_connector.refresh_calls == []
    assert fake_vault.puts == []


async def test_sync_connector_token_no_por_expirar_se_ignora(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(sync_connector_module, "SqlRepo", lambda session: fake_repo)
    fake_connector = FakeConnector()
    monkeypatch.setattr(sync_connector_module, "CONNECTORS", {"google": fake_connector})

    tenant_id = uuid.uuid4()
    fake_repo.oauth_tokens.append(
        {
            "tenant_id": tenant_id,
            "connector_account_id": uuid.uuid4(),
            "expires_at": datetime.now(UTC) + timedelta(hours=5),  # lejos de expirar
            "connector_key": "google",
        }
    )

    fake_vault = FakeVault()
    deps = make_deps(vault=lambda session: fake_vault)
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="sync_connector", payload={})
    await sync_connector_module.handle(env, deps)

    assert fake_connector.refresh_calls == []
    assert fake_vault.puts == []


async def test_sync_connector_un_fallo_no_detiene_los_demas(monkeypatch) -> None:
    fake_repo = FakeRepo()
    monkeypatch.setattr(sync_connector_module, "SqlRepo", lambda session: fake_repo)

    class ConnectorQueFalla:
        async def refresh(self, bundle, http, *, client_id, client_secret):
            raise RuntimeError("refresh_token revocado")

    connector_ok = FakeConnector()
    monkeypatch.setattr(
        sync_connector_module,
        "CONNECTORS",
        {"google": ConnectorQueFalla(), "microsoft": connector_ok},
    )

    tenant_id = uuid.uuid4()
    cuenta_falla = uuid.uuid4()
    cuenta_ok = uuid.uuid4()
    vence_pronto = datetime.now(UTC) + timedelta(minutes=1)
    fake_repo.oauth_tokens.extend(
        [
            {
                "tenant_id": tenant_id,
                "connector_account_id": cuenta_falla,
                "expires_at": vence_pronto,
                "connector_key": "google",
            },
            {
                "tenant_id": tenant_id,
                "connector_account_id": cuenta_ok,
                "expires_at": vence_pronto,
                "connector_key": "microsoft",
            },
        ]
    )

    fake_vault = FakeVault()
    fake_vault.store[(tenant_id, cuenta_falla)] = FakeTokenBundle(
        access_token="a", refresh_token="ra"
    )
    fake_vault.store[(tenant_id, cuenta_ok)] = FakeTokenBundle(access_token="b", refresh_token="rb")
    _seed_app_config(fake_repo, fake_vault, tenant_id, "google", "google-client-id")
    _seed_app_config(fake_repo, fake_vault, tenant_id, "microsoft", "ms-client-id")

    deps = make_deps(vault=lambda session: fake_vault)
    env = JobEnvelope(job_id=uuid.uuid4(), tenant_id=tenant_id, type="sync_connector", payload={})
    await sync_connector_module.handle(env, deps)  # no debe propagar la excepción

    assert len(connector_ok.refresh_calls) == 1
    assert len(fake_vault.puts) == 1
    assert fake_vault.puts[0][1] == cuenta_ok
