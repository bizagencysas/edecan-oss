"""Credenciales de la app OAuth PROPIA de cada tenant para los conectores
oficiales (Google/Microsoft/Meta/X/YouTube/Slack) — ARCHITECTURE.md §10.8.

Decisión de producto (2026-07-09): Edecán se vende como pago único
self-hosteado ("comprar el código y olvidarme") — el dueño del producto no
puede quedar operacionalmente atado a mantener una app OAuth compartida entre
todos sus clientes (cuotas de uso compartidas, revisión/verificación de
Google o Meta, una app suspendida por el abuso de UN cliente rompiendo a
todos los demás). Por eso cada tenant registra y pega SU PROPIA app OAuth
(`client_id` + `client_secret`) con cada proveedor que quiera usar — mismo
patrón "pegar y validar" que ya usan Stripe (`routers/finance.py`), Twilio/
WhatsApp (`routers/connectors.py`) e iCloud (`routers/contacts.py`).

Reutiliza `connector_accounts` + `TokenVault` SIN migración nueva: la fila de
config vive bajo un `connector_key` reservado `"{key}__app_config"` — NUNCA
debe aparecer en la lista de "cuentas conectadas" de `GET /v1/connectors`
(`routers/connectors.py::list_connectors` la filtra a propósito con
`is_app_config_connector_key`). `external_account_id` guarda el `client_id`
EN CLARO (no es secreto, es el identificador público de la app ante el
proveedor); el `client_secret` viaja cifrado en el vault bajo el id de esa
fila, como cualquier `TokenBundle` — vacío (`""`) si el proveedor admite apps
sin secreto (p. ej. X con PKCE puro), nunca `None` porque `TokenBundle.
access_token` es un campo obligatorio.
"""

from __future__ import annotations

import uuid
from typing import Any

from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle

from edecan_api.repo import Repo

_APP_CONFIG_SUFFIX = "__app_config"


def app_config_connector_key(key: str) -> str:
    return f"{key}{_APP_CONFIG_SUFFIX}"


def is_app_config_connector_key(connector_key: str) -> bool:
    return connector_key.endswith(_APP_CONFIG_SUFFIX)


def base_connector_key(app_config_key: str) -> str:
    """Inversa de `app_config_connector_key`: `"google__app_config"` -> `"google"`."""
    return app_config_key.removesuffix(_APP_CONFIG_SUFFIX)


def mask_client_id(client_id: str) -> str:
    if len(client_id) <= 10:
        return client_id
    return f"{client_id[:8]}…{client_id[-4:]}"


async def find_oauth_app_account(
    repo: Repo, tenant_id: uuid.UUID, key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == app_config_connector_key(key)]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


async def get_oauth_app_credentials(
    repo: Repo, vault: TokenVault, tenant_id: uuid.UUID, key: str
) -> tuple[str, str | None] | None:
    """`(client_id, client_secret)` que el tenant pegó para `key`, o `None` si
    todavía no configuró su propia app OAuth de este proveedor."""
    account = await find_oauth_app_account(repo, tenant_id, key)
    if account is None:
        return None
    bundle = await vault.get(tenant_id, account["id"])
    client_secret = (bundle.access_token or None) if bundle is not None else None
    return account["external_account_id"], client_secret


async def put_oauth_app_credentials(
    repo: Repo,
    vault: TokenVault,
    tenant_id: uuid.UUID,
    key: str,
    display_name: str,
    client_id: str,
    client_secret: str | None,
) -> None:
    """Upsert: si el tenant ya había configurado una app para `key`, la fila
    vieja se borra y se crea una nueva (no hay `update_connector_account` en
    `Repo` — ver protocolo en `repo.py` — así que reemplazar entero es más
    simple y evita dejar un `external_account_id` desactualizado si el tenant
    pega un `client_id` distinto al reconfigurar)."""
    existing = await find_oauth_app_account(repo, tenant_id, key)
    if existing is not None:
        await repo.delete_connector_account(tenant_id=tenant_id, account_id=existing["id"])
    account = await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=app_config_connector_key(key),
        external_account_id=client_id,
        display_name=f"App OAuth propia de {display_name}",
        scopes=[],
    )
    await vault.put(
        tenant_id,
        account["id"],
        TokenBundle(access_token=client_secret or "", token_type="oauth_app_secret", scopes=[key]),
    )


async def delete_oauth_app_credentials(repo: Repo, tenant_id: uuid.UUID, key: str) -> bool:
    account = await find_oauth_app_account(repo, tenant_id, key)
    if account is None:
        return False
    return await repo.delete_connector_account(tenant_id=tenant_id, account_id=account["id"])
