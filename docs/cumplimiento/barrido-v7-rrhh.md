# Barrido v7 — RRHH/nómina: esquema real, plan-flag, evidencia en rollback y BYO (WP-V7-01)

Este documento registra el barrido dedicado de la vertical RRHH/nómina de v5
(`apps/api/edecan_api/routers/rrhh.py`, `packages/business/edecan_business/rrhh.py`) contra
los cuatro patrones de bug que las auditorías v4-v6 encontraron repetidamente en el resto del
repo (ver `HOTFIXES_PENDIENTES.md` y `docs/seguridad-modelo-amenazas.md`, referencias
canónicas leídas completas antes de escribir una línea de este WP):

- **BARRIDO D** — esquema SQL asumido vs. esquema real (patrón del bug de v6 en
  `apps/api/edecan_api/routers/reuniones.py`/`process_meeting.py`).
- **BARRIDO B** — flag de plan de grano fino aplicado a TODAS las superficies, no solo
  algunas (patrón de v5/v6, `usar_computadora`/`delegar_mision`/`confirm_tool_call`).
- **BARRIDO C** — escritura de evidencia legal/cumplimiento perdida en un rollback de sesión
  (patrón de los puntos 8/9 de `HOTFIXES_PENDIENTES.md`, `crear_clon_voz`, `hooks.py`,
  `_apply_optout`).
- **BARRIDO A** — bring-your-own (RRHH no debería depender de ninguna credencial de terceros).
- **Guardrail de dinero real**: la nómina es SIEMPRE borrador, nunca ejecuta un pago.

**Resumen ejecutivo**: los cuatro barridos con nombre confirmaron que `rrhh.py` (router +
capa de negocio) ya estaba correcto — no hizo falta ningún fix en esos cuatro frentes,
consistente con que WP-V5-08/v6 ya lo habían construido/verificado con cuidado. La
verificación empírica de BARRIDO D (Postgres real, nunca antes corrida para esta vertical —
`docs/rrhh.md` lo documentaba explícitamente como pendiente) sí encontró y cerró **un
hallazgo real de concurrencia** en `resolver_ausencia` — fuera de los cuatro barridos con
nombre, pero dentro del archivo que sí es alcance de este WP
(`packages/business/edecan_business/rrhh.py`) y del mismo espíritu de "endurecer" que pide
`DIRECCION_ACTUAL.md` para v7 (ver la sección dedicada más abajo).

---

## BARRIDO D — esquema asumido vs. esquema real

**Metodología**: comparación columna por columna de CADA `INSERT`/`UPDATE`/`SELECT` de
`packages/business/edecan_business/rrhh.py` y `apps/api/edecan_api/routers/rrhh.py` contra
`packages/db/alembic/versions/0007_v5_expansion.py` (la migración real, nunca el docstring del
propio paquete) y `packages/db/edecan_db/models.py` — más una corrida real contra Postgres
migrado de verdad (ver más abajo), no solo lectura.

### Tabla de columnas: código vs. migración real vs. Postgres real (`\d` verificado)

| Tabla | Columna que el código lee/escribe | Migración `0007_v5_expansion` | Postgres real (`\d`, verificado) |
|---|---|---|---|
| `employees` | `tenant_id, user_id, nombre, email, puesto, salario_mensual, moneda, fecha_ingreso, status` | Coincide exacto (`salario_mensual numeric(14,2) NULL`, `moneda char(3) DEFAULT 'USD'`, `status text sin CHECK`) | Coincide exacto |
| `employees.meta` | Nunca se lee/escribe en ningún CRUD (columna reservada) | `jsonb NOT NULL DEFAULT '{}'` | `jsonb NOT NULL DEFAULT '{}'::jsonb` — confirmado que el default aplica solo (INSERT no la menciona) |
| `time_off` | `tenant_id, employee_id, kind, desde, hasta, status, notas` — **SIN `user_id` NI `approved_at`** (confirmado, ningún INSERT/UPDATE los menciona) | Sin esas 2 columnas — confirmado | Sin esas 2 columnas — confirmado (`\d time_off`) |
| `time_off.status` | Solo produce `pending`→`approved`/`rejected`; filtra por los 4 valores | `CHECK IN ('pending','approved','rejected','cancelled')` | `ck_time_off_status CHECK (status = ANY (ARRAY['pending','approved','rejected','cancelled']))` |
| `payroll_runs` | `tenant_id, user_id, periodo, status, total, moneda, notas, approved_at` — **`total` es LA ÚNICA columna de totales** (sin `deducciones_pct` propia) | Coincide exacto — sin `deducciones_pct`, `total numeric(14,2) DEFAULT 0` | Coincide exacto (`\d payroll_runs`, sin `deducciones_pct`) |
| `payroll_runs.status` | Produce `draft`→`approved`/`cancelled`; filtra por los 4 valores (incluye `paid`, nunca escrito) | `CHECK IN ('draft','approved','paid','cancelled')` | `ck_payroll_runs_status`, mismos 4 valores |
| `payroll_items` | `tenant_id, payroll_run_id, employee_id, bruto, deducciones, neto` | Coincide exacto | Coincide exacto |
| Todas las 4 | `id`, `created_at`, `updated_at` (mixins) | `IDMixin`/`TimestampMixin` | Coincide (`gen_random_uuid()`, `timestamptz DEFAULT now()`) |

**FakeSession/mock rows de los tests preexistentes** (`apps/api/tests/test_rrhh_router.py`,
`packages/business/tests/test_rrhh.py`): también verificados columna por columna contra la
migración real — `_ausencia_row()` (`test_rrhh_router.py`) NO incluye `user_id`/`approved_at`
(con una aserción explícita `assert "user_id" not in params_insert` en
`test_create_ausencia_happy_path`); `_payroll_run_row()` NO incluye `deducciones_pct`; ambos
ya estaban alineados con el esquema real ANTES de este WP — el "hallazgo invisible por mocks
calcando el mismo error" que sí ocurrió en `reuniones.py` (v6) no se repite aquí.

### Verificación empírica FUERTE (hecha)

Postgres desechable, migrado de verdad hasta `head` (incluye `0007_v5_expansion` y las 3
migraciones posteriores):

```
docker run -d --name edecan-v7-rrhh-pg -e POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan \
  -e POSTGRES_DB=edecan -p 55471:5432 pgvector/pgvector:pg16
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:55471/edecan \
  uv run --all-packages alembic -c packages/db/alembic.ini upgrade head
```

`\d employees`/`\d time_off`/`\d payroll_runs`/`\d payroll_items` contra el Postgres real
confirmaron la tabla de arriba columna por columna (tipos, nullability, `CHECK`, FKs,
políticas RLS) — sin ninguna discrepancia.

Test nuevo `packages/business/tests/test_rrhh_integration.py` (`@pytest.mark.integration`,
gateado por `DATABASE_URL`, se salta solo si no hay Postgres alcanzable — mismo patrón que
`packages/db/tests/test_rls.py`): ejercita las funciones REALES de `edecan_business.rrhh`
(nunca mocks) de punta a punta —

- `test_ciclo_completo_empleado_ausencia_nomina_contra_postgres_real` — crea un empleado,
  lo edita, lo lista/lee por id/nombre, registra una ausencia, la lista, la aprueba, calcula
  una nómina con deducciones, la lista/lee, la aprueba, y confirma que cancelar una ya
  aprobada lanza `EstadoNominaError` — TODO contra SQL real, columna por columna.
- `test_registrar_ausencia_solapada_con_aprobada_rechaza_contra_postgres_real`.
- `test_calcular_nomina_excluye_inactivos_y_sin_salario_contra_postgres_real` — confirma el
  filtro `status = 'active' AND salario_mensual IS NOT NULL` contra datos reales.
- `test_resolver_ausencia_concurrente_se_serializa_con_for_update` /
  `test_aprobar_nomina_concurrente_se_serializa_con_for_update` — ver la sección de
  concurrencia más abajo.
- `test_rls_aisla_tablas_rrhh_entre_tenants` — extiende la garantía de
  `packages/db/tests/test_rls.py` (que solo cubre `usage_events`, tabla de v1) a las 4 tablas
  nuevas de `0007_v5_expansion`: dos tenants, confirma que ninguno ve filas del otro en
  `employees`/`time_off`/`payroll_runs` aun pasando el `tenant_id` ajeno a mano como
  argumento (la política de la base de datos es la última línea de defensa, no el código
  llamador).

`uv run --all-packages pytest -q packages/business/tests/test_rrhh_integration.py` (con
`DATABASE_URL` apuntando al contenedor) → **6 passed**. Contenedor detenido y borrado al
terminar (`docker rm -f edecan-v7-rrhh-pg`), verificado con `docker ps -a` (ver sección final).

**Veredicto BARRIDO D: Correcto.** El esquema asumido por `rrhh.py`/`edecan_business/rrhh.py`
coincide EXACTO con la migración real y con Postgres real migrado de verdad — ningún fix de
esquema hizo falta. `docs/rrhh.md` dejaba esto pendiente de verificación empírica
explícitamente ("No hubo una corrida contra Postgres real disponible para este work
package") — ese hueco queda cerrado.

### Hallazgo real encontrado durante BARRIDO D (fuera de los 4 barridos con nombre): carrera en `resolver_ausencia`

Al ejercitar las funciones reales bajo concurrencia (parte del objetivo "ejercita las
funciones reales" de BARRIDO D), se encontró que `resolver_ausencia` — a diferencia de
`aprobar_nomina`/`cancelar_nomina`, que sí usan `_lock_payroll_run` (`SELECT ... FOR UPDATE`)
— leía `status` con un `SELECT` PLANO, sin ningún lock de fila:

```python
actual = (
    await session.execute(
        text("SELECT status FROM time_off WHERE id = :id ::uuid AND tenant_id = :tenant_id ::uuid"),
        {...},
    )
).mappings().first()
if actual["status"] != _AUSENCIA_STATUS_PENDING:
    raise EstadoAusenciaError(...)
# ... UPDATE sin repetir `AND status = 'pending'` en el WHERE ...
```

**Reproducido contra Postgres real, función SIN modificar, 3/3 corridas**: dos llamadas
concurrentes sobre LA MISMA ausencia `pending` (una `aprobar=True`, otra `aprobar=False`)
podían AMBAS leer `status='pending'` (ninguna había comiteado todavía), ambas pasar el chequeo
Python, y ambas ejecutar su `UPDATE` (el `WHERE` del `UPDATE` solo filtra por `id`/
`tenant_id`, nunca por `status` — el invariante "solo una vez" vivía ÚNICAMENTE en el chequeo
Python contra una lectura ya obsoleta para quien perdiera la carrera del `UPDATE`). Resultado:
**las DOS llamadas "tenían éxito"** (ninguna lanzaba `EstadoAusenciaError`), la segunda en
comitear pisaba silenciosamente el resultado de la primera — exactamente el mismo tipo de bug
que `inventory.registrar_movimiento` tenía y se corrigió en v4, y el mismo patrón que
`_lock_payroll_run` ya existía para prevenir en la nómina de este mismo archivo, pero nunca se
replicó para `time_off`.

**Fix**: `_lock_time_off` (`packages/business/edecan_business/rrhh.py`) — mismo patrón EXACTO
que `_lock_payroll_run`/`inventory.registrar_movimiento`: `SELECT * FROM time_off WHERE
id = :id ::uuid AND tenant_id = :tenant_id ::uuid FOR UPDATE` ANTES de leer `status`.
`resolver_ausencia` ahora llama a `_lock_time_off` en vez de su `SELECT` plano anterior — el
resto de la función no cambia. **Re-verificado 3/3 corridas limpias tras el fix**: exactamente
una llamada tiene éxito, la otra encuentra la ausencia ya resuelta y lanza
`EstadoAusenciaError` de forma determinista.

**Severidad**: media — no es dinero real ni evidencia legal (a diferencia de los patrones de
BARRIDO C), es una inconsistencia de datos de negocio (una ausencia podría quedar
`'approved'` cuando dos personas la resolvían casi simultáneamente con decisiones distintas,
sin que nadie recibiera un error indicándolo). Vector de explotación: dos usuarios con acceso
a la pantalla RRHH aprobando/rechazando la misma solicitud casi al mismo tiempo — plausible en
un negocio con más de una persona con permisos de RRHH.

**Tests de regresión** (dos capas, mismo criterio que el resto del repo):
1. `packages/business/tests/test_rrhh.py::test_resolver_ausencia_select_inicial_usa_for_update`
   — unitario con `FakeSession`, confirma `"FOR UPDATE" in sql_select` (mismo patrón que
   `test_aprobar_nomina_select_inicial_usa_for_update`).
2. `packages/business/tests/test_rrhh_integration.py::
   test_resolver_ausencia_concurrente_se_serializa_con_for_update` — contra Postgres real,
   dos tareas `asyncio` sincronizadas con una barrera (`asyncio.Event`) para maximizar la
   ventana de carrera, confirma exactamente 1 éxito + 1 `EstadoAusenciaError` y que el estado
   final en la base de datos coincide con el único resultado exitoso.

`uv run --all-packages pytest -q packages/business/tests/test_rrhh.py
packages/business/tests/test_tools.py` → **120 passed** (sin regresiones tras el fix).

---

## BARRIDO B — flag de plan `erp.hr` en TODAS las superficies

**Router (`apps/api/edecan_api/routers/rrhh.py`)**: las 11 rutas del contrato pinned
(`GET`/`POST /empleados`, `PATCH /empleados/{id}`, `GET`/`POST /ausencias`, `PATCH
/ausencias/{id}`, `GET`/`POST /nominas`, `GET /nominas/{id}`, `POST /nominas/{id}/aprobar`,
`POST /nominas/{id}/cancelar`) declaran `current_user: CurrentUser = Depends(_require_erp_hr)`
— confirmado por lectura línea por línea de las 11 firmas Y por un test programático nuevo
(`test_las_11_rutas_pinned_estan_cubiertas_por_este_barrido`, compara el set de rutas reales
del router, vía introspección de `rrhh_module.router.routes`, contra la lista de 11 que este
barrido ejercita — avisa si algún día se agrega/quita una ruta sin actualizar la cobertura).

**Tools del agente (`packages/business/edecan_business/tools.py`)**: las 3 tools de RRHH
(`GestionarEmpleadoTool`, `RegistrarAusenciaTool`, `PrepararNominaTool`) ya declaraban
`requires_flags = frozenset({FLAG_ERP_HR})` — confirmado por lectura y por
`apps/api/tests/test_v6_sweep_flags.py` (WP-V6-02, no tocado en este WP), que ya pinnea la
paridad tool↔router para las 3 (`rrhh:GestionarEmpleadoTool`/`RegistrarAusenciaTool`/
`PrepararNominaTool` vs. `rrhh_module._require_erp_hr`).

**Test nuevo**: `apps/api/tests/test_v7_sweep_rrhh.py::test_todas_las_rutas_rrhh_exigen_flag_erp_hr`
(parametrizado, 11 casos — uno por ruta) apaga `erp.hr` en memoria sobre `hosted_basic` y
confirma `403` + `fake_session.llamadas == []` para las 11, cerrando el hueco real de
cobertura que dejaba `test_rrhh_router.py` (que solo ejercitaba el gate de flag para 2 de las
11: `GET`/`POST /empleados`). También `test_las_3_tools_rrhh_siguen_apuntando_al_mismo_gate_que_este_router`
— confirmación liviana (no reimplementa la matriz completa de `test_v6_sweep_flags.py`) de
que las 3 tools siguen exigiendo exactamente `FLAG_ERP_HR`.

`uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_rrhh.py -k "flag_erp_hr or
gate"` → **13 passed** (11 rutas + 2 confirmaciones estructurales).

**Veredicto BARRIDO B: Correcto.** Las 11 rutas y las 3 tools exigían el flag desde antes de
este WP — ningún fix hizo falta; se agregó la cobertura de test que faltaba (9 de las 11
rutas no tenían un `403` explícito pinneado).

---

## BARRIDO C — evidencia en rollback (sitios de escritura del router)

**`aprobar_nomina_endpoint`/`cancelar_nomina_endpoint`** (los 2 únicos sitios de este router
que escriben `audit_log`, vía el helper local `_audit`): ya verificados "Seguro" por el
barrido dedicado v6 (`docs/cumplimiento/barrido-evidencia-v6.md`, tabla "Resto del barrido —
`apps/api/edecan_api/routers/`", fila `rrhh.py`) — **confirmado intacto SIN rehacer el
análisis**: releído el código real, `EstadoNominaError` (409) se sigue lanzando ANTES de
`_audit()` en ambos handlers (nunca después), y ninguno de los dos hace una llamada de red que
pudiera fallar después de auditar (la nómina nunca mueve dinero — ver el guardrail más abajo).
`apps/api/tests/test_v6_sweep_evidencia.py` (WP-V6-03, no tocado) ya pinnea
`test_aprobar_nomina_camino_feliz_nunca_comitea_su_propia_sesion` (`fake_session.commits ==
0`) — re-ejecutado sin cambios: **9 passed** (archivo completo). Este WP agrega
`test_cancelar_nomina_camino_feliz_nunca_comitea_su_propia_sesion` en
`test_v7_sweep_rrhh.py` (cierra el hueco puntual: `cancelar_nomina_endpoint` estaba marcado
"Seguro" en la tabla pero sin un `commits`-counter DEDICADO como el que sí tiene
`aprobar_nomina_endpoint`) + los dos casos de error (`test_cancelar_nomina_error_de_estado_no_comitea`,
`test_create_*_error_de_negocio_no_comitea` de abajo).

**Sitios de escritura RESTANTES** (alta de empleado, ausencia, generar nómina — los 3 que el
enunciado pedía barrer explícitamente): ninguno de los tres llama a `_audit()`/escribe
`audit_log` — **no hay ninguna fila de "evidencia legal/cumplimiento" que un rollback pudiera
perder**, a diferencia de `aprobar`/`cancelar` nómina (que sí dejan un registro de auditoría).
Los tres siguen el mismo patrón seguro que el resto del router: toda validación que puede
`raise` (Pydantic, `ValueError`/`AusenciaSolapadaError` de la capa de negocio) ocurre ANTES de
cualquier `INSERT`, y el `INSERT` en sí es la ÚLTIMA operación de la rama feliz (el handler
retorna inmediatamente después, sin ninguna otra operación de sesión que pudiera fallar).

| Endpoint | Función de negocio | ¿Escribe `audit_log`? | ¿Hay un `raise` alcanzable DESPUÉS del INSERT/UPDATE de negocio? | Veredicto |
|---|---|---|---|---|
| `POST /empleados` | `crear_empleado` | No | No — valida todo (nombre/email/salario/status) antes del único `INSERT`; el handler retorna justo después | **Seguro** |
| `PATCH /empleados/{id}` | `editar_empleado` | No | No — mismo patrón, un solo `UPDATE` como última operación | **Seguro** |
| `POST /ausencias` | `registrar_ausencia` | No | No — las dos posibles excepciones (`ValueError` de validación, `AusenciaSolapadaError` de solapamiento) ocurren ANTES del `INSERT` (el chequeo de solapamiento es un `SELECT`, no puede fallar por datos de negocio) | **Seguro** |
| `PATCH /ausencias/{id}` | `resolver_ausencia` | No | No — `EstadoAusenciaError` se lanza ANTES del `UPDATE` (tras el `SELECT ... FOR UPDATE`, WP-V7-01) | **Seguro** |
| `POST /nominas` | `calcular_nomina` | No | No — el único INSERT de negocio (`payroll_runs` + `payroll_items`, todo el cálculo aritmético en Python ocurre ANTES de tocar la sesión) es la última operación de la rama feliz | **Seguro** |
| `GET *` (5 rutas) | solo `SELECT` | No | N/A — sin escritura | **N/A** |

**Test nuevo** (`apps/api/tests/test_v7_sweep_rrhh.py`, mismo patrón `commits`-counter que
`test_v6_sweep_evidencia.py`): `test_create_empleado_camino_feliz_nunca_comitea_su_propia_sesion`
/ `test_create_empleado_error_de_negocio_no_comitea`,
`test_create_ausencia_camino_feliz_nunca_comitea_su_propia_sesion` /
`test_create_ausencia_solapada_no_comitea`,
`test_create_nomina_camino_feliz_nunca_comitea_su_propia_sesion` /
`test_create_nomina_error_de_negocio_no_comitea` — 6 tests, uno por combinación
camino-feliz/error de los 3 sitios, todos confirman `fake_session.commits == 0`
independientemente del resultado (200/201 o 409/422): consistente con que ninguno de los 3
TENÍA un `raise` alcanzable después de escribir, así que ninguno necesitaba jamás el patrón de
commit explícito.

`uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_rrhh.py -k "comitea"` → **8
passed** (6 nuevos + `aprobar`/`cancelar` de nómina).

**Veredicto BARRIDO C: Correcto en los 5 sitios de escritura del router.** Ningún fix de
rollback/commit hizo falta — a diferencia de los hallazgos de v5/v6 (`crear_clon_voz`,
`hooks.py`, `_apply_optout`), `rrhh.py` nunca tuvo el patrón "escribe evidencia y LUEGO
alcanza un `raise`": los 2 sitios que sí escriben evidencia (`aprobar`/`cancelar` nómina) la
escriben como última operación; los 3 restantes no escriben ninguna evidencia que proteger.

---

## BARRIDO A — bring-your-own (RRHH no debería depender de terceros)

Confirmado por lectura completa de ambos archivos (`apps/api/edecan_api/routers/rrhh.py`,
`packages/business/edecan_business/rrhh.py`): **ningún import de `httpx`/proveedor
HTTP/SDK de terceros, ningún acceso a `ctx.settings`/`self._settings`/`os.environ`, ninguna
credencial de ningún tipo.** La única dependencia externa de todo el módulo es
`sqlalchemy`/la propia sesión de base de datos del tenant (`AsyncSession` inyectada por
`Depends(get_tenant_session)`) — coherente con que RRHH es 100% datos propios del negocio del
tenant (empleados/ausencias/nómina en borrador), sin ningún proveedor bring-your-own que
resolver (a diferencia de `credentials.py`/`viajes.py`/`voz_avanzada.py`, que sí lo hacen).

**Test nuevo** (AST, no grep de texto — evita falsos negativos de un simple `in`, mismo
criterio que `packages/skills/tests/test_v6_seguridad_privilegios.py`):
`apps/api/tests/test_v7_sweep_rrhh.py::test_router_rrhh_nunca_lee_settings_ni_credenciales_de_terceros`
— parsea el AST de `rrhh.py` y confirma que NINGÚN nodo `ast.Attribute` referencia
`.settings`/`.environ`/`.getenv`, y que el módulo no importa `os`.

**Veredicto BARRIDO A: Correcto.** Nada que corregir.

---

## Guardrail de dinero real: la nómina NUNCA mueve dinero

Confirmado por lectura completa (router + capa de negocio): `calcular_nomina` es aritmética +
persistencia pura (`payroll_runs.status` nace SIEMPRE `'draft'`, vía `server_default`/literal
`'draft'` en el `INSERT`, nunca decidido por el llamador); `aprobar_nomina` es un cambio de
`status` (`draft → approved`) + `approved_at`, sin ninguna llamada de red ni a un proveedor de
pagos. El router exige `{"confirmar": true}` EXPLÍCITO antes de aprobar (`400` sin él,
`NominaAprobarIn.confirmar` nace en `False`) y tanto la respuesta HTTP como
`PrepararNominaTool.description`/`ToolResult.content` repiten literalmente: *"No mueve
dinero: genera un borrador contable que apruebas tú en la pantalla RRHH."*

**Test nuevo**: `apps/api/tests/test_v7_sweep_rrhh.py::
test_router_rrhh_no_importa_ningun_cliente_de_pagos_ni_hace_llamadas_de_red` — confirma que
`rrhh.py` no importa `httpx`/`stripe`/`requests`/`aiohttp` en ninguna forma.
`docs/rrhh.md` ya documentaba este guardrail con el mismo rigor desde WP-V5-08 — sigue
vigente y sin cambios.

**Veredicto: Correcto.** Nada que corregir; doc y código coinciden.

---

## Resumen de archivos tocados

| Archivo | Qué cambió |
|---|---|
| `packages/business/edecan_business/rrhh.py` | Fix real: `_lock_time_off` (`FOR UPDATE`) + `resolver_ausencia` lo usa — cierra la carrera encontrada en BARRIDO D |
| `packages/business/tests/test_rrhh.py` | +1 test (`test_resolver_ausencia_select_inicial_usa_for_update`) |
| `packages/business/tests/test_rrhh_integration.py` | Nuevo — 6 tests de integración contra Postgres real (BARRIDO D) |
| `apps/api/tests/test_v7_sweep_rrhh.py` | Nuevo — 23 tests (BARRIDO B: 11 rutas + 2 estructurales; BARRIDO C: 8; BARRIDO A: 1; dinero: 1) |
| `apps/api/edecan_api/routers/rrhh.py` | Sin cambios — verificado correcto en los 4 barridos |
| `apps/api/tests/test_rrhh_router.py` | Sin cambios — verificado correcto, cobertura complementada (no reemplazada) por `test_v7_sweep_rrhh.py` |
| `docs/rrhh.md` | Documenta el fix de `resolver_ausencia`, la verificación empírica contra Postgres real, y los 2 archivos de test nuevos |
| `docs/cumplimiento/barrido-v7-rrhh.md` | Este documento |

## Verificación final

```
uv run --all-packages pytest -q apps/api/tests/test_rrhh_router.py \
  apps/api/tests/test_v7_sweep_rrhh.py packages/business/tests -m "not integration"
```
→ **286 passed, 6 deselected** (los 6 deselected son
`test_rrhh_integration.py`, gateados por `DATABASE_URL` — nunca fallan, solo se saltan sin
ella).

Con `DATABASE_URL` apuntando al Postgres desechable (mismo comando, sin el filtro `-m`):
**292 passed** (los 6 de integración incluidos).

Sanity check adicional (archivos estáticos que referencian `rrhh.py` desde fuera de este WP,
sin re-litigar su contenido): `uv run --all-packages pytest -q
apps/api/tests/test_v6_sweep_flags.py apps/api/tests/test_v6_sweep_evidencia.py` → **55
passed**, sin regresiones por el cambio en `edecan_business/rrhh.py`.

`uv run --all-packages ruff check` sobre los 7 archivos de este WP → **All checks passed!**.

**Nota sobre `ruff format --check`**: reporta 5 de los 7 archivos como "would reformat" —
verificado que es una deriva PRE-EXISTENTE de versión del formateador (`ruff 0.15.20`
instalado hoy reescribe el patrón `(await session.execute(...)).mappings().first()` a una
forma distinta con paréntesis anidados), **no causada por este WP**: afecta por igual a
funciones que este WP nunca tocó dentro de `rrhh.py` (`crear_empleado`, `obtener_empleado`,
etc.) y a archivos completamente ajenos (`packages/business/edecan_business/inventory.py`,
`invoices.py`, `apps/api/edecan_api/routers/erp.py` — ninguno de los tres en el alcance de
este WP, confirmado que TAMBIÉN están "would reformat" con el mismo ruff instalado). Es un
asunto de versión de herramienta a nivel de repo, no de este paquete de trabajo — no se
corrió `ruff format` sobre ningún archivo (hubiera producido un diff masivo, puramente
cosmético, mezclado con el fix real de `resolver_ausencia`, dificultando la revisión). El
comando de verificación pedido explícitamente por el encargo (`ruff check`, no `ruff format
--check`) pasa limpio.

## Docker

Contenedor `edecan-v7-rrhh-pg` (puerto 55471) creado, usado para la verificación empírica de
BARRIDO D, y **detenido/borrado al terminar** (`docker rm -f edecan-v7-rrhh-pg`). Verificado
con `docker ps -a`: no queda ningún contenedor con ese nombre. (Un contenedor
`edecan-v7-resid-pg`, de otra sesión de trabajo concurrente sobre este mismo repo, se dejó
intacto — no es de este WP, no se tocó.)
