# Negocios — facturación ligera + KPIs + inventario (ERP básico)

`edecan_business` (`packages/business/`) + `apps/api/edecan_api/routers/negocios.py`
(`/v1/negocios/*`) + `/app/negocios` en el frontend. Corresponde al work package **P1
WP-V2-12** de `ROADMAP_V2.md` §5, pantalla 4 del mockup de `REQUISITOS_V2.md`: KPIs de
ingresos/gastos/beneficio/nuevos clientes + dona de ventas por canal + actividad reciente,
más facturación con PDF real.

Este mismo documento también cubre **inventario / ERP básico** (`apps/api/edecan_api/
routers/erp.py`, `/v1/erp/*`, `/app/inventario`), agregado en v4 por **WP-V4-06**
(`ARCHITECTURE.md` §13) sobre el MISMO paquete `edecan_business` — ver la sección
"Inventario (ERP básico)" más abajo. Antes era parte del backlog P2 de este documento (ver
"Qué queda para P2").

> **Estado**: completamente real y funcional hoy (P1 = "arquitectura sólida + implementación
> parcial segura", pero en este WP la parte construida no tiene atajos ni datos simulados —
> ver "Qué queda para P2" más abajo para lo que sí se dejó fuera a propósito).
> Verificado extremo a extremo contra Postgres y S3 (LocalStack) reales, no solo con fakes:
> registro → crear factura → PDF subido a S3 → transición de estado → KPIs reflejando la
> factura pagada, todo confirmado con una corrida real antes de cerrar este WP.

## Alcance y disclaimer

Esto es **facturación ligera para un negocio pequeño/freelance**, no un módulo de
contabilidad fiscal:

- No calcula impuestos según la jurisdicción del cliente (el `impuestos_pct` es un único
  porcentaje plano que el usuario captura a mano).
- No emite CFDI/factura electrónica timbrada ante ninguna autoridad fiscal — el PDF es un
  documento informativo/comercial, no un comprobante fiscal válido.
- No lleva un libro contable de partida doble ni concilia con un banco.
- Los "KPIs" son una vista simplificada de un ledger de transacciones que el propio usuario
  captura (`edecan_toolkit.finanzas`, v1) — no un cierre contable auditado.

**Este módulo es informativo y operativo, no un sustituto de un contador o un sistema de
facturación fiscal certificado.** Si el negocio necesita facturación fiscal real, sigue
haciendo falta un proveedor de timbrado (fuera de alcance, ver "Qué queda para P2").

## Modelo de datos

Reutiliza tres tablas: dos nuevas de este WP y dos que ya existían en v1.

- `invoices`/`invoice_items` (nuevas, migración `0003_v2_expansion`, `ROADMAP_V2.md` §7.4)
  — el documento de factura y sus líneas. `invoices.user_id` existe en el esquema pero la
  **numeración es por tenant**, no por usuario (ver más abajo): dos usuarios del mismo
  tenant facturando el mismo día comparten la secuencia `F-{año}-{n}`, como dos cajeros de
  un mismo negocio.
- `transactions` (v1, `ARCHITECTURE.md` §10.3, alimentada por
  `edecan_toolkit.finanzas.RegistrarTransaccionTool` / `POST /v1/finance/transactions`) —
  fuente única de `ingresos`/`gastos`/`beneficio` y de la dona "ventas por canal" (agrupada
  por el campo `cuenta`, reinterpretado aquí como "canal de venta").
- `contacts` (v1) — fuente de `nuevos_clientes` (cuenta los creados en el mes).
- `files` (v1) — cada PDF generado se sube ahí igual que un archivo subido a mano
  (`ARCHITECTURE.md` §10.14), y `invoices.pdf_file_id` apunta a esa fila.

## Numeración de facturas

`edecan_business.invoices.next_numero(session, *, tenant_id, year)` produce
`"F-{YYYY}-{n:04d}"`, con `n` = cantidad de facturas del tenant en ese año + 1.

**Carrera teórica, aceptada en P1**: el `SELECT COUNT(*)` y el `INSERT` posterior no están
envueltos en un bloqueo (`SELECT ... FOR UPDATE` o una secuencia dedicada por tenant) — dos
creaciones concurrentes desde el mismo tenant podrían leer el mismo `COUNT` y terminar con
el mismo `numero`. Se documenta y se acepta porque: (a) `invoices.numero` no tiene una
restricción `UNIQUE` en el esquema pinned, así que una colisión no rompe ningún `INSERT`,
solo produce un número visualmente duplicado; (b) el patrón de uso esperado en P1 es una
persona (o pocas) creando facturas una a la vez desde la UI, no un punto de venta de alto
volumen; (c) el arreglo correcto (una fila de contador con `FOR UPDATE`, o una secuencia de
Postgres por tenant) es una migración de esquema nueva, fuera del alcance de este paquete de
trabajo (no puede tocar `packages/db/alembic/`). Ver el docstring de `next_numero` y
`packages/business/tests/test_invoices.py::test_next_numero_llamadas_concurrentes_...` para
el test que fija este comportamiento como conocido.

## Totales: `Decimal` + redondeo bancario

`edecan_business.invoices.compute_totals(items, impuestos_pct)` calcula `(subtotal,
impuestos, total)` con `decimal.Decimal` en todo momento (nunca `float`) y redondeo
**bancario** (`ROUND_HALF_EVEN`) a 2 decimales — el criterio contable estándar para no
sesgar sistemáticamente hacia arriba una serie larga de empates exactos (`x.xx5`).

El detalle importante: **cada línea se redondea antes de sumar el subtotal**, no se suma en
precisión completa y se redondea solo el resultado final. Con dos líneas de `10.005` cada
una, sumar-y-luego-redondear da `20.01`; redondear-cada-línea-y-sumar (lo que hace este
código) da `20.00` — y ese segundo número es el que coincide con lo que el PDF muestra línea
por línea, así que nunca hay un centavo de diferencia entre "lo que se ve" y "lo que se
suma". Ver `packages/business/tests/test_invoices.py` (sección `compute_totals`) para los
casos exactos, verificados a mano.

## PDF

`edecan_business.invoices.render_pdf(invoice, items)` usa **fpdf2** con una fuente core
(Helvetica) — mismo límite y mismo motivo que `edecan_creative` (`docs/creatividad.md`): sin
descargar ninguna fuente TrueType (regla dura: nada de red al generar un archivo), el texto
se satura a latin-1 (ISO-8859-1); español funciona sin pérdida (acentos, `ñ`, `¿`/`¡`),
emojis y comillas tipográficas curvas se sustituyen por `'?'`.

Layout: encabezado (número, fecha de emisión, cliente, email, vencimiento), tabla de items
(descripción/cantidad/precio/total), subtotal/impuestos/total alineados a la derecha, notas
opcionales, y el pie "Generado por Edecán". El PDF se genera **en memoria** (nunca toca
disco) y se sube con el mismo layout S3 que cualquier archivo de la plataforma:

```
s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{numero}.pdf
```

...naciendo `status="ready"` en `files` (no pasa por el job asíncrono `ingest_file`: ya está
completo). Verificado con una corrida real: el PDF que llega a S3 empieza con `%PDF-1.3`,
termina con `%%EOF`, y se parseó exitosamente con `pypdf` para confirmar que cabe en una
sola página.

## Flujo de estados

`draft → sent → paid`, más `void` desde **cualquier** estado no-`void` (incluida una factura
ya `paid`, por ejemplo para corregir un error de captura):

```
        ┌────────┐   sent   ┌──────┐   paid   ┌──────┐
        │ draft  │────────► │ sent │────────► │ paid │
        └───┬────┘          └──┬───┘          └──┬───┘
            │                  │                 │
            └──────────────────┴────────void──────┘
                                ▼
                            ┌──────┐
                            │ void │
                            └──────┘
```

`draft` nunca es un destino (una factura no "vuelve" a borrador). `edecan_business.invoices.
cambiar_estado` es deliberadamente simple — solo transiciona `status` y toca `updated_at`,
sin ningún metadato adicional de cobro (no hay un campo `paid_at` dedicado; ver "Qué queda
para P2"). Una transición fuera de ese vocabulario (`POST .../estado {status}` con algo que
no sea `sent`/`paid`/`void`) es rechazada por Pydantic con `422` antes de llegar al handler;
una transición que sí es una de esas tres pero no está permitida desde el estado actual de
ESA factura (p. ej. `draft → paid`, saltándose `sent`) es `409 Conflict`
(`EstadoInvalidoError`).

## KPIs — supuestos importantes

`edecan_business.kpis.kpis_mes(session, *, tenant_id, user_id, mes)` calcula, para el mes
pedido (`"YYYY-MM"`, por defecto el actual):

| Campo | Fuente | Alcance temporal |
|---|---|---|
| `ingresos` | `transactions.monto > 0` | mes, por `fecha` |
| `gastos` | `abs(transactions.monto < 0)` | mes, por `fecha` |
| `beneficio` | `ingresos - gastos` | — |
| `nuevos_clientes` | `contacts` creados | mes, por `created_at` |
| `facturado` | `invoices.total` con `status IN ('sent','paid')` | mes, por `created_at` |
| `cobrado` | `invoices.total` con `status = 'paid'` | mes, por `updated_at` |
| `por_canal` | `transactions` con `monto > 0`, agrupadas por `cuenta` | mes, top 6 + `"otros"` |
| `actividad` | últimas 10 entre `invoices` y `transactions` | mes, mezcladas por fecha desc. |

**Por qué `ingresos` nunca incluye `facturado`/`cobrado` (anti doble conteo)**: una factura
es un DOCUMENTO — un registro/promesa de cobro —, no un ingreso por el solo hecho de
crearla o marcarla `sent`/`paid`. El cruce entre "facturé" y "de verdad recibí el dinero" lo
hace el USUARIO a mano: cuando cobra de verdad, además de marcar la factura `paid`
(`POST /v1/negocios/facturas/{id}/estado`) debe registrar la transacción de ingreso
correspondiente (`registrar_transaccion` / `POST /v1/finance/transactions`) para que
aparezca en `ingresos`. Si `ingresos` sumara `invoices.total` automáticamente, cualquier
tenant que ya registra sus ingresos a mano vería el mismo cobro contado dos veces.
`facturado`/`cobrado` quedan como métricas informativas de la cartera de facturas,
deliberadamente separadas — verificado con una corrida real: una factura de 2,325.80
marcada `paid` sin ninguna transacción asociada deja `ingresos = 0`, `facturado = cobrado =
2325.80`.

**`cobrado` se acota por `updated_at`, no `created_at`**: es la mejor aproximación
disponible hoy de "cuándo se cobró" sin una columna `paid_at` dedicada (P2). Una factura
creada en junio pero cobrada en julio cuenta en el `cobrado` de julio, no el de junio.

**Dona "ventas por canal"**: agrupa solo transacciones de INGRESO (`monto > 0`) por el campo
`cuenta` — un gasto en la misma cuenta no resta ni suma al canal. Los 6 canales de mayor
total quedan explícitos; el resto se colapsa en un bucket `"otros"` (omitido si hay 6 o
menos canales en total). Verificado con 8 canales sintéticos contra Postgres real: el top 6
queda ordenado por total descendente y "otros" suma exactamente los dos restantes.

## API (`/v1/negocios`, Bearer, sin flag de plan — todos los planes)

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/kpis?mes=YYYY-MM` | `kpis_mes` (default: mes actual) |
| `GET` | `/facturas?status=` | Lista, filtro opcional por estado |
| `POST` | `/facturas` | Crea `draft` completa (totales + numeración + PDF + S3) |
| `GET` | `/facturas/{id}` | Factura + items |
| `POST` | `/facturas/{id}/estado` | Transición `{status: sent\|paid\|void}` |

`items` no vacíos y montos `>= 0` se validan en dos capas: Pydantic (`min_length=1`, `ge=0`,
`422` inmediato) y `edecan_business` (reglas semánticas que Pydantic no puede expresar, como
`cliente_nombre` en blanco después de `.strip()` — también `422`). El router **no**
reimplementa nada de `edecan_business`; solo hace el mapeo HTTP.

## Herramientas del agente

- `crear_factura` — mismos argumentos que `POST /facturas`; crea el borrador completo.
  `dangerous=False`: un borrador de documento no mueve dinero (enviarlo por correo usaría
  `enviar_correo`, que trae su propio gate).
- `estado_negocio` — resumen ejecutivo del mes (`{mes?}`) en texto listo para el chat.

## Frontend (`/app/negocios`)

Fila de 4 tarjetas KPI, dona SVG a mano (`stroke-dasharray`/`stroke-dashoffset` sobre
círculos apilados, sin librería de gráficos — `ROADMAP_V2.md` §7.10), actividad reciente,
formulario de factura con items dinámicos y totales en vivo (mismo cálculo de redondeo que
el backend), tabla de facturas con acciones de estado, selector de mes. Componentes en
`apps/web/src/components/negocios/`; fetchers en `apps/web/src/lib/api-negocios.ts`
(duplica el pequeño bloque de fetch autenticado de `lib/api.ts`, igual que
`lib/api-remoto.ts`/`lib/api-ide.ts`, porque ese archivo compartido está fuera del alcance
de este WP). Español, dark/light con los tokens existentes, cero dependencias npm nuevas.

Limitación conocida y deliberada: no hay un botón de "descargar PDF" — `GET /v1/files/{id}`
(v1) solo devuelve metadatos, no el contenido; `/app/archivos` (v1) tampoco expone descarga
hoy. Agregar un endpoint de descarga/URL firmada es una mejora futura que toca
`apps/api/edecan_api/routers/files.py`, fuera del alcance de este paquete de trabajo.

---

## Inventario (ERP básico)

`edecan_business.inventory` (mismo paquete `packages/business/`, extendido en v4 por
**WP-V4-06**) + `apps/api/edecan_api/routers/erp.py` (`/v1/erp/*`) + `/app/inventario` en el
frontend. Corresponde a la categoría "🏢 Negocios / ERP" de `REQUISITOS_V2.md` — inventario
estaba marcado P2 en `ROADMAP_V2.md` §5 y ahora es código real (`ARCHITECTURE.md` §13,
contrato pinned de tablas/flags que aportó WP-V4-01 en paralelo).

> **Estado**: real y funcional, mismo criterio de "sin datos simulados" que el resto de este
> documento — SQL parametrizado contra el esquema pinned, nunca un stub. Verificado con la
> suite automatizada offline (`FakeSession` programable, ver "Tests" al final).

### Alcance

Inventario ligero para un negocio pequeño/freelance — productos con SKU único, stock por
unidad (fraccionario, `numeric(14,3)`: soporta kg/litros/etc., no solo unidades enteras),
movimientos auditables (entrada/salida/ajuste) y alertas de stock bajo. **Esto NO es**:

- Un sistema de costeo por lotes (FIFO/promedio ponderado/etc.) — `costo` es un campo plano
  por producto, no una cola de compras a distintos precios.
- Multi-almacén: el stock es un único número por producto y tenant, no por
  ubicación/bodega.
- Trazabilidad de lote o número de serie.

### Modelo de datos

Dos tablas nuevas (migración `0006_v4_expansion`, `ARCHITECTURE.md` §13.b — modelos
SQLAlchemy `Product`/`StockMove` en `edecan_db.models`):

- `products(tenant_id, user_id, sku, nombre, descripcion default '', unidad default
  'unidad', precio numeric(14,2) nullable, costo numeric(14,2) nullable, stock
  numeric(14,3) default 0, stock_minimo numeric(14,3) default 0, activo default true)` +
  `UNIQUE(tenant_id, sku)`.
- `stock_moves(tenant_id, user_id, product_id FK → products ON DELETE CASCADE, delta
  numeric(14,3), motivo, nota default '', ref nullable)` — una fila por movimiento, nunca
  se edita ni se borra (es el historial de auditoría del stock). `motivo` NO tiene un
  `CHECK` a nivel de base de datos (vocabulario abierto a propósito, ver el docstring de
  `StockMove`) — la única validación es en `edecan_business.inventory`: debe ser uno de
  `compra`/`venta`/`ajuste`/`merma`/`devolucion`.

Gateado por el flag de plan `erp.inventory` (`edecan_schemas.plans.FLAG_ERP_INVENTORY`,
`ARCHITECTURE.md` §13.c): `True` en `free_selfhost`/`hosted_pro`/`hosted_business`, `False`
en `hosted_basic` — mismo patrón exacto que `commerce.orders` (v2).

### Un producto nace SIEMPRE con stock 0

`crear_producto` no acepta un `stock` inicial como argumento: la ÚNICA forma de que
`products.stock` cambie es `registrar_movimiento`, que inserta la fila de `stock_moves` y
hace `UPDATE products SET stock = stock + :delta` en la MISMA transacción (sin commit
intermedio — mismo principio que `crear_factura`: varias escrituras relacionadas, una sola
transacción de request). Si un producto arranca con existencias reales, el flujo es: crear
el producto y luego un movimiento con `motivo='ajuste'` para dejar el conteo inicial
asentado — así nunca hay stock "de la nada" sin su fila de auditoría correspondiente.

`sku` se normaliza (recorta espacios + mayúsculas: `" abc-001 "` y `"ABC-001"` son el MISMO
producto) y es la identidad permanente del producto — **no es editable** después de creado
(`PATCH /productos/{id}` lo ignora en silencio si viene en el cuerpo, Pydantic no declara
ese campo en `ProductoUpdateIn`). Crear un producto con un `sku` que ya existe en el tenant
(esté activo o no — `UNIQUE(tenant_id, sku)` es incondicional) es `409 Conflict`.

### Movimientos: nunca dejan stock negativo, salvo `motivo='ajuste'`

Con `motivo` ∈ {`compra`, `venta`, `merma`, `devolucion`}, un movimiento que dejaría
`products.stock` negativo se RECHAZA (`400 Bad Request`) — refleja la realidad física de un
inventario: no se puede vender/mermar lo que no se tiene. Con `motivo='ajuste'` SÍ se
permite quedar en negativo: es la vía explícita de corrección administrativa (reconciliar
un conteo físico, corregir una captura anterior).

`delta` nunca puede ser cero (un movimiento que no cambia nada no deja ningún rastro útil
que auditar) — `422` si se intenta.

### `activo` se edita, no se "desactiva" con una ruta HTTP aparte

No existe `POST /productos/{id}/desactivar`: `PATCH /productos/{id} {"activo": false}` es
la ÚNICA vía HTTP para desactivar (y `{"activo": true}` para reactivar) un producto —
soft-delete, nunca se borra una fila que puede tener movimientos históricos en
`stock_moves`. La tool del agente `gestionar_inventario` sí expone una acción dedicada
`desactivar_producto` (más natural en una conversación que pedirle al modelo que arme un
PATCH parcial), pero por debajo llama a la MISMA función
(`edecan_business.inventory.desactivar_producto`, un atajo sobre `editar_producto(...,
activo=False)`).

### Resumen de inventario

`edecan_business.inventory.resumen_inventario` calcula, sobre productos **activos
únicamente** (uno inactivo no cuenta para nada de esto):

| Campo | Cómo se calcula |
|---|---|
| `total_skus` | Cantidad de productos activos |
| `valor_costo` | `Σ stock · costo` (ignora productos sin `costo` asignado) |
| `valor_precio` | `Σ stock · precio` (ignora productos sin `precio` asignado) |
| `stock_bajo` | Productos con `stock <= stock_minimo` — empatar EXACTO en el mínimo ya cuenta como alerta, no hace falta cruzarlo |

### API (`/v1/erp`, Bearer, flag `erp.inventory`)

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/productos?activo=&q=` | Lista, filtros opcionales (`q` busca por sku/nombre) |
| `POST` | `/productos` | Crea un producto (`stock` nace en 0). `409` si el sku ya existe |
| `PATCH` | `/productos/{id}` | Edición parcial — incluye `activo` (des/reactivar) |
| `POST` | `/productos/{id}/movimientos` | `{delta, motivo, nota?, ref?}` → `201`. `400` si dejaría stock negativo sin `motivo='ajuste'` |
| `GET` | `/resumen` | SKUs activos, valor a costo/precio, alertas de stock bajo |

Cada escritura (`POST /productos`, `PATCH /productos/{id}`, `POST .../movimientos`) deja
una fila en `audit_log` (acciones `erp.product.created`/`erp.product.updated`/
`erp.stock_move.created`). El router **no** reimplementa nada de `edecan_business`; solo
hace el mapeo HTTP (Pydantic + status codes + `audit_log`) — mismo criterio que
`routers/negocios.py`.

### Herramientas del agente

- `gestionar_inventario` — `{accion: crear_producto|editar_producto|registrar_movimiento|
  desactivar_producto, sku, ...}`. El producto SIEMPRE se identifica por `sku`, nunca por su
  `id`/UUID interno (ninguna tool de este repo le pide un UUID crudo al modelo — mismo
  criterio que `edecan_toolkit.contactos`). `dangerous=False`: modifica datos propios del
  negocio del usuario, no dinero real ni nada que salga del tenant.
- `estado_inventario` — `{consulta?}`: sin `consulta`, el resumen general; con `consulta`,
  busca productos activos por sku/nombre.

Ambas requieren el flag `erp.inventory` (`requires_flags`).

### Ejemplo (conversación con el agente)

```
Usuario: dame de alta un producto "Cable USB-C 1m", sku CBL-001, precio 8.99, costo 3.50
Asistente: [gestionar_inventario: crear_producto] Creé el producto «Cable USB-C 1m» (sku CBL-001).

Usuario: llegaron 50 unidades del CBL-001, fue una compra
Asistente: [gestionar_inventario: registrar_movimiento delta=50 motivo=compra]
           Registré el movimiento de «CBL-001» (motivo: compra). Stock actual: 50.00.

Usuario: ¿cómo va el inventario?
Asistente: [estado_inventario] Inventario: 1 SKUs activos.
             Valor a costo: $175.00
             Valor a precio de venta: $449.50
           Sin alertas de stock bajo.
```

### Frontend (`/app/inventario`)

Tarjeta de resumen (SKUs, valor a costo/precio, alertas de stock bajo), formulario único de
crear/editar (el mismo componente cambia de modo según si hay un producto seleccionado —
`sku` queda deshabilitado en modo edición porque no es editable), tabla de productos (badge
rojo en el stock cuando `stock <= stock_minimo`), modal "Registrar movimiento"
(Entrada/Salida/Ajuste — la UI arma el signo del `delta` según el tipo elegido; solo
"Ajuste" permite escribirlo con signo a mano), buscador con debounce manual (300 ms, sin
dependencias npm nuevas) y filtro activos/inactivos/todos. Componentes en `apps/web/src/
components/inventario/`; fetchers en `apps/web/src/lib/api-inventario.ts` (duplica el
bloque de fetch autenticado de `lib/api.ts`, incluido el manejo de
`TOTP_REQUIRED_DETAIL` en el refresh de `/v1/auth/refresh` — mismo criterio que
`lib/api-negocios.ts`, ver su docstring/`HOTFIXES_PENDIENTES.md` punto 2). Español,
dark/light con los tokens existentes, cero dependencias npm nuevas. La entrada de
navegación ("Inventario", `/app/inventario`) la agregó WP-V4-01 en
`components/layout/nav-items.ts`.

### Qué queda para P2 (dentro de inventario)

- **Costeo por lotes**: FIFO/promedio ponderado — hoy `costo` es un único valor plano por
  producto, no una cola de compras a distintos precios.
- **Multi-almacén**: el stock es un número único por producto/tenant, sin ubicación/bodega.
- **Trazabilidad de lote/número de serie.**
- **Reordenar automático**: hoy `stock_minimo` solo genera una alerta visible/en el resumen
  — no dispara ninguna acción (p. ej. una orden de compra sugerida).
- **Reporte de movimientos**: no hay todavía un endpoint/página para listar el historial de
  `stock_moves` de un producto (solo se usa internamente al registrar cada movimiento) —
  mejora futura razonable sobre `GET /v1/erp/productos/{id}/movimientos`, fuera del alcance
  de este paquete de trabajo.

---

## Qué queda para P2

- ~~**Inventario**: control de stock, SKUs, costo de venta.~~ Implementado en v4
  (WP-V4-06) — ver "Inventario (ERP básico)" arriba; lo que queda pendiente DENTRO de
  inventario está listado en esa sección, no acá.
- **RRHH/nómina**: empleados, recibos de pago, retenciones.
- **Impuestos reales**: timbrado fiscal (CFDI o equivalente por país), retenciones por
  jurisdicción, integración con un proveedor certificado.
- **`paid_at` dedicado**: hoy `cobrado` se aproxima con `updated_at`; una columna propia
  sería más precisa si una factura pasa por más transiciones intermedias en el futuro.
- **Descarga de PDF**: URL firmada o endpoint de contenido en `routers/files.py` (v1).
- **Numeración sin carrera**: contador con `FOR UPDATE` o secuencia de Postgres por tenant.
- **Multi-moneda real**: hoy los KPIs suman `monto`/`total` sin agrupar por `moneda` (mismo
  criterio que `GET /v1/finance/summary` en v1) — un tenant que factura en más de una moneda
  ve una suma mezclada, no convertida.

## Tests

```
cd packages/business && PYTHONPATH=. pytest      # 130 tests: facturación (numeración,
                                                   # Decimal/redondeo, PDF, kpis_mes,
                                                   # transiciones de estado) + inventario
                                                   # (products/stock_moves, movimientos,
                                                   # resumen) + las 4 tools del paquete
uv run pytest apps/api/tests/test_negocios_router.py   # 25 tests: contrato HTTP de /v1/negocios
uv run pytest apps/api/tests/test_erp_router.py        # 29 tests: contrato HTTP de /v1/erp
```

Offline y determinista: `FakeSession` programable (`packages/business/tests/conftest.py`,
mismo patrón que `packages/commerce/tests/conftest.py`, y una copia local equivalente en
cada archivo de test de router, `ARCHITECTURE.md` §10.1) y `monkeypatch`/sustitución directa
de las funciones de `edecan_business` importadas en cada router — ninguna llamada de red ni
de Postgres real en la suite de CI.

`test_inventory.py` (42 tests) cubre `edecan_business.inventory` a fondo: normalización de
`sku`, validaciones de cada campo, el rechazo/permiso de stock negativo según `motivo`, y el
resumen (valorización + detección de stock bajo, incluidos los empates exactos en el
mínimo). `test_erp_router.py` (29 tests) cubre el contrato HTTP: gate de flag (`403` con
`hosted_basic`, camino feliz con `hosted_pro`), `409` de sku duplicado, `400` de stock
negativo, `404` de producto inexistente, y que cada escritura deje su fila en `audit_log`.

Además de la suite automatizada, la parte de facturación de este documento (WP-V2-12) se
verificó con una corrida real end-to-end (Postgres + LocalStack S3 reales, sin fakes):
registro de tenant → `POST /facturas` con dos items e impuestos → PDF real subido y
confirmado válido con `pypdf` → `GET /facturas/{id}` → transición `draft→sent→paid` →
intento de transición ilegal (`409` confirmado) → `GET /kpis` reflejando
`facturado`/`cobrado` sin inflar `ingresos` → un segundo lote de transacciones sintéticas en
8 canales para confirmar `ingresos`/`gastos`/`beneficio`/`nuevos_clientes`/`por_canal` (top 6
+ "otros") contra agregaciones SQL reales, no simuladas. Esa corrida encontró y permitió
corregir un bug real de layout del PDF (el pie empujaba una página 2 en blanco) que ningún
test con `FakeSession` podía haber atrapado — ver el docstring de `render_pdf` y el test de
regresión correspondiente. La parte de inventario (WP-V4-06) se verificó con la suite
automatizada offline más `tsc`/`next lint`/`next build` reales sobre el frontend completo
(sin una corrida contra Postgres real: la migración `0006_v4_expansion` la aportó WP-V4-01
en paralelo dentro de la misma ventana de trabajo, pero este paquete no tenía un Postgres
real disponible para una corrida end-to-end propia — el SQL parametrizado sigue el mismo
esquema pinned al pie de la letra, mismo criterio que `edecan_api.routers.commerce` documenta
para `orders`/`holdings`/`budgets`).
