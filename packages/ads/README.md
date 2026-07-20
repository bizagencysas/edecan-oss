# packages/ads — `edecan_ads`

Ads: preparación de campañas publicitarias con guardrail de dinero, proveedor real Meta
Marketing API (bring-your-own cuenta del tenant) + stub offline determinista
(`ARCHITECTURE.md` §13, fase v4 — completa el esqueleto de fase v4). Ver
[`docs/ads.md`](../../docs/ads.md) para el flujo completo y el modelo bring-your-own.

## Guardrail de dinero (lo más importante del paquete)

`MetaAdsProvider.create_campaign_paused` crea la campaña **SIEMPRE en pausa**
(`status="PAUSED"` hardcodeado, sin ninguna forma de que un `payload` lo cambie) — y
`ads_preparar_campana` (la única tool que toca la base de datos) **jamás llama a Meta**:
solo inserta un borrador `ad_drafts(status='draft')`. El push real solo ocurre cuando el
humano confirma explícitamente en `POST /v1/ads/borradores/{id}/confirmar`
(`apps/api/edecan_api/routers/ads.py`). Ver `docs/ads.md` para el diagrama completo del
doble gate.

## Las 2 herramientas (nombres exactos)

| Tool | Flag | `dangerous` | Qué hace |
|---|---|---|---|
| `ads_resumen` | `tools.ads` | No | Campañas + métricas del proveedor del tenant (Meta real, o datos de ejemplo si no hay cuenta conectada). |
| `ads_preparar_campana` | `tools.ads` | **Sí** | `INSERT ad_drafts(status='draft')` — nunca llama a Meta. |

`get_all_tools() -> list[Tool]` (`edecan_ads/__init__.py`, fase v4, NUNCA editado por
este WP) es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`.

## Módulos

- **`providers.py`** — `AdsProvider` (protocolo `Protocol runtime_checkable`:
  `list_campaigns()`, `insights(date_preset)`, `create_campaign_paused(nombre, objetivo,
  presupuesto_diario, moneda, payload) -> external_id`).
  - `StubAdsProvider` (default sin credencial conectada): 100% offline y determinista.
  - `MetaAdsProvider`: habla con `https://graph.facebook.com/v23.0` (Graph API oficial de
    Meta Marketing) con el access token + `ad_account_id` **del tenant**.
  - `get_tenant_ads_provider(ctx)`: resuelve del `TokenVault` (connector_key `"ads"`) con
    fallback directo a `StubAdsProvider` — sin ningún nivel intermedio de credencial de
    plataforma (Edecán no opera ninguna cuenta de Meta propia).
- **`tools.py`** — `AdsResumenTool`/`AdsPrepararCampanaTool` + `get_all_tools()`.
  `ads_preparar_campana` hace un único `INSERT` en `ad_drafts` vía `ctx.session` (SQL
  parametrizado directo, sin `edecan_db.models` — mismo criterio que `edecan_toolkit`/
  `edecan_commerce`, ver sus README) y nunca importa `providers.py` en su rama de
  escritura.

## Tests

```
uv run pytest packages/ads
```

`tests/conftest.py` define fakes locales por duck typing (`FakeSession`, `FakeVault`,
`ctx` como `SimpleNamespace`) — no importa `edecan_db` ni `edecan_api` (`ARCHITECTURE.md`
§10.1).

| Archivo | Cubre |
|---|---|
| `test_providers.py` | `StubAdsProvider` (determinismo, sin red); `MetaAdsProvider` con `respx` (requests/params exactos de `list_campaigns`/`insights`, y **el test explícito del guardrail**: `create_campaign_paused` siempre manda `status=PAUSED` a Meta, incluso si `payload` intenta colar `status=ACTIVE`); `get_tenant_ads_provider` (tenant → stub en cada rama de fallo, nunca una credencial de plataforma). |
| `test_tools.py` | `ads_resumen` con un proveedor inyectado; **el test explícito**: `ads_preparar_campana` nunca resuelve un `AdsProvider` ni llama a Meta — solo un `INSERT` con `status='draft'`. |
| `test_catalogo.py` | `get_all_tools()` devuelve los 2 nombres pinned, solo `ads_preparar_campana` es `dangerous`, ambas requieren `tools.ads`, ninguna menciona LinkedIn. |

## Qué NO hace este paquete

- No activa campañas ni gasta presupuesto real bajo ninguna circunstancia — ver
  `docs/ads.md`, "Guardrail de dinero".
- No implementa Google Ads (documentado como proveedor futuro en `docs/ads.md`, exige
  developer token aprobado por Google — fuera de esta ola a propósito).
- No sincroniza en segundo plano campañas creadas fuera de Edecán: `ads_resumen`/
  `GET /v1/ads/resumen` siempre leen a Meta en vivo.
