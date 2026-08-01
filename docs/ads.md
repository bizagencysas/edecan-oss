# Ads (Meta Marketing API)

Edecán conecta **tu propia cuenta de anuncios de Meta** (Facebook/Instagram Ads) para
mostrarte un resumen de tus campañas y preparar campañas nuevas — pero **nunca activa
gasto por su cuenta**: toda campaña que Edecán crea nace **en pausa**, y activarla es
siempre una decisión tuya, tomada en el Ads Manager de Meta. Es **bring-your-own** al pie
de la letra (`ARCHITECTURE.md` §13 y [`credenciales.md`](./credenciales.md)): conectas TU PROPIA app de Meta for Developers con TUS
propios permisos — Edecán nunca opera una cuenta de anuncios compartida ni guarda una
credencial de plataforma.

## Guardrail de dinero (léelo primero)

> **Edecán jamás activa una campaña de anuncios ni le pone un centavo de presupuesto en
> marcha por su cuenta.** Ver `ARCHITECTURE.md` §0, `docs/dinero-real.md` (misma política,
> aplicada aquí a gasto publicitario en vez de pagos/trading).

Toda campaña que Edecán crea en tu cuenta de Meta se crea con `status="PAUSED"` —
**hardcodeado, sin excepción, sin ninguna forma de que un `payload` lo cambie**
(`packages/ads/edecan_ads/providers.py::MetaAdsProvider.create_campaign_paused`, con un
test explícito que verifica esto mirando el body exacto que se envía a Meta:
`packages/ads/tests/test_providers.py::test_meta_provider_create_campaign_paused_ignora_status_activo_en_payload`).
Activarla — es decir, dejar que empiece a gastar tu presupuesto real — es un botón que
solo tú puedes apretar, dentro del Ads Manager de Meta.

### El doble gate

```
Chat (agente)                              Página de Ads (UI / API)
─────────────                              ─────────────────────────
Usuario: "prepárame una campaña de
tráfico con $50/día para mi tienda"
        │
        ▼
ads_preparar_campana (Tool dangerous=True)
        │
        │  1er gate: edecan_core.agent.Agent.run_turn exige que el
        │  usuario apruebe ESTE tool call antes de correrlo
        │  (SSE `confirmation_required` → POST /v1/conversations/{id}/confirm)
        ▼
INSERT ad_drafts(status='draft')          ◄── lo ÚNICO que hace la tool.
        │                                      Nunca llama a Meta.
        │
        ▼
Usuario revisa el borrador (nombre, objetivo, presupuesto) en la página de Ads
        │
        │  2do gate: confirmación explícita en la UI
        ▼
POST /v1/ads/borradores/{id}/confirmar
        │
        ▼
Meta crea la campaña — SIEMPRE con status=PAUSED.
Tú la activas manualmente en el Ads Manager de Meta cuando quieras.
```

- **Gate 1 — confirmación del *tool call* en el chat**: `ads_preparar_campana`
  (`packages/ads/edecan_ads/tools.py`) es `dangerous=True`. `Agent.run_turn`
  (`ARCHITECTURE.md` §10.7) nunca la corre sin que el usuario haya aprobado ese
  `tool_call_id` — mismo gate que ya protege `preparar_pago`, `enviar_correo`,
  `llamar_contacto`, etc.
- **Incluso aprobado el tool call, lo ÚNICO que pasa es un `INSERT` en `ad_drafts` con
  `status='draft'`.** La tool nunca resuelve un `AdsProvider` ni hace ninguna llamada de
  red — ver el test explícito
  `packages/ads/tests/test_tools.py::test_preparar_campana_nunca_llama_a_meta_solo_crea_draft`,
  que verifica mirando el SQL exacto ejecutado que la única sentencia es ese `INSERT`.
- **Gate 2 — confirmación en la UI**: solo `POST /v1/ads/borradores/{id}/confirmar`
  (`apps/api/edecan_api/routers/ads.py`) puede empujar el borrador a Meta. Su respuesta
  siempre aclara explícitamente que la campaña quedó pausada y que activarla es cosa tuya.

Ningún atajo salta ninguno de los dos gates: no hay un endpoint que cree una campaña ya
activa, no hay una tool que llame a Meta directamente, y `confirmar` en sí siempre manda
`status="PAUSED"` sin importar qué traiga el borrador.

## Modelo bring-your-own: de dónde sacas tu token de Meta

1. Entra a **[developers.facebook.com](https://developers.facebook.com/apps)** con la
   cuenta de Facebook que administra tu página/cuenta de anuncios.
2. Crea una **app** (tipo "Business" — es la que habilita los productos de Marketing
   API). Si ya tienes una app de Meta para otra cosa (p. ej. la que usas para
   `connectors.social`, ver `docs/conectores.md`), puedes reutilizarla.
3. Agrega el producto **Marketing API** a tu app.
4. Genera un **access token** con los permisos `ads_management` y `ads_read` sobre la
   cuenta de anuncios que quieres conectar (un token de usuario de sistema o un token de
   página/negocio de larga duración, según cómo administres tu cuenta — Meta documenta
   varias formas; para pruebas rápidas, el **Explorador de la Graph API** dentro de la
   propia consola de tu app permite generar uno con esos dos permisos en un par de
   clics).
5. Copia el **ID de tu cuenta de anuncios** (`ad_account_id`) — lo encuentras en el Ads
   Manager, arriba a la izquierda, con o sin el prefijo `act_` (Edecán acepta ambas
   formas, ver `edecan_ads.providers.normalizar_ad_account_id`).

Estas credenciales son **tuyas**: viven en tu propia app de Meta, bajo tu propio control
(puedes revocarlas en cualquier momento desde la configuración de tu app o de tu cuenta de
Meta Business). Edecán nunca pide ni almacena una credencial "de plataforma" para hablar
con Meta — cada tenant trae la suya.

## Conectarla en Edecán

`PUT /v1/ads/credentials` (flag de plan `tools.ads`):

```json
{
  "access_token": "TU_ACCESS_TOKEN_DE_META_AQUI",
  "ad_account_id": "act_1234567890",
  "validate": true
}
```

Con `validate: true` (el default), Edecán hace **antes de guardar nada**:

1. `GET /me` — confirma que el access token es válido.
2. `GET /act_{ad_account_id}?fields=name,currency` — confirma que ese id de cuenta existe
   y que el token tiene acceso a ella.

Si cualquiera de las dos falla, `PUT` responde `400` con el mensaje EXACTO que dio Meta
(p. ej. "Error validating access token") — nunca se guarda una credencial sin probarla
primero, mismo principio de "pegar y validar" que el resto de conectores bring-your-own
(mismo patrón que
`docs/casa-inteligente.md`). `validate: false` es la escotilla de escape (tests,
migraciones).

Solo si la validación pasa (o se saltó a propósito) se guarda, cifrada en tu `TokenVault`
(`ARCHITECTURE.md` §10.4, connector_key `"ads"`) — nunca en texto plano, nunca en logs.

- `GET /v1/ads/status` → `{configured, ad_account_id, nombre_cuenta, moneda, reachable}`.
  Vuelve a pedirle a Meta el nombre/moneda de la cuenta en cada llamada (nunca queda en
  caché desde el `PUT`), así que también sirve para detectar un token que ya venció:
  `reachable: false`. Si la red falla del todo, `reachable` queda en `null` — nunca
  provoca un error 500.
- `DELETE /v1/ads/credentials` → desconecta (idempotente).

## Qué puede hacer el agente

`edecan_ads` (`packages/ads/`) expone 2 herramientas al agente, ambas detrás del flag de
plan `tools.ads` (`True` en `free_selfhost`/`hosted_pro`/`hosted_business`, `False` en
`hosted_basic` — `edecan_schemas.plans.PLANES`):

| Tool | `dangerous` | Qué hace |
|---|---|---|
| `ads_resumen` | no | Lista tus campañas y las métricas del período (gasto, impresiones, clics) — solo lectura. Si no conectaste tu cuenta de Meta, muestra un ejemplo de demostración en modo offline en vez de fallar. |
| `ads_preparar_campana` | **sí** | Crea un BORRADOR de campaña (`ad_drafts`, `status='draft'`) con nombre, objetivo y presupuesto diario. NO llama a Meta — ver el guardrail de dinero arriba. |

## API HTTP completa (`/v1/ads/*`, flag `tools.ads` en todas las rutas)

| Ruta | Qué hace |
|---|---|
| `PUT /v1/ads/credentials` | Pegar y validar la credencial de Meta (ver arriba). |
| `DELETE /v1/ads/credentials` | Desconectar (idempotente). |
| `GET /v1/ads/status` | Estado de la conexión + sonda en vivo. |
| `GET /v1/ads/resumen?periodo=last_30d` | Campañas + métricas del proveedor del tenant (Meta real, o datos de ejemplo si no hay cuenta conectada). `periodo` acepta cualquier `date_preset` de Meta (`last_7d`, `last_30d`, `this_month`...). |
| `GET /v1/ads/borradores` | Lista tus borradores (`ad_drafts`) de más nuevo a más viejo. |
| `POST /v1/ads/borradores/{id}/confirmar` | `draft → confirmed → pushed` (o `→ error` si Meta rechaza el push, con el motivo guardado en `error`). Empuja la campaña a Meta **SIEMPRE en pausa** — la respuesta siempre lo aclara explícitamente. Solo funciona sobre un borrador en estado `draft`. |
| `POST /v1/ads/borradores/{id}/cancelar` | Cancela un borrador (`draft`/`confirmed`/`error` → `cancelled`). No puede cancelar uno ya `pushed` — esa campaña ya vive en Meta, se gestiona desde el Ads Manager. |

Cada borrador (`ad_drafts`) guarda: `provider` (hoy siempre `"meta"`), `nombre`,
`objetivo`, `presupuesto_diario`, `moneda`, `payload` (campos adicionales de Meta más allá
de los básicos, p. ej. `special_ad_categories` — nunca incluye `status`, que el proveedor
siempre sobrescribe), `status`, `external_id` (el id real de la campaña en Meta, una vez
`pushed`), `error` (si `confirmar` falló), `confirmed_at`, `pushed_at`.

### Auditabilidad: la confirmación humana nunca se pierde, ni siquiera si Meta falla

`POST /v1/ads/borradores/{id}/confirmar` marca `status='confirmed'` + queda auditada
ANTES de intentar el push real a Meta. Si Meta rechaza la campaña (parámetro inválido,
token vencido a mitad de camino, cuenta suspendida, lo que sea), el handler deja
constancia (`status='error'`, `error=<mensaje exacto de Meta>`, un segundo registro de
auditoría) y hace un `commit` explícito de esa evidencia **antes** de devolver el error —
mismo guardrail que ya aplican `edecan_api.routers.commerce.confirm_order` y
`edecan_api.routers.remote.get_frame` (`docs/seguridad-modelo-amenazas.md`, puntos 8 y 9): sin este
cuidado, el rollback automático de la transacción de la request se llevaría puesta
justamente la prueba de que el usuario confirmó — el peor momento para perderla. Un
borrador en `status='error'` sigue siendo cancelable, o se puede volver a intentar creando
uno nuevo.

## Proveedores

- **`StubAdsProvider`** (default, sin ninguna cuenta de Meta conectada): 100% offline y
  determinista — pensado para desarrollo, self-host sin cuenta de Meta todavía, y tests.
  Nunca hace una llamada de red; `ads_resumen` con el stub muestra una campaña de ejemplo
  claramente marcada como tal.
- **`MetaAdsProvider`** (se activa apenas conectas tu cuenta): habla con
  `https://graph.facebook.com/v23.0` — la Graph API oficial de Meta Marketing:
  - `GET /act_{ad_account_id}/campaigns?fields=name,status,objective,daily_budget`
  - `GET /act_{ad_account_id}/insights?fields=spend,impressions,clicks,cpc,ctr&date_preset=...`
  - `POST /act_{ad_account_id}/campaigns` — SIEMPRE con `status=PAUSED` y
    `special_ad_categories` (por defecto `[]` si tu borrador no especifica ninguna).

`get_tenant_ads_provider(ctx)` resuelve cuál usar leyendo el `TokenVault` del tenant
(connector_key `"ads"`); si el tenant no conectó nada, o cualquier paso de esa resolución
falla (vault caído, credencial corrupta), se degrada silenciosamente al stub — nunca
revienta `ads_resumen` ni la pantalla de Ads por esto, solo deja un `logging.warning`.
No existe ningún nivel intermedio de "credencial de plataforma": a diferencia de, por
ejemplo, generación de imágenes (`docs/creatividad.md`), Edecán no opera ninguna cuenta de
Meta propia que ofrecer como alternativa — la única opción además de la tuya es el stub.

## Google Ads — proveedor futuro, documentado pero no implementado

Google Ads es un proveedor futuro, todavía no implementado. Queda **fuera del alcance actual a propósito** — mejor no implementarlo a medias que dejar un
proveedor roto o engañoso:

- La API de Google Ads (`google-ads` API v18+) exige un **developer token aprobado por
  Google** para cualquier acceso más allá de una cuenta de prueba (`test account`) — un
  proceso de solicitud manual, con revisión humana de Google, que no se puede automatizar
  ni obtener con un simple "pegar y validar" como el resto de conectores. Un tenant no
  puede simplemente generar una API key y usarla de inmediato, a diferencia de Meta.
- El modelo de autenticación también es más pesado: OAuth 2.0 de Google Ads exige un
  `refresh_token` obtenido a través de un flujo OAuth completo (no solo un access token
  pegado a mano) más el `developer_token` aprobado y, típicamente, un `login_customer_id`
  si se administra la cuenta a través de un MCC (Manager Account).
- Cuando se implemente, debería seguir exactamente el mismo diseño que ya sienta este
  documento: `AdsProvider` como protocolo común (`packages/ads/edecan_ads/providers.py`),
  un `GoogleAdsProvider` nuevo que lo implemente, el mismo flujo borrador → confirmación,
  y el mismo guardrail de dinero — creación siempre pausada
  (`campaign.status = PAUSED` es el equivalente exacto en la API de Google Ads),
  activación siempre manual en Google Ads. `provider` en `ad_drafts` ya está pensado para
  distinguir `"meta"` de un futuro `"google"` en la misma tabla, sin necesitar una
  migración nueva.

## Qué falta / decisiones conscientes

- El `payload` de un borrador (campos adicionales de Meta más allá de
  nombre/objetivo/presupuesto/moneda, p. ej. `special_ad_categories` no estándar,
  `bid_strategy`, segmentación) hoy solo lo puede rellenar quien edite la fila
  directamente o una futura tool/pantalla "avanzada" — `ads_preparar_campana` (la tool que
  usa el chat) crea el borrador con `payload={}` a propósito: el modelo de lenguaje no
  tiene por qué conocer los nombres de campo internos de la Graph API de Meta.
- No hay un job en segundo plano que sincronice campañas creadas fuera de Edecán
  (directamente en el Ads Manager) hacia `ad_drafts` — `ads_resumen`/`GET /v1/ads/resumen`
  siempre reflejan el estado real de Meta en el momento de la consulta (lectura en vivo,
  no una copia desactualizada), así que esto no es una laguna de datos, solo significa que
  `ad_drafts` únicamente registra las campañas que pasaron por el flujo de Edecán.
- La conversión de `presupuesto_diario` a la unidad mínima de la moneda (`daily_budget` de
  Meta, p. ej. centavos para USD) cubre las monedas de "offset cero" más comunes (JPY,
  KRW, VND, CLP...) y asume 2 decimales para el resto — no pretende ser la tabla completa
  de monedas de Meta. Un borrador puede incluir `daily_budget` ya calculado en su
  `payload` para saltarse esta conversión si hace falta un caso no cubierto.
