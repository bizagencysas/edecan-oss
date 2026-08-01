"""`/v1/smarthome/*` — conector Home Assistant, bring-your-own (`ARCHITECTURE.md`
§12, §12.a, §12.b; `DIRECCION_ACTUAL.md`; WP-V3-12; `docs/casa-inteligente.md`).

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V3-01) lo monta de
forma defensiva, igual que el resto de routers v2/v3 (`importlib.import_module`
+ `try/except ImportError`, `V3_ROUTER_NAMES` ya incluye `"smarthome"`) — este
módulo solo declara `router`.

## Qué resuelve

Cada tenant pega la URL de SU PROPIA instancia de Home Assistant
(típicamente en su LAN, p. ej. `http://homeassistant.local:8123`) y un
**Long-Lived Access Token** generado en su perfil de Home Assistant. Mismo
patrón "pegar y validar" que `routers/credentials.py`
(`DIRECCION_ACTUAL.md` "Principio de UX no negociable"): `PUT
/v1/smarthome/credentials` acepta `validate: bool = True` (default) — si es
`true`, antes de guardar nada se hace un `GET {base_url}/api/` real con el
token para confirmar que sirve, y devuelve `400` con el detalle exacto si
Home Assistant lo rechaza (token inválido, host inalcanzable, etc.); nunca
se persiste una credencial sin probarla. `validate: false` es la escotilla
de escape (tests, o el propio dueño del proyecto sabiendo que está bien).

## Contrato del vault (`ARCHITECTURE.md` §12.b, pinned)

`connector_key = "homeassistant"` es **singleton por tenant** (una sola
`connector_account` por `(tenant_id, connector_key)`, a diferencia de un
conector OAuth que puede tener varias) — `_find_or_create_account` calca
exactamente el mismo helper de `routers/credentials.py` (busca la cuenta
existente y la reutiliza en vez de crear una nueva en cada `PUT`;
`external_account_id` se fija al propio `connector_key` porque no hay uno
natural, mismo criterio que "llm"/"voice_stt"/"voice_tts").

`TokenBundle.access_token` = el Long-Lived Access Token **tal cual** (nunca
envuelto en JSON, a diferencia de "llm"/"voice_stt"/"voice_tts" — ver
ARCHITECTURE.md §12.b); `TokenBundle.scopes[0]` = `base_url` (mismo patrón
que Twilio, `scopes=[ACCOUNT_SID]`, ver `routers/connectors.py`);
`token_type` queda en el default `"bearer"`.

## Por qué este router NO importa `edecan_smarthome`

El ping de validación de este router es, a propósito, un `httpx.AsyncClient`
puro y mínimo (una sola llamada `GET /api/`) local a este módulo — mismo
criterio que `routers/connectors.py` no importa `edecan_premium` para el
bloque de Twilio y duplica `_TWILIO_API_BASE` en vez de importarlo. Por eso
este router en sí nunca hace `from edecan_smarthome import ...`.

Eso es independiente de que `edecan_smarthome` (el paquete de tools, también
WP-V3-12) SÍ esté declarado como dependencia de `apps/api`
(`apps/api/pyproject.toml`, junto a `edecan-skills`/`edecan-business`) — ahí
el motivo es otro: `edecan_smarthome` expone sus 3 herramientas del agente
(`casa_dispositivos`/`casa_estado`/`casa_controlar`,
`packages/smarthome/edecan_smarthome/tools.py`) SOLO vía el entry point
`edecan.tools`, que `ToolRegistry.load_entry_points` resuelve con
`importlib.metadata.entry_points()` sobre lo que esté REALMENTE instalado —
sin importar si algún módulo lo importa o no. Sin declararlo,
`infra/docker/Dockerfile.api` (`uv sync --no-dev --package edecan-api`) no lo
instalaría, y el agente quedaría sin esas 3 tools en cualquier imagen de
producción EN SILENCIO: este mismo endpoint (`PUT /v1/smarthome/credentials`)
seguiría dejando "conectar" sin ningún error, porque valida contra Home
Assistant directo, no contra el `ToolRegistry`. Ver el comentario junto a
`"edecan-smarthome"` en `apps/api/pyproject.toml` para el detalle completo.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any
from urllib.parse import urlsplit

import httpx
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from edecan_api.deps import CurrentUser, get_current_user, get_repo, get_vault, rate_limit
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1/smarthome", tags=["smarthome"], dependencies=[Depends(rate_limit)]
)

# Clave EXACTA pinned en ARCHITECTURE.md §12.b — `edecan_smarthome.tools`
# (paquete hermano, no importado aquí, ver docstring del módulo) usa la MISMA
# cadena literal para resolver las credenciales desde `ctx.vault`.
HOMEASSISTANT_CONNECTOR_KEY = "homeassistant"
_DISPLAY_NAME = "Home Assistant"

_ESQUEMAS_PERMITIDOS = frozenset({"http", "https"})
_VALIDATE_TIMEOUT_SECONDS = 15.0
_STATUS_PING_TIMEOUT_SECONDS = 5.0


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class SmarthomeCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    base_url: str
    token: str
    validate_: bool = Field(default=True, alias="validate")


class SmarthomeStatusOut(BaseModel):
    configured: bool
    base_url: str | None
    reachable: bool | None


# ---------------------------------------------------------------------------
# Validación de formato de base_url — SSRF deliberadamente INVERTIDA respecto
# a `edecan_browser.policy` (ver `edecan_smarthome.client`, mismo criterio
# duplicado aquí a propósito): Home Assistant vive en la LAN del usuario por
# diseño, así que una IP privada o un hostname ".local" es el caso NORMAL, no
# se bloquea. Solo se rechaza esquema no-http(s) o credenciales embebidas.
# ---------------------------------------------------------------------------


def _normalizar_base_url(base_url: str) -> str:
    return base_url.strip().rstrip("/")


def _validar_formato(base_url: str) -> None:
    partes = urlsplit(base_url)
    if partes.scheme.lower() not in _ESQUEMAS_PERMITIDOS or not partes.hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"«{base_url}» no es una URL http/https válida. Usa algo como "
                "'http://homeassistant.local:8123' o 'http://192.168.1.50:8123'."
            ),
        )
    if partes.username or partes.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "La URL no debe incluir credenciales embebidas (usuario:contraseña@host) — "
                "el token va aparte, nunca en la URL."
            ),
        )


# ---------------------------------------------------------------------------
# Ping de validación (PUT, "pegar y validar") — detalle exacto en el 400.
# ---------------------------------------------------------------------------


async def _ping_home_assistant(base_url: str, token: str, *, timeout: float) -> None:
    url = f"{base_url}/api/"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"No pudimos conectar con tu Home Assistant en '{base_url}': {exc}. ¿Está "
                "encendido y alcanzable desde donde corre Edecán?"
            ),
        ) from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Home Assistant rechazó el token (401) — revisa el Long-Lived Access Token.",
        )
    if response.status_code != 200:
        snippet = response.text[:300]
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Home Assistant respondió {response.status_code} en /api/: {snippet}",
        )


async def _probe_reachable(base_url: str, token: str, *, timeout: float) -> bool | None:
    """Sonda liviana para `GET /v1/smarthome/status`: `True` si `GET
    {base_url}/api/` responde 200 con `token`, `False` si responde con
    cualquier otro status (p. ej. token vencido), `None` si la red falla del
    todo (timeout, conexión rechazada, DNS) — NUNCA lanza, así este endpoint
    jamás puede responder 500 por un Home Assistant apagado o inalcanzable.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(
                f"{base_url}/api/", headers={"Authorization": f"Bearer {token}"}
            )
    except httpx.HTTPError:
        return None
    return response.status_code == 200


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` (singleton por tenant, ver docstring del
# módulo) — mismo patrón que `_find_account`/`_find_or_create_account` de
# `routers/credentials.py`, duplicado a propósito (paquetes hermanos no se
# importan entre routers).
# ---------------------------------------------------------------------------


async def _find_account(
    repo: Repo, tenant_id: uuid.UUID, connector_key: str
) -> dict[str, Any] | None:
    accounts = await repo.list_connector_accounts(tenant_id=tenant_id)
    matches = [a for a in accounts if a["connector_key"] == connector_key]
    if not matches:
        return None
    return min(matches, key=lambda a: a["created_at"])


async def _find_or_create_account(repo: Repo, tenant_id: uuid.UUID) -> dict[str, Any]:
    existing = await _find_account(repo, tenant_id, HOMEASSISTANT_CONNECTOR_KEY)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=HOMEASSISTANT_CONNECTOR_KEY,
        external_account_id=HOMEASSISTANT_CONNECTOR_KEY,
        display_name=_DISPLAY_NAME,
        scopes=[],
    )


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/smarthome/credentials, GET /v1/smarthome/status
# ---------------------------------------------------------------------------


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_credentials(
    payload: SmarthomeCredentialsIn,
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    base_url = _normalizar_base_url(payload.base_url)
    token = payload.token.strip()
    _validar_formato(base_url)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El Long-Lived Access Token no puede estar vacío.",
        )

    if payload.validate_:
        await _ping_home_assistant(base_url, token, timeout=_VALIDATE_TIMEOUT_SECONDS)

    account = await _find_or_create_account(repo, current_user.tenant_id)
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=token, scopes=[base_url]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="smarthome.connected",
        target=HOMEASSISTANT_CONNECTOR_KEY,
    )


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, HOMEASSISTANT_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="smarthome.disconnected",
        target=HOMEASSISTANT_CONNECTOR_KEY,
    )


@router.get("/status", response_model=SmarthomeStatusOut)
async def get_status(
    current_user: CurrentUser = Depends(get_current_user),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> SmarthomeStatusOut:
    account = await _find_account(repo, current_user.tenant_id, HOMEASSISTANT_CONNECTOR_KEY)
    if account is None:
        return SmarthomeStatusOut(configured=False, base_url=None, reachable=None)

    bundle = await vault.get(current_user.tenant_id, account["id"])
    if bundle is None or not bundle.access_token or not bundle.scopes:
        return SmarthomeStatusOut(configured=False, base_url=None, reachable=None)

    base_url = bundle.scopes[0]
    reachable = await _probe_reachable(
        base_url, bundle.access_token, timeout=_STATUS_PING_TIMEOUT_SECONDS
    )
    return SmarthomeStatusOut(configured=True, base_url=base_url, reachable=reachable)
