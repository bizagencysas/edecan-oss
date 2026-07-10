"""Job `sync_connector`: refresca tokens OAuth que expiran en menos de 10
minutos, usando `edecan_connectors.registry.CONNECTORS[connector_key].refresh`
con el `TokenBundle` que guarda el `TokenVault` (ARCHITECTURE.md §10.4, §10.8,
§10.11).

Si `env.tenant_id` viene definido, solo mira los tokens de ese tenant; si es
`None`, es un barrido global (igual que `send_reminder_scan` — job de
sistema disparado periódicamente). Cada token se procesa en su propio
`try/except`: un fallo puntual (p. ej. `refresh_token` revocado, conector
desconocido) se loggea y NO detiene el resto — se reintentará en la próxima
corrida de `sync_connector`, no vía el backoff de reintentos de jobs.

`client_id`/`client_secret` (2026-07-09): cada `Connector.refresh` ahora los
exige como parámetro explícito -- son la app OAuth PROPIA de CADA TENANT, ver
`apps/api/edecan_api/oauth_app_credentials.py` (duplicado a propósito acá,
`_credenciales_app_oauth`, en vez de importar ese módulo de `apps/api`:
`ARCHITECTURE.md` §10.1, apps distintas no se importan entre sí). Se buscan
en `connector_accounts` bajo la fila reservada `"{connector_key}__app_config"`
para el MISMO tenant cuyo token se está refrescando -- si el tenant borró esa
config después de conectar la cuenta, el refresh falla con un mensaje claro
en vez de un `TypeError` por argumento faltante.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from edecan_connectors.registry import CONNECTORS
from edecan_schemas import JobEnvelope
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_worker.deps import Deps
from edecan_worker.repo import SqlRepo

logger = logging.getLogger(__name__)

VENTANA_EXPIRACION = timedelta(minutes=10)

_APP_CONFIG_SUFFIX = "__app_config"


async def _credenciales_app_oauth(
    deps: Deps, session: AsyncSession, tenant_id: Any, connector_key: str
) -> tuple[str, str | None] | None:
    """Duplicado deliberado de `edecan_api.oauth_app_credentials.
    get_oauth_app_credentials` -- ver docstring del módulo."""
    repo = SqlRepo(session)
    account = await repo.get_connector_account_by_key(
        tenant_id=tenant_id, connector_key=f"{connector_key}{_APP_CONFIG_SUFFIX}"
    )
    if account is None:
        return None
    vault = deps.vault(session)
    bundle = await vault.get(tenant_id=tenant_id, connector_account_id=account["id"])
    client_secret = (bundle.access_token or None) if bundle is not None else None
    return account["external_account_id"], client_secret


async def handle(env: JobEnvelope, deps: Deps) -> None:
    threshold = datetime.now(UTC) + VENTANA_EXPIRACION

    async with deps.session_factory(None) as session:
        repo = SqlRepo(session)
        expiring = await repo.list_expiring_oauth_tokens(tenant_id=env.tenant_id, before=threshold)

        logger.info(
            "sync_connector: %d token(s) por expirar en los próximos 10 minutos", len(expiring)
        )

        async with httpx.AsyncClient() as http:
            for row in expiring:
                await _refresh_one(deps, session, http, row)


async def _refresh_one(
    deps: Deps, session: AsyncSession, http: httpx.AsyncClient, row: dict[str, Any]
) -> None:
    tenant_id = row["tenant_id"]
    connector_account_id = row["connector_account_id"]
    connector_key = row["connector_key"]

    try:
        connector = CONNECTORS.get(connector_key)
        if connector is None:
            logger.warning(
                "sync_connector: conector desconocido %r (cuenta=%s tenant=%s)",
                connector_key,
                connector_account_id,
                tenant_id,
            )
            return

        vault = deps.vault(session)
        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=connector_account_id)
        if bundle is None:
            logger.warning(
                "sync_connector: sin credenciales en el vault para cuenta=%s tenant=%s",
                connector_account_id,
                tenant_id,
            )
            return

        creds = await _credenciales_app_oauth(deps, session, tenant_id, connector_key)
        if creds is None:
            logger.warning(
                "sync_connector: el tenant=%s ya no tiene configurada su app OAuth propia "
                "de %r; no se puede refrescar la cuenta=%s (deberá reautorizar).",
                tenant_id,
                connector_key,
                connector_account_id,
            )
            return
        client_id, client_secret = creds

        refreshed = await connector.refresh(
            bundle, http, client_id=client_id, client_secret=client_secret
        )
        await vault.put(
            tenant_id=tenant_id, connector_account_id=connector_account_id, bundle=refreshed
        )
        logger.info(
            "sync_connector: token refrescado conector=%s cuenta=%s tenant=%s",
            connector_key,
            connector_account_id,
            tenant_id,
        )
    except Exception:
        logger.exception(
            "sync_connector: fallo refrescando conector=%s cuenta=%s tenant=%s",
            connector_key,
            connector_account_id,
            tenant_id,
        )
