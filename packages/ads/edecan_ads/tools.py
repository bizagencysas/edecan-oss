"""Las 2 herramientas de `edecan_ads` (nombres exactos, pinned en el paquete de
trabajo WP-V4-07): `ads_resumen` (solo lectura) y `ads_preparar_campana`
(`dangerous=True`).

Ambas requieren el flag de plan `tools.ads` (`FLAG_TOOLS_ADS`, pinned en
`edecan_schemas.plans.PLANES` por WP-V4-01: `True` en
`free_selfhost`/`hosted_pro`/`hosted_business`, `False` en `hosted_basic`) —
usado aquí como string local (`_FLAG_ADS`, no importado de
`edecan_schemas.plans`) porque este paquete no declara `edecan-schemas` como
dependencia (solo `edecan-core`/`httpx`/`sqlalchemy`, ver `pyproject.toml`,
que WP-V4-07 no puede tocar). Mismo patrón que ya usa este mismo repo aunque
la dependencia SÍ esté disponible: `edecan_browser.tools._FLAG_BROWSER`/el
literal `"tools.images"` de `edecan_creative.tools.GenerarImagenTool` (ambos
paquetes SÍ declaran `edecan-schemas` y aun así usan el string a mano,
`ARCHITECTURE.md` §10.1). La cadena coincide exactamente con la del router
(`apps/api/edecan_api/routers/ads.py`, que sí importa `FLAG_TOOLS_ADS`
directo — `edecan_api` sí depende de `edecan-schemas`).

`ads_preparar_campana` es el ÚNICO lugar de este paquete donde una tool
`dangerous=True` toca la base de datos, y lo único que hace es un `INSERT`
en `ad_drafts` con `status='draft'` — JAMÁS llama a Meta ni a
`edecan_ads.providers` (ver `docs/ads.md`, "guardrail de dinero"): el push
real a Meta (con la campaña SIEMPRE en pausa) solo ocurre cuando el humano
confirma el borrador en la UI (`POST /v1/ads/borradores/{id}/confirmar`,
`apps/api/edecan_api/routers/ads.py`) — doble gate, mismo criterio que
`edecan_commerce.tools.PrepararPagoTool`/`PrepararOrdenTool` con
`docs/dinero-real.md`.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any

from edecan_core import Tool, ToolContext, ToolResult
from sqlalchemy import text

_FLAG_ADS = "tools.ads"
_MONEDA_DEFECTO = "USD"
_PROVIDER_DEFECTO = "meta"


def _parse_presupuesto(valor: Any) -> Decimal | None:
    if valor is None or valor == "":
        return None
    try:
        return Decimal(str(valor))
    except InvalidOperation:
        return None


class AdsResumenTool(Tool):
    name = "ads_resumen"
    description = (
        "Muestra un resumen de las campañas de anuncios (Meta Ads) del tenant: lista de "
        "campañas y métricas del período (gasto, impresiones, clics). Solo lectura — no "
        "crea ni modifica nada. Si el tenant no conectó su cuenta de Meta (PUT "
        "/v1/ads/credentials), muestra datos de ejemplo en modo offline."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "periodo": {
                "type": "string",
                "description": (
                    "Ventana de tiempo para las métricas (formato 'date_preset' de Meta, "
                    "p. ej. 'last_7d', 'last_30d', 'this_month'). Por defecto 'last_30d'."
                ),
            },
        },
    }
    requires_flags = frozenset({_FLAG_ADS})

    def __init__(self, *, provider_resolver: Any = None) -> None:
        # Patrón inyectable (mismo criterio que `GenerarImagenTool` de
        # `edecan_creative.tools`): por defecto resuelve el proveedor
        # bring-your-own real; los tests pueden sustituirlo por un doble sin
        # tocar `ctx.vault`/`ctx.session`.
        from .providers import get_tenant_ads_provider

        self._resolver = provider_resolver or get_tenant_ads_provider

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        periodo = str(args.get("periodo") or "last_30d").strip() or "last_30d"
        provider = await self._resolver(ctx)
        try:
            campanas = await provider.list_campaigns()
            metricas = await provider.insights(periodo)
        except Exception as exc:
            return ToolResult(content=f"No pude consultar Meta Ads: {exc}")

        if not campanas:
            resumen = "No tienes campañas todavía."
        else:
            lineas = [
                f"- {c.get('name', '(sin nombre)')} [{c.get('status', '?')}] "
                f"— objetivo {c.get('objective', '?')}"
                for c in campanas
            ]
            resumen = "Campañas:\n" + "\n".join(lineas)

        gasto = metricas.get("spend", "0")
        impresiones = metricas.get("impressions", "0")
        clics = metricas.get("clicks", "0")
        resumen += (
            f"\n\nMétricas ({periodo}): gasto {gasto}, {impresiones} impresiones, "
            f"{clics} clics."
        )
        return ToolResult(content=resumen, data={"campanas": campanas, "metricas": metricas})


async def _crear_ad_draft(
    session: Any,
    *,
    tenant_id: Any,
    user_id: Any,
    nombre: str,
    objetivo: str,
    presupuesto_diario: Decimal | None,
    moneda: str,
    payload: dict[str, Any],
) -> Any:
    """`INSERT` directo en `ad_drafts` con `status='draft'` — lo ÚNICO que
    hace `ads_preparar_campana`. No hay ninguna otra sentencia SQL en esta
    función y nunca se llama a Meta desde aquí (ver el docstring del módulo).
    """
    row = (
        await session.execute(
            text(
                "INSERT INTO ad_drafts "
                "(tenant_id, user_id, provider, nombre, objetivo, presupuesto_diario, moneda, "
                "payload, status) "
                "VALUES (:tenant_id ::uuid, :user_id ::uuid, :provider, :nombre, :objetivo, "
                ":presupuesto_diario, :moneda, CAST(:payload AS jsonb), 'draft') "
                "RETURNING id"
            ),
            {
                "tenant_id": str(tenant_id),
                "user_id": str(user_id),
                "provider": _PROVIDER_DEFECTO,
                "nombre": nombre,
                "objetivo": objetivo,
                "presupuesto_diario": presupuesto_diario,
                "moneda": moneda,
                "payload": json.dumps(payload),
            },
        )
    ).mappings().first()
    await session.flush()
    if row is None:  # defensivo: Postgres no devolvió la fila recién insertada.
        raise RuntimeError("No se pudo crear el borrador de campaña.")
    return row["id"]


class AdsPrepararCampanaTool(Tool):
    name = "ads_preparar_campana"
    description = (
        "Crea un BORRADOR de campaña de anuncios (Meta Ads). NO la publica ni la crea en "
        "Meta: solo deja el borrador pendiente de confirmación en la página de Ads. Cuando "
        "el usuario la confirme ahí, la campaña se crea en Meta SIEMPRE en pausa — "
        "activarla es una decisión del usuario, tomada en el Ads Manager de Meta."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "nombre": {"type": "string", "description": "Nombre de la campaña."},
            "objetivo": {
                "type": "string",
                "description": (
                    "Objetivo de campaña de Meta, p. ej. 'OUTCOME_TRAFFIC', "
                    "'OUTCOME_ENGAGEMENT', 'OUTCOME_LEADS', 'OUTCOME_SALES', "
                    "'OUTCOME_AWARENESS'."
                ),
            },
            "presupuesto_diario": {
                "type": "number",
                "description": (
                    "Presupuesto diario, en la unidad principal de 'moneda' (p. ej. 50.00 "
                    "para 50 dólares/día). Opcional."
                ),
            },
            "moneda": {
                "type": "string",
                "description": "Código ISO-4217 de 3 letras.",
                "default": _MONEDA_DEFECTO,
            },
        },
        "required": ["nombre", "objetivo"],
    }
    requires_flags = frozenset({_FLAG_ADS})
    dangerous = True

    async def run(self, ctx: ToolContext, args: dict[str, Any]) -> ToolResult:
        nombre = str(args.get("nombre", "")).strip()
        if not nombre:
            return ToolResult(content="Necesito un nombre para la campaña.")
        objetivo = str(args.get("objetivo", "")).strip()
        if not objetivo:
            return ToolResult(
                content="Necesito el objetivo de la campaña (p. ej. 'OUTCOME_TRAFFIC')."
            )

        presupuesto_arg = args.get("presupuesto_diario")
        presupuesto_diario = _parse_presupuesto(presupuesto_arg)
        if presupuesto_arg not in (None, "") and presupuesto_diario is None:
            return ToolResult(content=f"'{presupuesto_arg}' no es un presupuesto válido.")
        if presupuesto_diario is not None and presupuesto_diario <= 0:
            return ToolResult(content="El presupuesto diario debe ser mayor que cero.")

        moneda = str(args.get("moneda") or _MONEDA_DEFECTO).strip().upper()
        if len(moneda) != 3 or not moneda.isalpha():
            moneda = _MONEDA_DEFECTO

        draft_id = await _crear_ad_draft(
            ctx.session,
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            nombre=nombre,
            objetivo=objetivo,
            presupuesto_diario=presupuesto_diario,
            moneda=moneda,
            payload={},
        )
        return ToolResult(
            content=(
                f"Borrador de campaña «{nombre}» creado (objetivo {objetivo}). NO se ha "
                "creado nada en Meta todavía — confírmalo o cancélalo en la página de Ads. "
                "Al confirmarlo, la campaña se crea SIEMPRE en pausa: actívala tú desde el "
                "Ads Manager de Meta cuando quieras que empiece a gastar."
            ),
            data={"draft_id": str(draft_id)},
        )


def get_all_tools() -> list[Tool]:
    """Instancia las 2 herramientas de ads. Consumido por
    `edecan_ads.__init__.get_all_tools` (`try: from .tools import
    get_all_tools`, ver ese módulo — WP-V4-07 nunca lo edita)."""
    return [AdsResumenTool(), AdsPrepararCampanaTool()]
