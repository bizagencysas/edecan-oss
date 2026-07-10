"""Proveedores de Ads (`ARCHITECTURE.md` §13, WP-V4-07 — flag de plan `tools.ads`).

`AdsProvider` es el protocolo intercambiable (mismo estilo `Protocol
runtime_checkable` que `edecan_creative.providers.ImageProvider`):
`StubAdsProvider` es determinista y 100% offline (proveedor por defecto,
ningún tenant conectó su cuenta de Meta todavía) y `MetaAdsProvider` habla
con la **Graph API oficial de Meta Marketing** (`https://graph.facebook.com/
v23.0`) usando el access token + `ad_account_id` **del tenant** — nunca una
credencial compartida de plataforma (Edecán no opera ninguna cuenta de Meta
propia).

## GUARDRAIL DE DINERO — el diseño gira alrededor de esto

`MetaAdsProvider.create_campaign_paused` es la ÚNICA función de todo el
paquete que puede crear algo en Meta, y lo crea **SIEMPRE en pausa**
(`status="PAUSED"`, hardcodeado como la última línea antes de armar el
body — pase lo que pase en `payload`, incluido un intento de colar
`status="ACTIVE"`, se pisa). Activar la campaña es una decisión humana que
se toma en el Ads Manager de Meta, nunca aquí. Ver `docs/ads.md`.

`get_tenant_ads_provider(ctx)` es la variante bring-your-own real (mismo
criterio "tenant → stub" que `edecan_creative.providers.
get_tenant_image_provider`, pero SIN el nivel intermedio de "config de
plataforma": Edecán nunca tiene una cuenta de Meta propia que ofrecer como
segundo nivel, así que la única alternativa a la credencial del tenant es el
stub): si el tenant conectó su cuenta (`PUT /v1/ads/credentials`,
`apps/api/edecan_api/routers/ads.py`, `TokenVault` connector_key
`ADS_CONNECTOR_KEY`), la usa; si no —o si falla cualquier paso de esa
resolución— cae a `StubAdsProvider`, nunca revienta.
"""

from __future__ import annotations

import hashlib
import json
import logging
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Protocol, runtime_checkable

import httpx
from sqlalchemy import text as sql_text

logger = logging.getLogger(__name__)

# `connector_key` del `TokenVault` para la credencial de Ads bring-your-own
# del tenant (pinned en el paquete de trabajo: 'Connector_key del vault:
# "ads"'). Definido acá y, por separado, en
# `apps/api/edecan_api/routers/ads.py` importado de aquí directo — a
# diferencia de `edecan_smarthome`/su router (que NO se importan entre sí),
# `edecan_api.routers.ads` SÍ depende de este paquete (necesita
# `get_tenant_ads_provider` para `/resumen` y `/borradores/{id}/confirmar`),
# así que no hace falta duplicar el string.
ADS_CONNECTOR_KEY = "ads"

META_GRAPH_BASE_URL = "https://graph.facebook.com/v23.0"
DEFAULT_DATE_PRESET = "last_30d"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Monedas de "offset cero" (sin decimales) según la tabla pública de Meta
# (developers.facebook.com/docs/marketing-api/currencies) — lista NO
# exhaustiva, cubre las más comunes. Para cualquier otra moneda no listada
# aquí se asume el caso general (2 decimales, `x100`). Un tenant que necesite
# un offset distinto (o quiera controlar el valor exacto enviado) puede
# incluir `daily_budget` directamente en el `payload` de
# `create_campaign_paused` — se respeta tal cual y no se recalcula (ver
# abajo).
_MONEDAS_SIN_DECIMALES = frozenset({"JPY", "KRW", "VND", "CLP", "PYG", "UGX", "XAF", "XOF"})


@runtime_checkable
class AdsProvider(Protocol):
    """Protocolo común de proveedor de plataformas de Ads."""

    async def list_campaigns(self) -> list[dict[str, Any]]:
        """Lista las campañas de la cuenta (`name`, `status`, `objective`, `daily_budget`)."""
        ...

    async def insights(self, date_preset: str = DEFAULT_DATE_PRESET) -> dict[str, Any]:
        """Métricas agregadas de la cuenta (`spend`, `impressions`, `clicks`, `cpc`, `ctr`)
        para la ventana `date_preset` (formato de Meta, p. ej. `"last_30d"`)."""
        ...

    async def create_campaign_paused(
        self,
        nombre: str,
        objetivo: str,
        presupuesto_diario: Decimal | float | int | None,
        moneda: str,
        payload: dict[str, Any] | None,
    ) -> str:
        """Crea la campaña **SIEMPRE en pausa** y devuelve su `external_id`.

        `payload` son campos adicionales de la campaña (p. ej.
        `special_ad_categories`, `bid_strategy`) que se mezclan en el cuerpo
        del request — salvo `status`, que esta función siempre fuerza a
        `"PAUSED"` sin excepción, ver el docstring del módulo.
        """
        ...


class MetaAdsError(RuntimeError):
    """Error al hablar con la Graph API de Meta — mensaje ya legible (extraído
    de `error.message` de la respuesta de Meta cuando está disponible, en vez
    de una excepción cruda de `httpx`)."""


def normalizar_ad_account_id(value: str) -> str:
    """Acepta el id de cuenta de anuncios con o sin el prefijo `"act_"` (Meta
    lo muestra con el prefijo en su UI, pero la Graph API lo exige por
    separado al armar la URL: `/act_{id}/...`) — siempre devuelve el id SIN
    el prefijo, para que quien arme la URL agregue `act_` una única vez."""
    limpio = str(value or "").strip()
    if limpio.lower().startswith("act_"):
        limpio = limpio[4:]
    return limpio


def _presupuesto_a_unidad_minima(monto: Decimal | float | int, moneda: str) -> str:
    """`daily_budget` de Meta va en la unidad MÍNIMA de la moneda de la cuenta
    (p. ej. centavos para USD) como string entero — ver la nota sobre
    `_MONEDAS_SIN_DECIMALES` arriba para el alcance de esta conversión."""
    decimal_monto = monto if isinstance(monto, Decimal) else Decimal(str(monto))
    factor = 1 if moneda.strip().upper() in _MONEDAS_SIN_DECIMALES else 100
    unidad_minima = (decimal_monto * factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return str(int(unidad_minima))


def _extraer_error_meta(response: httpx.Response) -> str:
    """Intenta extraer `error.message` (+ `error.code`) del cuerpo de error
    estándar de la Graph API (`{"error": {"message", "type", "code", ...}}`);
    si el cuerpo no es JSON o no trae ese shape, cae a un resumen genérico
    con el status y un fragmento del cuerpo crudo."""
    try:
        data = response.json()
    except ValueError:
        return f"Meta respondió {response.status_code}: {response.text[:300]}"
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict) and error.get("message"):
        codigo = error.get("code")
        sufijo = f" (código {codigo})" if codigo is not None else ""
        return f"{error['message']}{sufijo}"
    return f"Meta respondió {response.status_code}: {response.text[:300]}"


class StubAdsProvider:
    """Proveedor determinista y 100% offline — proveedor por defecto (ningún
    tenant conectó su cuenta de Meta todavía, o no quiere hacerlo). No hace
    ninguna llamada de red: `list_campaigns`/`insights` devuelven datos de
    ejemplo fijos y `create_campaign_paused` devuelve un id falso
    determinista (`sha256(nombre|objetivo|moneda)`) — nunca crea nada real en
    ninguna plataforma.
    """

    name = "stub"

    async def list_campaigns(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "stub-campaign-demo",
                "name": "Campaña de ejemplo (modo offline, sin cuenta de Meta conectada)",
                "status": "PAUSED",
                "objective": "OUTCOME_TRAFFIC",
                "daily_budget": None,
            }
        ]

    async def insights(self, date_preset: str = DEFAULT_DATE_PRESET) -> dict[str, Any]:
        return {
            "spend": "0",
            "impressions": "0",
            "clicks": "0",
            "cpc": "0",
            "ctr": "0",
            "date_preset": date_preset,
        }

    async def create_campaign_paused(
        self,
        nombre: str,
        objetivo: str,
        presupuesto_diario: Decimal | float | int | None,
        moneda: str,
        payload: dict[str, Any] | None,
    ) -> str:
        digest = hashlib.sha256(f"{nombre}|{objetivo}|{moneda}".encode()).hexdigest()[:16]
        return f"stub-campaign-{digest}"


class MetaAdsProvider:
    """Habla con la Graph API oficial de Meta Marketing (`META_GRAPH_BASE_URL`)
    usando el access token + `ad_account_id` **del tenant**. Se activa cuando
    el tenant conectó su cuenta vía `PUT /v1/ads/credentials` — nunca con una
    credencial compartida de la plataforma.
    """

    name = "meta"

    def __init__(
        self,
        access_token: str,
        ad_account_id: str,
        *,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._access_token = access_token
        self._ad_account_id = normalizar_ad_account_id(ad_account_id)
        self._client = http_client or httpx.AsyncClient(
            base_url=META_GRAPH_BASE_URL, timeout=timeout
        )

    async def aclose(self) -> None:
        """Cierra el cliente HTTP subyacente (pool de conexiones)."""
        await self._client.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        call_params = dict(params or {})
        call_params["access_token"] = self._access_token
        try:
            response = await self._client.request(method, path, params=call_params, json=json_body)
        except httpx.HTTPError as exc:
            raise MetaAdsError(f"No se pudo conectar con la Graph API de Meta: {exc}") from exc
        if response.status_code >= 400:
            raise MetaAdsError(_extraer_error_meta(response))
        try:
            return response.json()
        except ValueError as exc:
            raise MetaAdsError(
                "La Graph API de Meta devolvió una respuesta no-JSON inesperada."
            ) from exc

    async def list_campaigns(self) -> list[dict[str, Any]]:
        data = await self._request(
            "GET",
            f"/act_{self._ad_account_id}/campaigns",
            params={"fields": "name,status,objective,daily_budget"},
        )
        return list(data.get("data") or [])

    async def insights(self, date_preset: str = DEFAULT_DATE_PRESET) -> dict[str, Any]:
        data = await self._request(
            "GET",
            f"/act_{self._ad_account_id}/insights",
            params={"fields": "spend,impressions,clicks,cpc,ctr", "date_preset": date_preset},
        )
        rows = data.get("data") or []
        if rows:
            return dict(rows[0])
        return {
            "spend": "0",
            "impressions": "0",
            "clicks": "0",
            "cpc": "0",
            "ctr": "0",
            "date_preset": date_preset,
        }

    async def create_campaign_paused(
        self,
        nombre: str,
        objetivo: str,
        presupuesto_diario: Decimal | float | int | None,
        moneda: str,
        payload: dict[str, Any] | None,
    ) -> str:
        body: dict[str, Any] = dict(payload or {})
        body["name"] = nombre
        body["objective"] = objetivo
        body.setdefault("special_ad_categories", [])
        if presupuesto_diario is not None and "daily_budget" not in body:
            body["daily_budget"] = _presupuesto_a_unidad_minima(presupuesto_diario, moneda)
        # ---------------------------------------------------------------
        # GUARDRAIL DE DINERO — NUNCA NEGOCIABLE (ver docstring del módulo).
        # Última asignación, después de todo lo demás: pase lo que pase en
        # `payload` (incluido un intento de colar `status="ACTIVE"`), la
        # campaña SIEMPRE se crea en pausa. Activarla es una decisión humana
        # tomada en el Ads Manager de Meta, jamás de Edecán.
        # ---------------------------------------------------------------
        body["status"] = "PAUSED"

        data = await self._request("POST", f"/act_{self._ad_account_id}/campaigns", json_body=body)
        external_id = data.get("id")
        if not external_id:
            raise MetaAdsError("Meta no devolvió el 'id' de la campaña creada.")
        return str(external_id)


async def get_tenant_ads_provider(ctx: Any) -> AdsProvider:
    """`AdsProvider` bring-your-own del tenant, con fallback a
    `StubAdsProvider` (ver docstring del módulo — a diferencia de
    `edecan_creative.providers.get_tenant_image_provider`, NO hay un segundo
    nivel de "config de plataforma": Edecán nunca opera una cuenta de Meta
    propia).

    Lee `ctx.tenant_id`/`ctx.session`/`ctx.vault` de forma defensiva (`ctx` es
    `edecan_core.tools.ToolContext` en producción, pero un `Any` a propósito):
    si falta cualquiera de los tres, o el tenant nunca hizo
    `PUT /v1/ads/credentials`, o CUALQUIER paso falla (vault caído, JSON
    corrupto, faltan campos), se degrada a `StubAdsProvider` — nunca revienta
    `ads_resumen`/`ads_preparar_campana`/`GET /v1/ads/resumen` por esto, solo
    `logger.warning`.
    """
    tenant_id = getattr(ctx, "tenant_id", None)
    session = getattr(ctx, "session", None)
    vault = getattr(ctx, "vault", None)
    if tenant_id is None or session is None or vault is None:
        return StubAdsProvider()

    try:
        row = (
            await session.execute(
                sql_text(
                    "SELECT id FROM connector_accounts WHERE tenant_id = :tenant_id "
                    "AND connector_key = :connector_key ORDER BY created_at DESC LIMIT 1"
                ),
                {"tenant_id": tenant_id, "connector_key": ADS_CONNECTOR_KEY},
            )
        ).mappings().first()
        if row is None:
            logger.warning(
                "El tenant_id=%s no conectó su cuenta de Meta Ads (PUT /v1/ads/credentials) — "
                "uso StubAdsProvider.",
                tenant_id,
            )
            return StubAdsProvider()

        bundle = await vault.get(tenant_id=tenant_id, connector_account_id=row["id"])
        if bundle is None or not bundle.access_token:
            return StubAdsProvider()

        data = json.loads(bundle.access_token)
        access_token = data.get("access_token")
        ad_account_id = data.get("ad_account_id")
        if not (access_token and ad_account_id):
            return StubAdsProvider()
        return MetaAdsProvider(access_token=access_token, ad_account_id=ad_account_id)
    except Exception:
        logger.warning(
            "No se pudo resolver el AdsProvider bring-your-own del tenant_id=%s; "
            "uso StubAdsProvider.",
            tenant_id,
            exc_info=True,
        )
        return StubAdsProvider()
