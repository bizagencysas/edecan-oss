"""`/v1/ads/*` — Ads: Meta Marketing API oficial, bring-your-own, con el
guardrail de dinero como centro del diseño (`ARCHITECTURE.md` §13,
`DIRECCION_ACTUAL.md`, WP-V4-07; ver `docs/ads.md` para el flujo completo).

Este router NO se monta a sí mismo: `edecan_api.main` (WP-V4-01) ya lo monta
de forma defensiva (`V4_ROUTER_NAMES` incluye `"ads"`, mismo patrón que v2/v3
— `importlib.import_module` + `try/except ImportError` + `logger.warning` si
falta) — este módulo solo declara `router`.

## Qué resuelve

Cada tenant pega su propio **access token** de la Graph API de Meta (de SU
PROPIA app en developers.facebook.com, con permisos `ads_management`/
`ads_read`) + el **id de SU cuenta de anuncios** (`ad_account_id`, con o sin
el prefijo `"act_"` — ver `edecan_ads.providers.normalizar_ad_account_id`).
Mismo patrón "pegar y validar" que `routers/smarthome.py`
(`DIRECCION_ACTUAL.md` "Principio de UX no negociable"): `PUT
/v1/ads/credentials` acepta `validate: bool = True` (default) — si es
`true`, antes de guardar nada se hace `GET /me` + `GET /act_{id}?fields=
name,currency` reales contra la Graph API para confirmar que el token y la
cuenta sirven, y devuelve `400` con el detalle EXACTO que dio Meta si algo
falla; nunca se persiste una credencial sin probarla. `validate: false` es
la escotilla de escape (tests, o el propio dueño del proyecto sabiendo que
está bien).

## Contrato del vault

`connector_key = "ads"` (`edecan_ads.providers.ADS_CONNECTOR_KEY`) es
**singleton por tenant** (una sola `connector_account` por
`(tenant_id, connector_key)`, igual que `"homeassistant"`/`"llm"`/
`"images"`/`"search"`) — `_find_or_create_account` calca el mismo helper de
`routers/smarthome.py`/`routers/credentials.py`. `TokenBundle.access_token`
guarda el JSON `{"access_token", "ad_account_id"}` (`token_type="config"`,
mismo criterio que `credentials.py` con LLM/voz/imágenes/búsqueda — NO es un
token OAuth real, es una config que hay que `json.loads()`).

## Guardrail de dinero (el corazón de este router)

`POST /borradores/{id}/confirmar` es la ÚNICA ruta de toda la API que puede
crear algo en la cuenta de Meta del tenant, y la campaña se crea **SIEMPRE
en pausa** (`edecan_ads.providers.MetaAdsProvider.create_campaign_paused`
hardcodea `status="PAUSED"` sin excepción, ver ese módulo). La respuesta de
esta ruta SIEMPRE aclara que la campaña quedó pausada y que activarla es una
decisión del humano en el Ads Manager de Meta — Edecán jamás activa gasto.
`ads_preparar_campana` (`edecan_ads.tools`, `dangerous=True`) es el ÚNICO
punto de entrada para crear un borrador, y JAMÁS llama a Meta — solo
inserta `ad_drafts(status='draft')`; el push real solo ocurre aquí, cuando
el humano confirma explícitamente en la UI. Doble gate, mismo criterio que
`docs/dinero-real.md` (commerce): confirmación del *tool call* en el chat +
confirmación del borrador en esta página.

## `POST /borradores/{id}/confirmar` — commit de evidencia antes de `raise`

Mismo guardrail que `HOTFIXES_PENDIENTES.md` puntos 8/9
(`edecan_api.routers.commerce.confirm_order`, `edecan_api.routers.
remote.get_frame`): `get_tenant_session` envuelve TODA la request en una
única transacción con rollback automático si una excepción se propaga fuera
del handler. Si `create_campaign_paused` falla (Meta rechaza el request,
red caída, etc.) DESPUÉS de que el borrador ya pasó a `status='confirmed'`
+ su audit log, un `raise` ingenuo en ese punto se llevaría puesta esa
evidencia — el usuario confirmó, pero no quedaría ningún rastro. Por eso,
en la rama de error, el handler deja constancia (`status='error'`
+ `error=<mensaje>` + audit `ads.draft.error`) y hace `await
session.commit()` como ÚLTIMA operación sobre la sesión, justo antes del
`raise HTTPException(502, ...)` — nunca antes, nunca después (seguir usando
la sesión tras un commit manual, dentro del mismo `async with
session.begin()` de `edecan_db.session.get_session`, revienta con
`InvalidRequestError`, ver el docstring de `confirm_order` para el detalle
verificado empíricamente). En el camino feliz no hace falta ningún commit
explícito: nada vuelve a lanzar después de las escrituras, así que el commit
implícito de `get_session` al terminar la request ya las persiste.
"""

from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx
from edecan_ads.providers import (
    ADS_CONNECTOR_KEY,
    META_GRAPH_BASE_URL,
    get_tenant_ads_provider,
    normalizar_ad_account_id,
)
from edecan_core import ToolContext
from edecan_db.vault import TokenVault
from edecan_schemas import TokenBundle
from edecan_schemas.plans import FLAG_TOOLS_ADS
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from edecan_api.config import Settings, get_settings
from edecan_api.deps import (
    CurrentUser,
    get_current_user,
    get_repo,
    get_tenant_session,
    get_vault,
    rate_limit,
)
from edecan_api.repo import Repo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ads", tags=["ads"], dependencies=[Depends(rate_limit)])

_DISPLAY_NAME = "Meta Ads"

_VALIDATE_TIMEOUT_SECONDS = 15.0
_STATUS_PING_TIMEOUT_SECONDS = 5.0
_ESTADOS_CANCELABLES = frozenset({"draft", "confirmed", "error"})


# ---------------------------------------------------------------------------
# Gate de flag de plan — sustituye a `get_current_user` en cada ruta (mismo
# patrón que `edecan_api.routers.commerce._require_commerce_orders`).
# ---------------------------------------------------------------------------


async def _require_tools_ads(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    if not current_user.tenant.flags.get(FLAG_TOOLS_ADS, False):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Ads (Meta) no está disponible en tu plan.",
        )
    return current_user


# ---------------------------------------------------------------------------
# Bodies / respuestas
# ---------------------------------------------------------------------------


class AdsCredentialsIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    access_token: str
    ad_account_id: str
    validate_: bool = Field(default=True, alias="validate")


class AdsStatusOut(BaseModel):
    configured: bool
    ad_account_id: str | None
    nombre_cuenta: str | None
    moneda: str | None
    reachable: bool | None


# ---------------------------------------------------------------------------
# Ping de validación (PUT, "pegar y validar") + sonda de `/status` — ambos
# `httpx.AsyncClient` puros y locales a este módulo (mismo criterio que
# `routers/smarthome.py::_ping_home_assistant`): no reusan
# `edecan_ads.providers.MetaAdsProvider` porque ese proveedor no expone un
# método de validación (su Protocol pinned son solo los 3 métodos que
# necesitan `ads_resumen`/`ads_preparar_campana`).
# ---------------------------------------------------------------------------


def _detalle_error_meta(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return f"Meta respondió {response.status_code}."
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        return str(error["message"])
    return f"Meta respondió {response.status_code}."


async def _ping_meta(access_token: str, ad_account_id: str, *, timeout: float) -> None:
    try:
        async with httpx.AsyncClient(base_url=META_GRAPH_BASE_URL, timeout=timeout) as client:
            me_response = await client.get("/me", params={"access_token": access_token})
            if me_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Meta rechazó el access token: {_detalle_error_meta(me_response)}",
                )

            account_response = await client.get(
                f"/act_{ad_account_id}",
                params={"fields": "name,currency", "access_token": access_token},
            )
            if account_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"Meta rechazó la cuenta de anuncios 'act_{ad_account_id}': "
                        f"{_detalle_error_meta(account_response)}"
                    ),
                )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No pudimos conectar con la Graph API de Meta: {exc}",
        ) from exc


async def _probe_reachable(
    access_token: str, ad_account_id: str, *, timeout: float
) -> tuple[bool | None, str | None, str | None]:
    """Sonda liviana para `GET /v1/ads/status`: `(True, name, currency)` si
    `GET /act_{id}` responde 200, `(False, None, None)` si responde con
    cualquier otro status (p. ej. token vencido), `(None, None, None)` si la
    red falla del todo — NUNCA lanza, así este endpoint jamás responde 500
    por Meta caído o un token vencido."""
    try:
        async with httpx.AsyncClient(base_url=META_GRAPH_BASE_URL, timeout=timeout) as client:
            response = await client.get(
                f"/act_{ad_account_id}",
                params={"fields": "name,currency", "access_token": access_token},
            )
    except httpx.HTTPError:
        return None, None, None
    if response.status_code != 200:
        return False, None, None
    data = response.json()
    return True, data.get("name"), data.get("currency")


# ---------------------------------------------------------------------------
# Helpers de `connector_accounts` (singleton por tenant) — mismo patrón que
# `_find_account`/`_find_or_create_account` de `routers/smarthome.py`/
# `routers/credentials.py`, duplicado a propósito (paquetes hermanos no se
# importan entre routers para esta parte).
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
    existing = await _find_account(repo, tenant_id, ADS_CONNECTOR_KEY)
    if existing is not None:
        return existing
    return await repo.create_connector_account(
        tenant_id=tenant_id,
        connector_key=ADS_CONNECTOR_KEY,
        external_account_id=ADS_CONNECTOR_KEY,
        display_name=_DISPLAY_NAME,
        scopes=[],
    )


def _build_ads_ctx(
    current_user: CurrentUser, session: Any, vault: Any, settings: Any
) -> ToolContext:
    """`ToolContext` liviano para invocar `get_tenant_ads_provider(ctx)` desde
    un endpoint HTTP (fuera de un turno de chat) — mismo shape que
    `edecan_api.routers.conversations._build_ctx`, con `llm=None` y `extras={}`
    porque `GET /resumen`/`confirmar` no necesitan ninguna otra pieza."""
    return ToolContext(
        tenant_id=current_user.tenant_id,
        user_id=current_user.user_id,
        session=session,
        settings=settings,
        llm=None,
        vault=vault,
        extras={},
    )


# ---------------------------------------------------------------------------
# Helpers SQL de `ad_drafts` — parametrizado directo (mismo criterio que
# `edecan_api.routers.commerce`: el contrato pinnea tabla/columna, no una
# forma de ORM; este router no importa `edecan_db.models`).
# ---------------------------------------------------------------------------


async def _first(session: AsyncSession, stmt: str, params: dict[str, Any]) -> dict[str, Any] | None:
    result = await session.execute(text(stmt), params)
    row = result.mappings().first()
    return dict(row) if row is not None else None


async def _all(session: AsyncSession, stmt: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    result = await session.execute(text(stmt), params)
    return [dict(row) for row in result.mappings().all()]


def _from_jsonb(value: Any) -> dict[str, Any]:
    """`payload` puede llegar como `dict` ya decodificado o como texto JSON
    crudo según el driver — mismo criterio defensivo que
    `edecan_api.routers.commerce._from_jsonb`/`edecan_toolkit.contactos.
    _desde_jsonb`, duplicado a propósito."""
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            cargado = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return cargado if isinstance(cargado, dict) else {}
    return {}


def _row_to_draft(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    out["payload"] = _from_jsonb(out.get("payload"))
    return out


async def _audit(
    session: AsyncSession,
    *,
    tenant_id: uuid.UUID,
    actor_user_id: uuid.UUID,
    action: str,
    target: str,
    meta: dict[str, Any],
) -> None:
    await session.execute(
        text(
            "INSERT INTO audit_log (tenant_id, actor_user_id, action, target, meta) "
            "VALUES (:tenant_id ::uuid, :actor_user_id ::uuid, :action, :target, "
            "CAST(:meta AS jsonb))"
        ),
        {
            "tenant_id": str(tenant_id),
            "actor_user_id": str(actor_user_id),
            "action": action,
            "target": target,
            "meta": json.dumps(meta),
        },
    )


async def _get_draft_or_404(
    session: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID, draft_id: uuid.UUID
) -> dict[str, Any]:
    row = await _first(
        session,
        "SELECT * FROM ad_drafts WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid "
        "AND user_id = :user_id ::uuid",
        {"id": str(draft_id), "tenant_id": str(tenant_id), "user_id": str(user_id)},
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borrador no encontrado.")
    return row


# ---------------------------------------------------------------------------
# PUT/DELETE /v1/ads/credentials, GET /v1/ads/status
# ---------------------------------------------------------------------------


@router.put("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def put_credentials(
    payload: AdsCredentialsIn,
    current_user: CurrentUser = Depends(_require_tools_ads),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> None:
    access_token = payload.access_token.strip()
    ad_account_id = normalizar_ad_account_id(payload.ad_account_id)
    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="El access token no puede estar vacío."
        )
    if not ad_account_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El ID de la cuenta de anuncios no puede estar vacío.",
        )

    if payload.validate_:
        await _ping_meta(access_token, ad_account_id, timeout=_VALIDATE_TIMEOUT_SECONDS)

    account = await _find_or_create_account(repo, current_user.tenant_id)
    config = {"access_token": access_token, "ad_account_id": ad_account_id}
    await vault.put(
        current_user.tenant_id,
        account["id"],
        TokenBundle(access_token=json.dumps(config), token_type="config", scopes=["meta"]),
    )
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="ads.connected",
        target=ADS_CONNECTOR_KEY,
    )


@router.delete("/credentials", status_code=status.HTTP_204_NO_CONTENT)
async def delete_credentials(
    current_user: CurrentUser = Depends(_require_tools_ads),
    repo: Repo = Depends(get_repo),
) -> None:
    account = await _find_account(repo, current_user.tenant_id, ADS_CONNECTOR_KEY)
    if account is None:
        return  # idempotente: nada que borrar ya es un estado válido de "desconectado".
    await repo.delete_connector_account(tenant_id=current_user.tenant_id, account_id=account["id"])
    await repo.add_audit_log(
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="ads.disconnected",
        target=ADS_CONNECTOR_KEY,
    )


@router.get("/status", response_model=AdsStatusOut)
async def get_status(
    current_user: CurrentUser = Depends(_require_tools_ads),
    repo: Repo = Depends(get_repo),
    vault: TokenVault = Depends(get_vault),
) -> AdsStatusOut:
    account = await _find_account(repo, current_user.tenant_id, ADS_CONNECTOR_KEY)
    if account is None:
        return AdsStatusOut(
            configured=False, ad_account_id=None, nombre_cuenta=None, moneda=None, reachable=None
        )

    bundle = await vault.get(current_user.tenant_id, account["id"])
    if bundle is None or not bundle.access_token:
        return AdsStatusOut(
            configured=False, ad_account_id=None, nombre_cuenta=None, moneda=None, reachable=None
        )

    try:
        config = json.loads(bundle.access_token)
        access_token = config["access_token"]
        ad_account_id = config["ad_account_id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return AdsStatusOut(
            configured=False, ad_account_id=None, nombre_cuenta=None, moneda=None, reachable=None
        )

    reachable, nombre_cuenta, moneda = await _probe_reachable(
        access_token, ad_account_id, timeout=_STATUS_PING_TIMEOUT_SECONDS
    )
    return AdsStatusOut(
        configured=True,
        ad_account_id=ad_account_id,
        nombre_cuenta=nombre_cuenta,
        moneda=moneda,
        reachable=reachable,
    )


# ---------------------------------------------------------------------------
# GET /v1/ads/resumen — proveedor del tenant (real vía Meta, o Stub offline).
# ---------------------------------------------------------------------------


@router.get("/resumen")
async def get_resumen(
    periodo: str = Query(default="last_30d"),
    current_user: CurrentUser = Depends(_require_tools_ads),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    ctx = _build_ads_ctx(current_user, session, vault, settings)
    provider = await get_tenant_ads_provider(ctx)
    try:
        campanas = await provider.list_campaigns()
        metricas = await provider.insights(periodo)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo consultar Meta Ads: {exc}",
        ) from exc
    return {"campanas": campanas, "metricas": metricas, "periodo": periodo}


# ---------------------------------------------------------------------------
# Borradores (`ad_drafts`) — listar, confirmar (con el guardrail de dinero),
# cancelar.
# ---------------------------------------------------------------------------


@router.get("/borradores")
async def list_borradores(
    current_user: CurrentUser = Depends(_require_tools_ads),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, Any]]:
    rows = await _all(
        session,
        "SELECT * FROM ad_drafts WHERE tenant_id = :tenant_id ::uuid AND user_id = :user_id ::uuid "
        "ORDER BY created_at DESC",
        {"tenant_id": str(current_user.tenant_id), "user_id": str(current_user.user_id)},
    )
    return [_row_to_draft(r) for r in rows]


@router.post("/borradores/{draft_id}/confirmar")
async def confirmar_borrador(
    draft_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_tools_ads),
    session: AsyncSession = Depends(get_tenant_session),
    vault: TokenVault = Depends(get_vault),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    """`draft -> confirmed -> pushed` (o `-> error` si Meta rechaza el push) —
    ver el docstring del módulo para el guardrail de "commit de evidencia
    antes de `raise`" (`HOTFIXES_PENDIENTES.md` puntos 8/9)."""
    draft = await _get_draft_or_404(
        session, current_user.tenant_id, current_user.user_id, draft_id
    )
    if draft["status"] != "draft":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"El borrador está en estado '{draft['status']}'; solo se puede confirmar uno "
                "en estado 'draft'."
            ),
        )

    confirmado = await _first(
        session,
        "UPDATE ad_drafts SET status = 'confirmed', confirmed_at = now(), updated_at = now() "
        "WHERE id = :id ::uuid RETURNING *",
        {"id": str(draft_id)},
    )
    if confirmado is None:  # defensivo: ya validamos que el borrador existe arriba.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borrador no encontrado.")
    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="ads.draft.confirmed",
        target=str(draft_id),
        meta={},
    )

    ctx = _build_ads_ctx(current_user, session, vault, settings)
    provider = await get_tenant_ads_provider(ctx)
    payload = _from_jsonb(confirmado.get("payload"))
    presupuesto_diario: Decimal | None = confirmado.get("presupuesto_diario")

    try:
        external_id = await provider.create_campaign_paused(
            confirmado["nombre"],
            confirmado["objetivo"],
            presupuesto_diario,
            confirmado["moneda"],
            payload,
        )
    except Exception as exc:
        # Última operación de sesión de esta rama: registra la falla en la MISMA
        # transacción (todavía sin comitear) y comitea TODO junto (confirmación +
        # su audit + el error) antes de lanzar — ver el docstring del módulo
        # (HOTFIXES_PENDIENTES.md puntos 8/9).
        mensaje_error = str(exc)[:2000]
        await session.execute(
            text(
                "UPDATE ad_drafts SET status = 'error', error = :error, updated_at = now() "
                "WHERE id = :id ::uuid"
            ),
            {"id": str(draft_id), "error": mensaje_error},
        )
        await _audit(
            session,
            tenant_id=current_user.tenant_id,
            actor_user_id=current_user.user_id,
            action="ads.draft.error",
            target=str(draft_id),
            meta={"error": mensaje_error},
        )
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"No se pudo crear la campaña en Meta: {exc}",
        ) from exc

    pusheado = await _first(
        session,
        "UPDATE ad_drafts SET status = 'pushed', external_id = :external_id, "
        "pushed_at = now(), updated_at = now() WHERE id = :id ::uuid RETURNING *",
        {"id": str(draft_id), "external_id": external_id},
    )
    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="ads.draft.pushed",
        target=str(draft_id),
        meta={"external_id": external_id},
    )

    return {
        "borrador": _row_to_draft(pusheado) if pusheado else None,
        "mensaje": (
            f"Campaña creada en Meta (id {external_id}), SIEMPRE en pausa (PAUSED). "
            "Actívala tú desde el Ads Manager de Meta cuando quieras que empiece a gastar — "
            "Edecán nunca activa gasto."
        ),
    }


@router.post("/borradores/{draft_id}/cancelar")
async def cancelar_borrador(
    draft_id: uuid.UUID,
    current_user: CurrentUser = Depends(_require_tools_ads),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    draft = await _get_draft_or_404(
        session, current_user.tenant_id, current_user.user_id, draft_id
    )
    if draft["status"] not in _ESTADOS_CANCELABLES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"No se puede cancelar un borrador en estado '{draft['status']}'.",
        )

    cancelado = await _first(
        session,
        "UPDATE ad_drafts SET status = 'cancelled', updated_at = now() WHERE id = :id ::uuid "
        "RETURNING *",
        {"id": str(draft_id)},
    )
    if cancelado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Borrador no encontrado.")
    await _audit(
        session,
        tenant_id=current_user.tenant_id,
        actor_user_id=current_user.user_id,
        action="ads.draft.cancelled",
        target=str(draft_id),
        meta={},
    )
    return {"borrador": _row_to_draft(cancelado), "mensaje": "Borrador cancelado."}
