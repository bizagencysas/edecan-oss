# packages/travel — `edecan_travel`

Viajes: búsqueda real de vuelos/hoteles vía **Amadeus Self-Service** (bring-your-own
credenciales del tenant) + rastreo de paquetes vía **AfterShip**, con guardrail de dinero
— igual que `packages/ads` (`ARCHITECTURE.md` §13/§14, fase v5). Ver
[`docs/viajes.md`](../../docs/viajes.md) para el flujo completo y el modelo bring-your-own.

## Guardrail de dinero (lo más importante del paquete)

**Edecán jamás reserva ni paga un vuelo/hotel por su cuenta.** No existe ninguna llamada a
ninguna API de booking/pago de Amadeus en todo este paquete — solo búsqueda/información
(`GET /v2/shopping/flight-offers`, `GET /v3/shopping/hotel-offers`,
`GET /v2/schedule/flights`) y rastreo (AfterShip, también solo lectura).
`preparar_reserva` (la única tool que toca la base de datos) **jamás llama a Amadeus**:
solo inserta un borrador en la tabla `orders` ya existente (`status='draft'`). El humano
decide si comprar de verdad, directamente con la aerolínea/hotel — Edecán nunca completa
esa compra.

## Las 5 herramientas (nombres exactos)

| Tool | Flag | `dangerous` | Qué hace |
|---|---|---|---|
| `buscar_vuelos` | `tools.travel` | No | Ofertas de vuelo (Amadeus real, o ejemplo offline si no hay cuenta conectada). |
| `buscar_hoteles` | `tools.travel` | No | Ofertas de hotel por ciudad. |
| `estado_vuelo` | `tools.travel` | No | Horarios programados de un vuelo (`carrier` + `numero` + `fecha`). |
| `rastrear_paquete` | `tools.travel` | No | Estado + checkpoints de un envío (AfterShip). |
| `preparar_reserva` | `tools.travel` | **Sí** | `INSERT orders(kind='purchase', status='draft')` — nunca llama a Amadeus. |

`get_all_tools() -> list[Tool]` (`edecan_travel/__init__.py`) es el entry point que consume
`edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`.

## Módulos

- **`amadeus.py`** — `AmadeusClient`: OAuth2 `client_credentials` contra
  `POST /v1/security/oauth2/token` (token cacheado en memoria por instancia, margen de
  60s antes de vencer), `base_url` según `environment` ∈ `{"test", "production"}`
  (default `"test"` A PROPÓSITO — ver el docstring de la clase). Métodos
  `buscar_vuelos`/`buscar_hoteles`/`estado_vuelo`, todos de solo lectura contra la API
  oficial de Amadeus. `TravelError` con el mensaje real de Amadeus (nunca el secreto en
  el mensaje ni en logs).
- **`tracking.py`** — `AfterShipClient`: header `as-api-key`, base
  `https://api.aftership.com/v4`. `rastrear(tracking_number, courier_slug=None)` —
  si falta `courier_slug`, primero llama `POST /couriers/detect`. `TrackingError` mismo
  criterio que `TravelError`.
- **`providers.py`** — `TravelProvider`/`TrackingProvider` (protocolos
  `Protocol runtime_checkable`, mismo estilo que `edecan_ads.providers.AdsProvider`):
  - `StubTravelProvider`/`StubTrackingProvider` (default sin credencial conectada): 100%
    offline y deterministas, 2-3 resultados de ejemplo CLARAMENTE marcados como tales.
  - `get_tenant_travel_provider(ctx)` / `get_tenant_tracking_provider(ctx)`: resuelven del
    `TokenVault` (connector_key `"travel"` / `"tracking"`) con fallback directo al stub
    correspondiente ante cualquier fallo — sin ningún nivel intermedio de credencial de
    plataforma (Edecán no opera ninguna cuenta de Amadeus/AfterShip propia). Calcados
    línea por línea de `edecan_ads.providers.get_tenant_ads_provider`.
- **`tools.py`** — las 5 clases `Tool` + `get_all_tools()`. `preparar_reserva` hace un
  único `INSERT` en `orders` vía `ctx.session` (SQL parametrizado directo, mismas columnas
  que usa `packages/commerce`) y nunca importa `providers.py` en su rama de escritura.

## Tests

```
uv run --all-packages pytest packages/travel
```

`tests/conftest.py` define fakes locales por duck typing (`FakeSession`, `FakeVault`, `ctx`
como `SimpleNamespace`) — no importa `edecan_db` ni `edecan_api` (`ARCHITECTURE.md` §10.1).
Todo con `respx`, sin red real.

## Qué NO hace este paquete

- No reserva ni paga nada real bajo ninguna circunstancia — ver `docs/viajes.md`.
- No implementa el resto de la superficie de Amadeus (aerolíneas de baja tarifa fuera del
  GDS, asientos, equipaje, check-in, etc.) — solo búsqueda/información + estado de vuelo.
- `environment` de Amadeus queda en `"test"` por defecto a propósito, para que nadie gaste
  cuota productiva sin querer — el tenant debe elegir `"production"` explícitamente.
