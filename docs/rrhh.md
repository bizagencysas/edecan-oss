# RRHH — empleados, ausencias y nómina en borrador

`edecan_business.rrhh` (extiende `packages/business/`, mismo paquete que ya usan
`invoices.py`/`kpis.py` de WP-V2-12 e `inventory.py` de WP-V4-06) + `apps/api/edecan_api/
routers/rrhh.py` (`/v1/rrhh/*`) + `/app/rrhh` en el frontend. Corresponde a la categoría
"RRHH, nómina, reclutamiento" del wishlist de `REQUISITOS_V2.md` — `ROADMAP_V2.md` la había
dejado en P2 ("tras validar facturación"); `DIRECCION_ACTUAL.md` levantó esa barrera para v5.
Contrato pinned en `ARCHITECTURE.md` §14 (WP-V5-01, ya aterrizado: migración
`0007_v5_expansion`, flag `erp.hr`, nombres exactos de tools).

> **Estado**: real y funcional, mismo criterio de "sin datos simulados" que `docs/negocios.md`
> — SQL parametrizado contra el esquema pinned, nunca un stub. Verificado con la suite
> automatizada offline (`FakeSession` programable, ver "Tests" al final), contra la app real
> montada vía `edecan_api.main.create_app()` (`V5_ROUTER_NAMES`, ya incluye `"rrhh"`), y desde
> WP-V7-01 también **empíricamente contra Postgres real migrado** (columna por columna, más
> las dos carreras `FOR UPDATE` bajo concurrencia real) — ver
> `packages/business/tests/test_rrhh_integration.py` y
> `docs/cumplimiento/barrido-v7-rrhh.md`.

## Alcance y disclaimer

Esto es **RRHH ligero para un negocio pequeño/freelance**, no un sistema de nómina fiscal:

- **Los cálculos de nómina son informativos, no asesoría fiscal ni laboral.** No calcula
  impuestos sobre la renta, seguridad social, ni ninguna retención obligatoria según
  jurisdicción — `deducciones_pct` es un único porcentaje plano que la persona usuaria
  captura a mano (p. ej. "quiero ver el neto después de un 10% de deducciones"), no un motor
  de nómina certificado.
- **No emite recibos de nómina timbrados** ante ninguna autoridad — no hay PDF ni documento
  fiscal, solo registros en base de datos.
- **No gestiona reclutamiento** (vacantes, candidatos, entrevistas) — el wishlist de
  `REQUISITOS_V2.md` menciona "reclutamiento" en la misma categoría, pero este work package
  cubre solo la mitad "empleados/nómina"; reclutamiento queda para un WP de seguimiento.
- Si el negocio necesita nómina fiscal real (retenciones correctas, timbrado, cumplimiento
  legal), sigue haciendo falta un proveedor de nómina certificado o una persona contadora —
  **este módulo no sustituye a ninguno de los dos.**

## GUARDRAIL INNEGOCIABLE: la nómina JAMÁS mueve dinero

Misma política permanente que `docs/dinero-real.md` ("dinero real nunca se mueve solo"),
aplicada aquí con el mismo rigor que `packages/commerce`/`packages/ads`:

- `calcular_nomina` (`POST /v1/rrhh/nominas`) **solo hace aritmética y persistencia**: calcula
  bruto/deducciones/neto por empleado y guarda un `payroll_run` en `status='draft'` +
  sus `payroll_items`. No hay ninguna llamada de red, ningún proveedor de pagos, nada que
  salga del tenant.
- "Aprobar" una nómina (`POST /v1/rrhh/nominas/{id}/aprobar`) es un acto de **REGISTRO**, no
  de pago: pasa `draft → approved` dentro del propio sistema. Exige un cuerpo
  `{"confirmar": true}` EXPLÍCITO (`400 Bad Request` sin él) — segundo gate de confirmación,
  mismo espíritu de doble confirmación que `POST /v1/commerce/orders/{id}/confirm`/
  `POST /v1/ads/borradores/{id}/confirmar`.
- **El pago en sí lo hace la persona dueña del negocio por su propio medio**, fuera de este
  sistema (transferencia bancaria, efectivo, la plataforma de nómina que ya use) — Edecán
  nunca lo ejecuta ni se conecta a ningún proveedor de pagos para hacerlo.
- La tool del agente `preparar_nomina` es `dangerous=True` (exige confirmación del *tool
  call* en el chat, `ARCHITECTURE.md` §10.7) — mismo criterio que `preparar_pago`/
  `preparar_orden` (v2) y `vehiculo_controlar` (v4): cualquier acción con peso financiero
  exige aprobación humana explícita, aunque —como en este caso— lo único que produzca sea
  un borrador. Ver también `docs/dinero-real.md`.

Tanto la `description` de `preparar_nomina` como el texto de cada respuesta HTTP relevante
(`ToolResult.content`, `POST .../aprobar`) repiten literalmente el aviso: **"No mueve dinero:
genera un borrador contable que apruebas tú en la pantalla RRHH."**

## Modelo de datos

Cuatro tablas nuevas (migración `0007_v5_expansion`, `ARCHITECTURE.md` §14.b — modelos
SQLAlchemy `Employee`/`TimeOff`/`PayrollRun`/`PayrollItem` en `edecan_db.models`, sección
"v5"). **Importante**: varias columnas NO son lo que uno esperaría a primera vista — quedan
documentadas explícitamente porque el contrato de este work package se escribió ANTES de que
la migración aterrizara en el árbol (mismo criterio ya aceptado por `inventory.py` con
`0006_v4_expansion` en v4: "codea contra el contrato pinned aunque la migración todavía no
haya aterrizado") y luego se alineó al pie de la letra una vez la migración real llegó.

- `employees(tenant_id, user_id, nombre, email nullable, puesto default '', salario_mensual
  numeric(14,2) nullable, moneda char(3) default 'USD', fecha_ingreso date nullable, status
  default 'active', meta jsonb default '{}')` — el roster del negocio.
  - `salario_mensual` (NO "salario") es **nullable a propósito**: un empleado puede existir
    sin compensación asignada todavía. `calcular_nomina` solo toma empleados `active` **con**
    `salario_mensual` asignado — los demás se excluyen en silencio (no es un error).
  - `status` queda **texto abierto a nivel de base de datos** (sin `CHECK`, mismo criterio
    que `tenants.status`) — este módulo SÍ valida `active`/`inactive` en la capa de negocio
    (`edecan_business.rrhh`), un subconjunto razonable del vocabulario abierto de la columna.
  - `meta` (jsonb libre) **no se expone en ningún CRUD de este módulo** — sin un caso de uso
    pinned todavía, queda siempre en su default `{}` (ver "Qué queda para P2").
- `time_off(tenant_id, employee_id FK → employees ON DELETE CASCADE, kind, desde date, hasta
  date, status default 'pending', notas default '')` — una ausencia/solicitud de vacaciones.
  - **Sin `user_id` ni `approved_at`** — a diferencia de `stock_moves`/`invoices`, esta tabla
    no registra quién la creó ni cuándo se aprobó, solo su `status` actual y `updated_at`.
  - `notas` (NO "nota", plural) — nota libre opcional.
  - `status` SÍ lleva `CHECK IN ('pending','approved','rejected','cancelled')` — máquina de
    estados explícita. Este work package implementa `pending → approved`/`pending →
    rejected` (`resolver_ausencia`); `cancelled` existe en el esquema pero **ningún camino de
    este work package lo alcanza todavía** (ver "Qué queda para P2"). Sí es un valor
    filtrable en `GET /ausencias?status=cancelled` por si otra vía llega a fijarlo.
- `payroll_runs(tenant_id, user_id, periodo, status default 'draft', total numeric(14,2)
  default 0, moneda char(3) default 'USD', notas default '', approved_at nullable)` — una
  corrida de nómina.
  - **`total` es una ÚNICA columna** (no hay `total_bruto`/`total_deducciones` separados en
    la tabla): representa el **NETO final** de la corrida (`Σ payroll_items.neto`).
    `POST /v1/rrhh/nominas` y `GET /v1/rrhh/nominas/{id}` SÍ devuelven `total_bruto`/
    `total_deducciones` como campos **CALCULADOS** (sumando `payroll_items` en Python, nunca
    persistidos) para que el frontend muestre el desglose sin una consulta aparte.
  - **Sin `deducciones_pct` propia**: el porcentaje de deducciones es un parámetro de
    CÁLCULO de `calcular_nomina` (afecta cada `payroll_items.deducciones`), no algo que la
    corrida recuerde como columna.
  - `status` lleva `CHECK IN ('draft','approved','paid','cancelled')` — nace SIEMPRE `draft`
    (mismo espíritu que `ad_drafts`/`orders`: ninguna fila paga nada real por sí sola).
    `'paid'` existe en el esquema pinned (para cuando un WP futuro registre "esto ya se pagó
    de verdad, fuera de este sistema") pero **este work package nunca lo escribe** — solo
    implementa `draft → approved` (`aprobar_nomina`) y `draft → cancelled`
    (`cancelar_nomina`). Sí es un valor filtrable en `GET /nominas?status=paid`.
- `payroll_items(tenant_id, payroll_run_id FK → payroll_runs ON DELETE CASCADE, employee_id
  FK → employees, bruto numeric(14,2), deducciones numeric(14,2) default 0, neto
  numeric(14,2))` — una línea por empleado incluido en la corrida (tabla "hija", igual
  criterio que `invoice_items`/`stock_moves`).

Gateado por el flag de plan `erp.hr` (`edecan_schemas.plans.FLAG_ERP_HR`, `ARCHITECTURE.md`
§14.c): a diferencia de `erp.inventory` (v4), este flag está en **`True` en los 4 planes**
("básico", mismo criterio que `companion`) — ningún plan comercial lo bloquea.

## Empleados: identificados por nombre en el chat, por id en la web

Mismo criterio que `inventory.py` con `sku`: ninguna tool del agente le pide al modelo un
`id`/UUID crudo. `gestionar_empleado`/`registrar_ausencia` resuelven el empleado por
`nombre` (case-insensitive, `obtener_empleado_por_nombre`; si hay dos con el mismo nombre,
gana el más antiguo). El router HTTP (`PATCH /empleados/{id}`, consumido por la web, que sí
tiene el UUID a mano tras `GET /empleados`) usa `id` directo.

**`nombre` NO es un campo editable de la tool `gestionar_empleado`** (a propósito, mismo
motivo que `sku` en `GestionarInventarioTool`): es el identificador con el que se resuelve al
empleado para `actualizar`/`desactivar`, así que mezclarlo con "campo a cambiar" dejaría
inalcanzable el mensaje de "no indicaste ningún campo". Renombrar un empleado existente es
una acción de la pantalla web (`PATCH /v1/rrhh/empleados/{id}` sí acepta `nombre`), no del
chat.

## Ausencias: solapamiento solo contra otras YA aprobadas

`registrar_ausencia` rechaza (`409 Conflict`, `AusenciaSolapadaError`) un rango de fechas que
se solapa con OTRA ausencia **`approved`** del MISMO empleado. Dos solicitudes `pending` que
se solapan entre sí **no** se rechazan al registrarlas — ambas son válidas de dejar
pendientes; quien las resuelve (`PATCH /ausencias/{id}`) decide cuál prevalece. `desde` no
puede ser posterior a `hasta` (`422`).

## Nómina: cálculo, borrador y aprobación

`calcular_nomina(periodo, deducciones_pct?, moneda?, notas?)`:

1. Toma cada empleado `status='active'` con `salario_mensual` asignado (los demás quedan
   fuera en silencio — no es un error, es información: la corrida deja ver cuántos empleados
   entraron).
2. Por cada uno: `bruto = salario_mensual`; `deducciones = round(bruto · deducciones_pct /
   100, 2)` (0 si se omite `deducciones_pct`); `neto = bruto - deducciones`.
   `deducciones_pct` es un porcentaje **GLOBAL** (0-50, validado) aplicado por igual a todos
   los empleados de la corrida — no hay deducciones individuales todavía (ver "Qué queda para
   P2").
3. Inserta `payroll_runs` (`status='draft'`, `total` = Σ neto) + todos sus `payroll_items` en
   **una sola transacción** de request (sin commit intermedio, mismo principio que
   `crear_factura`).
4. Si ningún empleado califica, igual se crea el `draft` con 0 items y `total=0` — **nunca**
   es un error: una nómina vacía es información válida.

Todos los montos son `Decimal` con redondeo bancario (`ROUND_HALF_EVEN`) a 2 decimales en
cada paso — igual criterio que `invoices.compute_totals`, JAMÁS `float`.

`aprobar_nomina`/`cancelar_nomina` **solo funcionan desde `'draft'`**: reaprobar/cancelar una
corrida que ya no está en `draft` lanza `EstadoNominaError` (`409 Conflict` — cubre tanto una
transición fuera de vocabulario como la **idempotencia explícita** de una doble aprobación).
Reutiliza el patrón de `inventory.registrar_movimiento`: el `SELECT` que alimenta el chequeo
de estado usa `FOR UPDATE`, así que dos aprobaciones concurrentes sobre la MISMA corrida se
serializan — la segunda SIEMPRE encuentra la corrida ya `'approved'` y cae en el `409`, nunca
aprueba dos veces.

**Corrección (WP-V7-01, 2026-07-09)**: `resolver_ausencia` (aprobar/rechazar una ausencia)
tenía el MISMO tipo de carrera check-then-act pero SIN el lock `FOR UPDATE` que sí protege a
`aprobar_nomina`/`cancelar_nomina` — dos llamadas concurrentes sobre la MISMA ausencia
`pending` (p. ej. "aprobar" y "rechazar" disparadas casi al mismo tiempo por dos personas
revisando la misma solicitud) podían AMBAS "tener éxito" sin que ninguna lanzara
`EstadoAusenciaError`, violando el invariante "solo se resuelve una vez". Reproducido 3/3
corridas contra Postgres real con la función sin modificar, corregido con `_lock_time_off`
(mismo patrón `FOR UPDATE`) y re-verificado 3/3 corridas limpias — ver
`docs/cumplimiento/barrido-v7-rrhh.md` para el detalle completo y
`packages/business/tests/test_rrhh_integration.py::
test_resolver_ausencia_concurrente_se_serializa_con_for_update` para la regresión permanente
contra Postgres real.

## API (`/v1/rrhh`, Bearer, flag `erp.hr`)

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/empleados?status=&q=` | Lista, filtros opcionales (`q` busca nombre/email/puesto) |
| `POST` | `/empleados` | Crea un empleado (`201`) |
| `PATCH` | `/empleados/{id}` | Edición parcial (incluye `status` para des/reactivar) |
| `GET` | `/ausencias?employee_id=&status=` | Lista, filtros opcionales |
| `POST` | `/ausencias` | Registra `pending` (`201`); `404` empleado inexistente; `409` solapamiento |
| `PATCH` | `/ausencias/{id}` | `{"accion": "aprobar"\|"rechazar"}`; `409` si no está `pending` |
| `GET` | `/nominas?status=` | Lista corridas (sin items) |
| `POST` | `/nominas` | `{periodo, deducciones_pct?, moneda?, notas?}` → borrador (`201`) |
| `GET` | `/nominas/{id}` | Corrida + items + `total_bruto`/`total_deducciones` calculados |
| `POST` | `/nominas/{id}/aprobar` | Exige `{"confirmar": true}` (`400` sin él); `409` si no `draft` |
| `POST` | `/nominas/{id}/cancelar` | `409` si no está en `'draft'` |

Cada aprobación/cancelación de nómina deja una fila en `audit_log` (acciones
`rrhh.payroll.approved`/`rrhh.payroll.cancelled`) — mismo criterio que `erp.py`/`commerce.py`.
El router **no** reimplementa nada de `edecan_business`; solo hace el mapeo HTTP (Pydantic +
status codes + `audit_log`).

## Herramientas del agente

- `gestionar_empleado` — `{accion: crear|actualizar|desactivar|listar, nombre, email?,
  puesto?, salario_mensual?, status?, consulta?}`. `dangerous=False`: datos propios del
  negocio, no dinero real ni nada que salga del tenant.
- `registrar_ausencia` — `{empleado, kind, desde, hasta, notas?}`. `dangerous=False`.
- `preparar_nomina` — `{periodo, deducciones_pct?}`. `dangerous=True` (ver el guardrail
  arriba) — genera el borrador vía `calcular_nomina`; su `description` y su `ToolResult`
  repiten literalmente: *"No mueve dinero: genera un borrador contable que apruebas tú en la
  pantalla RRHH."*

Las tres requieren el flag `erp.hr`.

### Ejemplo (conversación con el agente)

```
Usuario: dame de alta a Ana Pérez como Gerente de Ventas, salario 2500
Asistente: [gestionar_empleado: crear] Creé el empleado «Ana Pérez».

Usuario: Ana va a estar de vacaciones del 1 al 5 de agosto
Asistente: [registrar_ausencia] Registré la ausencia de «Ana Pérez» (vacaciones,
           2026-08-01 a 2026-08-05). Queda pendiente de aprobación en la pantalla RRHH.

Usuario: prepárame la nómina de julio con 10% de deducciones
Asistente: [se detiene, pide confirmación del tool call — dangerous=True]
Usuario: sí, adelante
Asistente: [preparar_nomina periodo=2026-07 deducciones_pct=10]
           Borrador de nómina de 2026-07 generado: 1 empleado(s), neto total USD 2,250.00.
           No mueve dinero: genera un borrador contable que apruebas tú en la pantalla RRHH.
```

## Frontend (`/app/rrhh`)

Tres pestañas (Empleados / Ausencias / Nómina), un solo layout de página con navegación por
tabs (mismo patrón que `/app/ide`, sin un componente `Tabs` compartido en `components/ui.tsx`
— botones con `border-b-2` a mano):

- **Empleados**: tabla + formulario único de alta/edición (mismo patrón que
  `ProductoForm`/`ProductosTable` de `/app/inventario` — el mismo componente cambia de modo
  según si hay un empleado seleccionado), badge de estado, botón activar/desactivar.
- **Ausencias**: tabla de solicitudes con filtro por estado; las `pending` muestran botones
  Aprobar/Rechazar inline; formulario para registrar una nueva ausencia.
- **Nómina**: selector de periodo (`<input type="month">`, mismo patrón que `/app/negocios`)
  + porcentaje de deducciones opcional, botón "Generar nómina"; tabla de corridas con su
  `total`/estado; al seleccionar una corrida, detalle con la tabla de `items` (bruto/
  deducciones/neto por empleado) y desglose bruto/deducciones/neto; botón "Aprobar" abre un
  modal de confirmación que repite el texto "Esto NO mueve dinero..." (mismo patrón visual
  que el `ConfirmModal` de `/app/ordenes`, con el aviso ámbar) y solo entonces llama
  `POST .../aprobar {confirmar: true}`; botón "Cancelar" sin modal (no mueve nada tampoco,
  pero es reversible con menor fricción — crear una nueva nómina es trivial).

Componentes en `apps/web/src/components/rrhh/`; fetchers en `apps/web/src/lib/api-rrhh.ts`
(duplica el bloque de fetch autenticado de `lib/api.ts`, incluido el manejo de
`TOTP_REQUIRED_DETAIL` en el refresh de `/v1/auth/refresh` — mismo criterio EXACTO que
`lib/api-negocios.ts`, ver su docstring/`HOTFIXES_PENDIENTES.md` punto 2). Español, dark/light
con los tokens existentes, cero dependencias npm nuevas. La entrada de navegación ("RRHH",
`/app/rrhh`) ya la agregó WP-V5-01 en `components/layout/nav-items.ts`.

## Qué queda para P2

- **Reclutamiento** (vacantes, candidatos, entrevistas) — mitad de la categoría "RRHH,
  nómina, reclutamiento" del wishlist que este work package NO cubre.
- **Deducciones individuales por empleado** (IMSS/ISR/seguridad social real, préstamos,
  faltas) — hoy `deducciones_pct` es un único porcentaje global por corrida, no una tabla de
  conceptos por empleado.
- **`time_off.status='cancelled'`**: el valor existe en el `CHECK` pinned y es filtrable, pero
  ningún endpoint/tool de este work package lo produce (p. ej. "el empleado quiere retirar su
  propia solicitud pendiente").
- **`payroll_runs.status='paid'`**: existe en el `CHECK` pinned para cuando un WP futuro
  quiera registrar "esto ya se pagó de verdad, fuera de este sistema" (p. ej. tras confirmar
  una transferencia bancaria manual) — este work package nunca lo escribe.
- **`employees.meta`** (jsonb libre): columna reservada sin caso de uso todavía; ningún CRUD
  de este módulo la toca.
- **Recibos de pago en PDF**: a diferencia de `invoices` (que sí genera un PDF real), una
  nómina aprobada no produce ningún documento descargable.
- **Reportes de nómina histórica** (comparar periodos, exportar a CSV/Excel): hoy solo hay
  listado + detalle por corrida individual.

## Tests

```
uv run --all-packages pytest -q packages/business/tests/test_rrhh.py       # lógica de negocio
uv run --all-packages pytest -q packages/business/tests/test_tools.py      # las 3 tools + las
                                                                            # 4 preexistentes
uv run --all-packages pytest -q apps/api/tests/test_rrhh_router.py         # contrato HTTP
uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_rrhh.py       # WP-V7-01: las 11
                                                                            # rutas exigen el
                                                                            # flag + evidencia
                                                                            # en rollback
uv run --all-packages pytest -q packages/business/tests/test_rrhh_integration.py  # WP-V7-01:
                                                                            # Postgres real
                                                                            # (ver abajo)
```

Offline y determinista: `FakeSession` programable (`packages/business/tests/conftest.py` para
la capa de negocio; una copia local equivalente en `test_rrhh_router.py`/
`test_v7_sweep_rrhh.py`, `ARCHITECTURE.md` §10.1) — ninguna llamada de red ni de Postgres real
en esas suites. `test_rrhh_router.py` cubre el gate de flag (`403` apagándolo en memoria sobre
`hosted_basic` con `monkeypatch`, ya que `erp.hr` es `True` en los 4 planes reales — ver el
docstring del archivo), el camino feliz de cada endpoint, `404`/`409`/`422` según corresponda,
la doble aprobación de nómina, y que `POST .../aprobar` sin `{"confirmar": true}` responda
`400` sin tocar la base de datos. `test_v7_sweep_rrhh.py` (WP-V7-01) extiende el gate de flag
a las 11 rutas (no solo 2), confirma que los sitios de escritura restantes (alta de empleado,
ausencia, generar nómina) nunca comitean su propia sesión — mismo criterio que
`test_v6_sweep_evidencia.py` ya verificó para `aprobar_nomina`/`cancelar_nomina` —, y confirma
por AST que el router no lee ninguna credencial de terceros ni importa un cliente de pagos.

**Verificación empírica contra Postgres real (WP-V7-01, cierra el hueco que este documento
dejaba pendiente)**: `packages/business/tests/test_rrhh_integration.py`
(`@pytest.mark.integration`, gateado por `DATABASE_URL`, mismo patrón que
`packages/db/tests/test_rls.py`) migra un Postgres desechable hasta `0007_v5_expansion` y
ejercita las funciones reales de `edecan_business.rrhh` de punta a punta: el ciclo de vida
completo (empleado → ausencia → nómina → aprobación), el rechazo por solapamiento, la
exclusión de inactivos/sin-salario en `calcular_nomina`, aislamiento RLS entre dos tenants
sobre las 4 tablas nuevas, y — lo más importante — las DOS carreras `FOR UPDATE`
(`resolver_ausencia`/`aprobar_nomina` vs. `cancelar_nomina`) bajo concurrencia REAL de
Postgres (dos transacciones simultáneas disputando el mismo lock de fila, no algo que una
`FakeSession` pueda ejercitar). Encontró y cerró un hallazgo real en el proceso: ver la nota
de "Corrección (WP-V7-01, 2026-07-09)" en la sección de Nómina arriba. Detalle completo en
`docs/cumplimiento/barrido-v7-rrhh.md`.
