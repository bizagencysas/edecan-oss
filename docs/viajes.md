# Viajes (Amadeus + AfterShip)

Edecán conecta **tu propia cuenta de Amadeus for Developers** (self-service, gratis) para
buscar vuelos y hoteles reales y consultar horarios de vuelos, y **tu propia cuenta de
AfterShip** para rastrear paquetes/envíos — pero **nunca reserva ni paga nada por su
cuenta**: no existe, en todo este paquete, ninguna llamada a ninguna API de booking/pago
de Amadeus. Es **bring-your-own** al pie de la letra (`ARCHITECTURE.md` §14;
`DIRECCION_ACTUAL.md`, "Modelo de credenciales: TODO lo trae el cliente, siempre"):
conectas TUS PROPIAS credenciales de Amadeus/AfterShip — Edecán nunca opera una cuenta
de viajes compartida ni guarda una credencial de plataforma.

## Guardrail de dinero (léelo primero)

> **Edecán jamás reserva un vuelo/hotel, ni le cobra un centavo a nadie, por su
> cuenta.** Ver `ARCHITECTURE.md` §0, `docs/dinero-real.md` (misma política, aplicada
> aquí a viajes en vez de pagos/trading/publicidad).

`packages/travel/edecan_travel/amadeus.py::AmadeusClient` solo implementa **búsqueda e
información**: `buscar_vuelos` (`GET /v2/shopping/flight-offers`), `buscar_hoteles`
(`GET /v1/reference-data/locations/hotels/by-city` + `GET /v3/shopping/hotel-offers`) y
`estado_vuelo` (`GET /v2/schedule/flights`). Ninguno de los tres método —ni ningún otro
lugar del paquete— llama jamás a una API de creación de reserva/orden/pago de Amadeus.

### El único gate: la tool `preparar_reserva`

```
Chat (agente)                              Página de Viajes (UI)
─────────────                              ──────────────────────
Usuario: "resérvame el vuelo AV204
de Bogotá a Miami el 1 de agosto"
        │
        ▼
preparar_reserva (Tool dangerous=True)
        │
        │  ÚNICO gate: edecan_core.agent.Agent.run_turn exige que el
        │  usuario apruebe ESTE tool call antes de correrlo
        │  (SSE `confirmation_required` → POST /v1/conversations/{id}/confirm)
        ▼
INSERT orders(kind='purchase', status='draft')   ◄── lo ÚNICO que hace la tool.
        │                                             Nunca llama a Amadeus.
        ▼
Usuario revisa el borrador en la página de Órdenes (`/app/ordenes`)
        │
        ▼
Reservar de verdad = una acción del usuario, DIRECTAMENTE con la
aerolínea/hotel (o con Amadeus si administra su propia agencia) —
Edecán no participa en ese paso.
```

- **`preparar_reserva`** (`packages/travel/edecan_travel/tools.py`) es `dangerous=True`
  — `Agent.run_turn` (`ARCHITECTURE.md` §10.7) nunca la corre sin que el usuario haya
  aprobado ese `tool_call_id`, mismo gate que ya protege `preparar_pago`,
  `ads_preparar_campana`, `enviar_correo`, etc.
- Lo ÚNICO que hace, incluso aprobada, es un `INSERT` en la tabla `orders` **ya
  existente** (`kind='purchase'`, `status='draft'`, `descripcion` legible, `monto` +
  `moneda` de la oferta, `meta` con `{"tipo": "vuelo"|"hotel", "oferta": {...}}`) — ver
  el test explícito
  `packages/travel/tests/test_tools.py::test_preparar_reserva_nunca_llama_a_amadeus_solo_crea_draft`,
  que verifica mirando el SQL exacto ejecutado que la única sentencia es ese `INSERT`.
- A diferencia de `edecan_ads` (que sí tiene un SEGUNDO gate — `POST
  /v1/ads/borradores/{id}/confirmar` — para empujar el borrador a Meta), viajes **no
  tiene ningún "segundo push"**: no hay ninguna acción, en ningún endpoint de este
  producto, que reserve el vuelo/hotel de verdad. El borrador en Órdenes es el final
  del camino dentro de Edecán; reservar de verdad siempre pasa por fuera (la propia
  aerolínea/hotel, o el sitio de Amadeus si el tenant lo usa directamente).
- No hay ningún endpoint HTTP `POST` que cree un borrador directamente: la única forma
  de crear uno es la tool del agente, con su aprobación explícita en el chat. La página
  de Viajes ofrece un botón "Guardar como borrador en Órdenes" en cada resultado de
  búsqueda, pero por debajo hace exactamente ese mismo camino (crea una conversación,
  le pide al agente que llame a `preparar_reserva`, y muestra el mismo gate de
  confirmación ahí mismo) — nunca hay un atajo que salte la aprobación.

## Modelo bring-your-own: de dónde sacas tus credenciales

### Amadeus for Developers (vuelos y hoteles)

1. Entra a **[developers.amadeus.com](https://developers.amadeus.com/register)** y crea
   una cuenta gratuita (self-service).
2. Crea una **app** — Amadeus te da al instante un **API Key** y un **API Secret** para
   el entorno de pruebas (`test`), sin ninguna revisión manual.
3. El entorno `test` es **gratis e ilimitado**, con datos de prueba de Amadeus (rutas y
   hoteles reales, pero con disponibilidad/precios simulados) — perfecto para probar
   Edecán sin gastar cuota real.
4. Cuando quieras resultados 100% reales, solicita acceso de **production** desde tu
   panel de Amadeus (Amadeus revisa la solicitud) y elige `"production"` como
   `environment` al conectar en Edecán.

Estas credenciales son **tuyas**: viven en tu propia cuenta de Amadeus for Developers,
bajo tu control (puedes revocarlas cuando quieras). Edecán nunca pide ni almacena una
credencial "de plataforma" para hablar con Amadeus — cada tenant trae la suya.

### AfterShip (rastreo de paquetes)

1. Entra a **[aftership.com/signup](https://www.aftership.com/signup)** y crea una
   cuenta (tiene plan gratuito).
2. En **Configuración → API Keys** genera un API Key.

## Conectarlas en Edecán

`PUT /v1/viajes/credentials` (flag de plan `tools.travel`):

```json
{
  "api_key": "TU_API_KEY_DE_AMADEUS_AQUI",
  "api_secret": "TU_API_SECRET_DE_AMADEUS_AQUI",
  "environment": "test",
  "validate": true
}
```

Con `validate: true` (el default), Edecán pide un **token OAuth2 real**
(`POST /v1/security/oauth2/token`, `grant_type=client_credentials`) antes de guardar
nada. Si Amadeus lo rechaza, `PUT` responde `400` con el detalle EXACTO que dio Amadeus
— nunca se guarda una credencial sin probarla. `validate: false` es la escotilla de
escape (tests, migraciones).

`environment` queda en `"test"` por defecto **a propósito**, tanto en el cliente
(`AmadeusClient.__init__`) como en el endpoint: nadie gasta cuota de producción por
accidente solo por conectar sus credenciales.

`PUT /v1/viajes/rastreo/credentials` (mismo flag):

```json
{ "api_key": "TU_API_KEY_DE_AFTERSHIP_AQUI", "validate": true }
```

Con `validate: true`, Edecán llama `GET /couriers` (la sonda más barata de la API de
AfterShip) para confirmar que el `api_key` sirve.

Ambas credenciales se guardan cifradas en tu `TokenVault` (`ARCHITECTURE.md` §10.4,
connector_keys `"travel"` y `"tracking"`) — nunca en texto plano, nunca en logs.

- `GET /v1/viajes/status` → `{"travel": {"configured", "environment"}, "tracking":
  {"configured"}}`.
- `DELETE /v1/viajes/credentials` / `DELETE /v1/viajes/rastreo/credentials` →
  desconectan (idempotentes).

## Qué puede hacer el agente

`edecan_travel` (`packages/travel/`) expone 5 herramientas al agente, todas detrás del
flag de plan `tools.travel`:

| Tool | `dangerous` | Qué hace |
|---|---|---|
| `buscar_vuelos` | no | Ofertas de vuelo entre dos aeropuertos (Amadeus real, o ejemplos de demostración en modo offline si no hay cuenta conectada). |
| `buscar_hoteles` | no | Ofertas de hotel por ciudad y fechas. |
| `estado_vuelo` | no | Horarios programados de un vuelo (aerolínea + número + fecha). |
| `rastrear_paquete` | no | Estado + historial de checkpoints de un envío (AfterShip real, o ejemplo offline). |
| `preparar_reserva` | **sí** | Crea un BORRADOR en `orders` (`status='draft'`). NO llama a Amadeus — ver el guardrail de dinero arriba. |

## API HTTP completa (`/v1/viajes/*`, flag `tools.travel` en todas las rutas)

| Ruta | Qué hace |
|---|---|
| `PUT /v1/viajes/credentials` | Pegar y validar la credencial de Amadeus (`api_key`, `api_secret`, `environment?`). |
| `DELETE /v1/viajes/credentials` | Desconectar Amadeus (idempotente). |
| `PUT /v1/viajes/rastreo/credentials` | Pegar y validar la credencial de AfterShip (`api_key`). |
| `DELETE /v1/viajes/rastreo/credentials` | Desconectar AfterShip (idempotente). |
| `GET /v1/viajes/status` | Estado de ambas conexiones. |
| `GET /v1/viajes/buscar/vuelos?origen=&destino=&fecha=&adultos=&max_resultados=` | Proxy fino hacia el proveedor del tenant (Amadeus real, o ejemplos si no hay cuenta conectada). |
| `GET /v1/viajes/buscar/hoteles?ciudad=&checkin=&checkout=&adultos=` | Igual, para hoteles. |
| `GET /v1/viajes/rastreo/{numero}?courier_slug=` | Igual, para rastreo (AfterShip). `courier_slug` es opcional — si falta, se detecta automáticamente. |

Ninguna de estas rutas escribe nada en `orders`: como se explica arriba, la única forma
de crear un borrador es la tool `preparar_reserva`, siempre con aprobación humana
explícita en el chat.

## Proveedores

- **Stubs** (`StubTravelProvider`/`StubTrackingProvider`, default sin ninguna cuenta
  conectada): 100% offline y deterministas — pensados para desarrollo, self-host sin
  cuenta todavía, y tests. Nunca hacen una llamada de red; sus resultados están
  CLARAMENTE marcados como ejemplo en el propio texto (p. ej. "XX (ejemplo, sin cuenta
  de Amadeus conectada)").
- **`AmadeusClient`** (se activa apenas conectas tu cuenta): habla con
  `https://test.api.amadeus.com` o `https://api.amadeus.com` según `environment`,
  autenticado con OAuth2 `client_credentials` (token cacheado en memoria por instancia,
  con un margen de 60s antes de refrescarlo).
- **`AfterShipClient`** (se activa apenas conectas tu cuenta): habla con
  `https://api.aftership.com/v4`, header `as-api-key`.

`get_tenant_travel_provider(ctx)`/`get_tenant_tracking_provider(ctx)`
(`packages/travel/edecan_travel/providers.py`) resuelven cuál usar leyendo el
`TokenVault` del tenant; si el tenant no conectó nada, o cualquier paso de esa
resolución falla (vault caído, credencial corrupta), se degrada silenciosamente al
stub correspondiente — nunca revienta la búsqueda ni la pantalla de Viajes por esto,
solo deja un `logging.warning`. No existe ningún nivel intermedio de "credencial de
plataforma": Edecán no opera ninguna cuenta de Amadeus/AfterShip propia que ofrecer
como alternativa — la única opción además de la tuya es el stub.

## Límites (léelo también)

- **Buscar e informarte: sí, siempre** (con tu propia cuenta, o con datos de ejemplo si
  no conectaste nada).
- **Reservar y pagar: NUNCA por parte de Edecán.** El máximo que Edecán hace es dejar un
  borrador en Órdenes — reservar de verdad es siempre una decisión y una acción tuya,
  fuera de Edecán por completo (con la aerolínea, el hotel, o directamente en Amadeus si
  administras tu propia agencia).
- **Por qué esta línea es tan dura:** dos motivos, el mismo criterio que
  `docs/dinero-real.md`/`docs/ads.md`. (1) **ToS** — ninguna API de vuelos/hoteles
  permite que un agente automatizado complete compras en nombre de un usuario sin un
  flujo de pago explícito y verificado por el propio proveedor; automatizar eso violaría
  los términos de Amadeus (y de cualquier aerolínea/hotel). (2) **Dinero real nunca se
  mueve solo** (`ARCHITECTURE.md` §0, guardrail no negociable del producto completo) —
  ni una reserva de $50 ni una de $5000 se ejecutan sin que un humano la revise y decida,
  siempre.

## Qué falta / decisiones conscientes

- No hay integración con otros GDS o agregadores (Sabre, Travelport, Skyscanner, etc.) —
  Amadeus Self-Service es la única API de vuelos/hoteles con un modelo self-service
  "pegar y validar" comparable al resto de conectores bring-your-own de Edecán (API Key +
  Secret, sin revisión manual para el entorno de pruebas). Si se agrega otro proveedor en
  el futuro, debería seguir el mismo diseño: `TravelProvider` como protocolo común
  (`packages/travel/edecan_travel/providers.py`), un proveedor nuevo que lo implemente,
  y el mismo guardrail (búsqueda sí, reserva jamás).
- `buscar_hoteles` limita a 20 hoteles por consulta de `by-city` antes de pedir ofertas
  (`_MAX_HOTEL_IDS_POR_CONSULTA` en `amadeus.py`) — evita construir una URL
  desproporcionadamente larga; no pretende ser exhaustivo para ciudades con cientos de
  hoteles.
- No hay un job en segundo plano que sincronice reservas hechas fuera de Edecán — no
  aplica aquí de todos modos, porque Edecán nunca crea una reserva real que necesite
  sincronizarse.
- El rastreo (`rastrear_paquete`/`GET /rastreo/{numero}`) es de solo lectura: no crea,
  actualiza ni borra ningún tracking en la cuenta de AfterShip del tenant — Edecán solo
  consulta, nunca administra esa cuenta en su nombre.

## Auditoría v7 (WP-V7-02, 2026-07-09)

`packages/travel/edecan_travel/` no había sido incluido en ningún barrido de
seguridad dedicado hasta ahora (`WP-V5-02`, el barrido bring-your-own de v5,
cubrió 11 dominios pero no viajes — ver `HOTFIXES_PENDIENTES.md`, "Barrido v5").
Este WP hizo la primera pasada línea por línea sobre los 4 archivos del paquete
más `apps/api/edecan_api/routers/viajes.py`, con los mismos 4 criterios que
otros barridos de esta sesión (fuga bring-your-own, plan-flag, evidencia vs.
rollback, esquema real de `orders`). **Resultado: LIMPIO en los 4 — cero
hallazgos que corregir**, con regresiones nuevas para anclarlo. Tabla completa
en `docs/cumplimiento/barrido-v7-viajes.md`; resumen:

- **Bring-your-own**: `AmadeusClient`/`AfterShipClient` no tienen NINGÚN SDK de
  terceros involucrado (a diferencia del hallazgo de Polly en v5) — son
  `httpx.AsyncClient` puros, la credencial siempre por el constructor, sin
  ningún campo `AMADEUS_*`/`AFTERSHIP_*` en `Settings` al que algo pudiera caer.
  Nuevo: `packages/travel/tests/test_travel_byo.py`.
- **Plan-flag**: las 8 rutas del router y las 5 tools ya exigían
  `tools.travel` de forma consistente; ninguna tool multiplexa acciones con
  permisos distintos (a diferencia de `usar_computadora`), así que el patrón
  `_bloqueo_por_plan` no aplica acá. Se corrigió además un docstring
  desactualizado en `apps/api/tests/test_viajes_router.py` que afirmaba que
  `PLANES` todavía no tenía el flag pinned — hoy sí lo tiene, matriz idéntica a
  `ARCHITECTURE.md` §14.c.
- **Evidencia vs. rollback**: las 4 credenciales ya estaban "Seguro" desde
  `barrido-evidencia-v6.md`; re-verificado con aserciones AST (no solo lectura)
  que siguen intactas. `preparar_reserva`/`_crear_reserva_draft` (el único sitio
  de escritura nuevo que este WP tenía que barrer) está protegido por el mismo
  aislamiento de excepción de `Tool.run()` que ya documentó el hallazgo de
  `LanzarCampanaTool` en v6 — nunca comitea su propia sesión.
- **Esquema de `orders`**: el `INSERT` de `_crear_reserva_draft` coincide columna
  por columna con `edecan_db.models.Order`/la migración `0003_v2_expansion`
  real — verificado tanto estáticamente (`apps/api/tests/test_v7_sweep_viajes.py`)
  como empíricamente contra un Postgres desechable real.
