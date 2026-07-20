# packages/commerce — `edecan_commerce`

Dinero: cotizaciones de mercado (solo lectura), presupuestos por categoría y órdenes de
pago/trading que **nacen `draft` y jamás se auto-ejecutan** (`ARCHITECTURE.md` §10.7,
`ARCHITECTURE.md` §118.1 — fase v2). Ver `docs/dinero-real.md` para la
política completa (por qué, el doble gate, y qué haría falta para un broker/PSP live).

`get_all_tools() -> list[Tool]` (en `edecan_commerce/__init__.py`) es el entry point que
consume `edecan_core.ToolRegistry.load_entry_points(group="edecan.tools")`, declarado en
`pyproject.toml` como `[project.entry-points."edecan.tools"]`.

## Las 4 herramientas (nombres exactos, pinned en `ARCHITECTURE.md` §11)

| Tool | Módulo | `dangerous` | Qué hace |
|---|---|---|---|
| `cotizar_activo` | `tools.py` | No | Cotización actual de un activo + aviso "no es consejo de inversión". Solo lectura. |
| `gestionar_presupuesto` | `tools.py` | No | `fijar`\|`listar`\|`estado` de presupuestos por categoría (tabla `budgets`). |
| `preparar_pago` | `tools.py` | **Sí** | Crea un `orders(kind='payment', status='draft')`. Nunca mueve dinero. |
| `preparar_orden` | `tools.py` | **Sí** | Crea un `orders(kind='trade', status='draft')` con una cotización adjunta en `meta`. Nunca ejecuta nada. |

Las 4 requieren el flag de plan `commerce.orders` (`edecan_schemas.plans.FLAG_COMMERCE_ORDERS`,
ya definido en `PLANES` por fase v2). `preparar_pago`/`preparar_orden` son `dangerous=True`:
`edecan_core.agent.Agent.run_turn` exige confirmación humana del *tool call* antes de
correrlas — y, aun confirmado el tool call, lo único que hacen es **insertar un borrador**.
Ejecutar la orden (o generar el enlace de pago) es un paso *aparte*, solo alcanzable desde
`POST /v1/commerce/orders/{id}/confirm` (`apps/api/edecan_api/routers/commerce.py`), que
además exige su propia confirmación en la UI (`apps/web/.../ordenes`). Doble gate.

## Módulos

- **`quotes.py`** — `Quote(simbolo, precio, moneda, fuente, ts)` + protocolo `QuoteProvider`
  (`async quote(simbolo) -> Quote`). Implementaciones:
  - `StubQuotes` (default, `QUOTES_PROVIDER=stub`): precio determinista derivado de
    `sha256(simbolo)` — mismo símbolo, mismo precio, siempre, sin red. `moneda="USD"`,
    `fuente="stub"`.
  - `CoinGeckoQuotes` (`QUOTES_PROVIDER=coingecko`): `GET
    https://api.coingecko.com/api/v3/simple/price?ids={id}&vs_currencies=usd` — API
    pública oficial de CoinGecko, **sin API key**. Mapea símbolos comunes
    (BTC/ETH/SOL/USDT/USDC/BNB/XRP/ADA/DOGE/MATIC/LTC/DOT) a sus `id` de CoinGecko; símbolo
    desconocido → `ValueError` con mensaje claro (nunca inventa un precio).
  - `get_quote_provider(settings)` — resuelve por `settings.QUOTES_PROVIDER` (lectura
    defensiva `getattr(..., "stub")`, igual que `edecan_toolkit.research.get_search_provider`).
- **`paper.py`** — `PaperBroker`: ejecutor de trades en modo **simulado** ("paper"). Ver su
  docstring (grande, a propósito) para el guardrail permanente. `execute(order, session)`
  solo acepta `status="confirmed"` + `kind="trade"`; hace *upsert* de `holdings` con costo
  promedio ponderado (compra) o valida cantidad disponible (venta: error claro + el motivo
  queda en `meta.error`, SIN cambiar `status` — inventar un status fuera del enum pinned de
  `ARCHITECTURE.md` §11 rompería una futura `CHECK CONSTRAINT`; la orden sigue cancelable);
  si alcanza, marca la orden `executed_paper` + `executed_at`; y
  registra una fila real en `transactions` (categoria `inversion_paper`, cuenta `paper`) y en
  `audit_log`. Habla SQL parametrizado directo (mismo criterio que `edecan_toolkit`/
  `edecan_premium`, ver sus README) contra `orders`/`holdings` (tablas nuevas de
  `ARCHITECTURE.md` §11) y `transactions`/`audit_log` (tablas v1 ya existentes,
  `ARCHITECTURE.md` §10.3).
- **`budgets.py`** — `fijar_presupuesto`/`listar_presupuestos`/`estado_presupuestos(session,
  *, tenant_id, user_id, mes=None)`. `estado_presupuestos` reutiliza la tabla `transactions`
  v1 (`gastado` = suma de transacciones negativas del mes por categoría) para calcular `%`
  gastado vs. `monto_mensual` y `alerta=True` si `pct > 90`. Funciones puras que reciben
  `session` explícito (no `ctx`) para que tanto `tools.py` (con `ctx.session`) como
  `apps/api/edecan_api/routers/commerce.py` (con su propia sesión de tenant) las reutilicen
  sin duplicar el cálculo del porcentaje.
- **`tools.py`** — las 4 `Tool` de arriba.

## Qué es real hoy vs. qué falta (P1, léase con `docs/dinero-real.md`)

- **Real y probado hoy, sin red ni Postgres**: toda la lógica de `quotes.py` (con
  `StubQuotes`; `CoinGeckoQuotes` probado con `respx`), toda la aritmética de `paper.py`
  (costo promedio ponderado, validación de cantidad, construcción del SQL) y de
  `budgets.py` (%, alerta), y las 4 tools — todo contra un `FakeSession` que verifica el
  SQL/los parámetros exactos que se ejecutarían.
- **Persiste de verdad contra Postgres**: la migración `0003_v2_expansion` (fase v2,
  `ARCHITECTURE.md` §11) que crea las tablas `orders`/`holdings`/`budgets` ya aterrizó, con
  su modelo SQLAlchemy en `edecan_db.models` (`Order`/`Holding`/`Budget`). Este paquete
  asume el esquema pinneado en §7.4 al pie de la letra (nombres de tabla y columna
  EXACTOS), que es justo el esquema que la migración ya aplicó: este código funciona
  contra Postgres real sin ningún cambio, ni aquí ni en
  `apps/api/edecan_api/routers/commerce.py`. `transactions`/`audit_log` (usadas por
  `PaperBroker`) ya existen desde `0001_initial` y **sí** funcionan hoy.
- A diferencia de `apps/api/edecan_api/routers/remote.py` (fase v2, que usa un almacén en
  memoria por proceso como sustituto temporal de sus tablas nuevas), aquí NO se usa ese
  truco: las mismas filas de `orders`/`budgets` las crean las *tools* (invocadas dentro de
  una conversación, con la sesión de esa request) y las lee/confirma el *router* (otra
  request, otra sesión) — un almacén en memoria por proceso las volvería invisibles entre
  sí (una orden creada por el chat no aparecería en la página Órdenes, o viceversa), que es
  peor que "todavía no persiste hasta que la migración aterrice". Ver `docs/dinero-real.md`.

## Tests

```
uv run pytest packages/commerce
```

`tests/conftest.py` define fakes locales por duck typing (`FakeSession`/`FakeResult`, `ctx`
como `SimpleNamespace`) — mismo patrón que `packages/toolkit/tests/conftest.py`, duplicado a
propósito (`ARCHITECTURE.md` §10.1: los tests no importan paquetes hermanos).

| Archivo | Cubre |
|---|---|
| `test_quotes.py` | `StubQuotes` determinista; `CoinGeckoQuotes` con `respx` (éxito, símbolo desconocido, error HTTP); `get_quote_provider` resuelve por `QUOTES_PROVIDER` y cae a stub si es desconocido. |
| `test_paper.py` | `PaperBroker.execute`: rechazo si `status`/`kind` no son los esperados; compra sin holding previo; compra con holding previo (costo promedio ponderado); venta con cantidad suficiente; venta con cantidad insuficiente (error claro, orden→`error`, NUNCA toca `holdings`/`transactions`/`audit_log`); falta de cotización en `meta`. |
| `test_budgets.py` | `fijar_presupuesto` (crea/actualiza), validaciones; `listar_presupuestos`; `estado_presupuestos` (%, `alerta` con `>90%`, mes sin gasto). |
| `test_tools.py` | Las 4 tools: validaciones de argumentos, contenido/`data` devueltos, y el test explícito **`test_draft_never_executes_preparar_pago_y_preparar_orden`**: tras `preparar_pago`/`preparar_orden`, la ÚNICA sentencia SQL ejecutada es un `INSERT INTO orders ... status='draft'` — nunca un `UPDATE`, nunca `executed_paper`. |
