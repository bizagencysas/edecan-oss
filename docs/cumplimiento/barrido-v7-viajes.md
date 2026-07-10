# Barrido v7 — Viajes (WP-V7-02): fuga BYO, plan-flag, evidencia vs. rollback, esquema real de `orders`

Este documento registra el barrido dedicado de la vertical **Viajes**
(`apps/api/edecan_api/routers/viajes.py` + `packages/travel/edecan_travel/`:
`amadeus.py`, `tracking.py`, `providers.py`, `tools.py`) — el primero que la
recibe: el barrido bring-your-own de v5 (`WP-V5-02`, ver
`HOTFIXES_PENDIENTES.md` sección "Barrido v5") cubrió 11 dominios pero
**explícitamente no incluyó `packages/travel`** entre ellos, así que esta
vertical llegó a v7 genuinamente sin auditar. Referencias canónicas leídas
antes de escribir una línea de este WP: `HOTFIXES_PENDIENTES.md` completo
(los 4 patrones de bug ya documentados: fuga de credencial v4/Polly v5,
plan-flag-bypass v6/v7, evidencia-vs-rollback v6, y el bug de esquema de
`reuniones.py`/`process_meeting.py` en v6), `docs/cumplimiento/
barrido-evidencia-v6.md` (formato de este informe + el veredicto ya dado ahí
para las 4 credenciales de `viajes.py`), y `ARCHITECTURE.md` §14 (contrato
pinned de v5 para `packages/travel`).

**Resultado global: LIMPIO en los 4 criterios — cero hallazgos que corregir.**
No se modificó ningún archivo de producción (`viajes.py`, `amadeus.py`,
`tracking.py`, `providers.py`, `tools.py`): la vertical ya seguía, desde que
se construyó en v5 (WP-V5-09), el mismo patrón bring-your-own correcto que
`packages/ads`. Lo que este WP SÍ aporta: **regresiones nuevas que anclan
cada veredicto** (para que un cambio futuro que reintroduzca cualquiera de
los 4 patrones de bug falle en CI en vez de descubrirse en producción), una
**verificación empírica contra Postgres real** del esquema de `orders`
(BARRIDO D), y una corrección puntual de un docstring de test desactualizado
(no un bug de comportamiento).

---

## Alcance auditado

| archivo | líneas | rol |
|---|---|---|
| `apps/api/edecan_api/routers/viajes.py` | 531 | Router HTTP: 4 credenciales (Amadeus/AfterShip), status, 3 proxies de búsqueda/rastreo |
| `packages/travel/edecan_travel/amadeus.py` | 368 | `AmadeusClient` — OAuth2 `client_credentials`, búsqueda de vuelos/hoteles/horarios |
| `packages/travel/edecan_travel/tracking.py` | 179 | `AfterShipClient` — rastreo de paquetes, solo lectura |
| `packages/travel/edecan_travel/providers.py` | 280 | `get_tenant_travel_provider`/`get_tenant_tracking_provider` — resolución tenant→proveedor→stub |
| `packages/travel/edecan_travel/tools.py` | 448 | Las 5 tools del agente (`buscar_vuelos`, `buscar_hoteles`, `estado_vuelo`, `rastrear_paquete`, `preparar_reserva`) |

---

## BARRIDO A — Fuga bring-your-own (prioridad alta, dominio genuinamente sin auditar)

Metodología (misma que v5, `HOTFIXES_PENDIENTES.md` "Barrido v5"): lectura
línea por línea buscando (a) el patrón v4 clásico
(`config.campo or getattr(self._settings, "X", None)` / `os.environ`) y (b)
el patrón v5-Polly (un SDK de terceros que resuelve credenciales AMBIENTE por
dentro, invisible a un grep de `self._settings`/`os.environ`) — aplicando la
pregunta correcta: *"¿este proveedor tiene AL MENOS UN campo de credencial
que el tenant deba traer, y qué pasa si llega vacío?"*.

| archivo | veredicto | evidencia |
|---|---|---|
| `amadeus.py` (`AmadeusClient`) | **LIMPIO** | Constructor recibe `api_key`/`api_secret` SIEMPRE explícitos (`__init__(self, api_key, api_secret, *, environment="test", http_client=None, timeout=...)`); cero atributos `self._settings`, cero `os.environ`. Sin SDK de terceros: `httpx.AsyncClient` puro (a diferencia de `PollyTTS`/`aioboto3`, que sí resuelve una cadena de credenciales AWS ambiente por dentro). |
| `tracking.py` (`AfterShipClient`) | **LIMPIO** | Mismo criterio: `__init__(self, api_key, *, http_client=None, timeout=...)`, sin `settings`, sin SDK de terceros. Header `as-api-key` armado con la credencial recibida, nunca leída de otro lado. |
| `providers.py` (`get_tenant_travel_provider`/`get_tenant_tracking_provider`) | **LIMPIO** | Lee ÚNICAMENTE `ctx.tenant_id`/`ctx.session`/`ctx.vault` — **nunca** `ctx.settings`, en NINGÚN punto de la función (verificado con un objeto "veneno" que revienta ante cualquier `getattr`, ejercitando el camino MÁS profundo: bundle real → construye `AmadeusClient`/`AfterShipClient`). Sin cuenta conectada, o si cualquier paso de la resolución falla (vault caído, JSON corrupto, campo faltante), degrada a `StubTravelProvider`/`StubTrackingProvider` — nunca a una "credencial de plataforma", porque no existe ningún nivel intermedio de ese tipo para Amadeus/AfterShip (a diferencia del hallazgo #1 de v4 en `edecan_llm.router`). |
| `tools.py` (5 tools) | **LIMPIO** | Las 4 tools de solo lectura delegan 100% en `get_tenant_travel_provider`/`get_tenant_tracking_provider` (ya limpios arriba) vía un `provider_resolver` inyectable — no tocan credenciales directamente. `preparar_reserva`/`_crear_reserva_draft` (la única `dangerous=True`) NUNCA llama a Amadeus ni resuelve ningún proveedor — lo único que hace es un `INSERT INTO orders`, ver BARRIDO D. |
| `viajes.py` (`put_credentials`, `put_rastreo_credentials`) | **LIMPIO** | `_ping_amadeus`/`_ping_aftership` son funciones módulo-level que reciben la credencial como parámetro explícito (`api_key: str, api_secret: str, environment: str`) — nunca leen `ctx`/`settings`. Ningún campo `AMADEUS_*`/`AFTERSHIP_*` existe en `edecan_api.config.Settings`/`.env.example` (confirmado por grep exhaustivo Y por `Settings.model_fields` en tiempo de ejecución) — no hay NINGÚN valor de plataforma al que algo pudiera caer, ni por accidente. |

**Verificación adicional — guardrail de dinero** (relevante para BARRIDO A
porque una fuga de *capacidad* de booking sería tan grave como una fuga de
credencial): se enumeraron los métodos públicos de `AmadeusClient` y
`AfterShipClient` — `{"buscar_vuelos", "buscar_hoteles", "estado_vuelo",
"aclose"}` y `{"listar_couriers", "detectar_courier", "rastrear", "aclose"}`
respectivamente. **Ninguno de los dos clientes tiene, ni ha tenido nunca, un
método de booking/pago/escritura** — no es solo una promesa de docstring, es
estructuralmente imposible con el código actual (`docs/viajes.md`,
"Guardrail de dinero").

**Tests nuevos**: `packages/travel/tests/test_travel_byo.py` (15 tests,
archivo nuevo, mismo patrón que `packages/voice/tests/test_voice_byo.py`):
firma de constructores (sin `settings`), request real capturada con `respx`
con un centinela `AMADEUS_API_KEY`/`AFTERSHIP_API_KEY` de "plataforma" en el
entorno (nunca aparece en la request real), dos tenants seguidos sin mezclar
credenciales, `ctx.settings` "veneno" en `providers.py` (camino profundo,
bundle real), y los 2 tests de guardrail de dinero (enumeración de métodos).
El lado `apps/api` de esta misma garantía (`test_settings_no_declara_ningun_
campo_amadeus_ni_aftership`) vive en `apps/api/tests/test_v7_sweep_viajes.py`
— `packages/travel` no declara `edecan-api` como dependencia
(`ARCHITECTURE.md` §10.1, "los tests no importan paquetes hermanos").

---

## BARRIDO B — Plan-flag (`tools.travel`)

| superficie | veredicto | evidencia |
|---|---|---|
| Las 8 rutas de `viajes.router` | **LIMPIO** | Las 8 (`PUT`/`DELETE` credentials ×2, `GET status`, `GET buscar/vuelos`, `GET buscar/hoteles`, `GET rastreo/{numero}`) dependen de `_require_tools_travel` — verificado estructuralmente vía `route.dependant.dependencies` (no una lista de paths a mano: una ruta nueva sin el gate se detectaría sola). |
| Las 5 tools de `edecan_travel.tools` | **LIMPIO** | Las 5 (`buscar_vuelos`, `buscar_hoteles`, `estado_vuelo`, `rastrear_paquete`, `preparar_reserva`) tienen `requires_flags == frozenset({"tools.travel"})` — ya cubierto exhaustivamente por `packages/travel/tests/test_catalogo.py` (preexistente), re-confirmado por este WP. |
| Multiplexado de acciones (patrón `_bloqueo_por_plan` de `computadora.py`) | **N/A — no aplica** | Ninguna de las 5 tools despacha, vía un parámetro tipo `accion`/`action`, a sub-capacidades con flags MÁS finos que el `requires_flags` de la clase (el patrón que sí hizo falta en `usar_computadora`, `HOTFIXES_PENDIENTES.md`). Es 1 tool = 1 acción × 5, todas bajo el mismo flag — verificado inspeccionando `input_schema["properties"]` de las 5 (ninguna tiene `accion`/`action`). |
| `PLANES["*"].flags["tools.travel"]` | **LIMPIO, y un hallazgo de documentación menor corregido** | Verificado contra el `PLANES` REAL (no un mock): `free_selfhost=True`, `hosted_basic=False`, `hosted_pro=True`, `hosted_business=True` — coincide EXACTO con la matriz pinned en `ARCHITECTURE.md` §14.c. **Hallazgo menor**: el docstring de `apps/api/tests/test_viajes_router.py` afirmaba "HOY ningún plan real tiene la clave `tools.travel`" — eso era cierto cuando WP-V5-09 escribió el archivo, pero dejó de serlo en algún punto posterior (el linchpin de v5, WP-V5-01, sí terminó pinneando el flag) sin que nadie actualizara el comentario. No es un bug de comportamiento (el fixture `_con_flag_travel` que ese docstring justifica sigue siendo inofensivo — forzar `True` sobre un valor que ya es `True` es un no-op), pero SÍ es información falsa para cualquiera que lea ese archivo después. Corregido el docstring; el fixture se conservó tal cual (red de seguridad legítima). |

**Nota sobre el mecanismo genérico de `dangerous=True`**: `preparar_reserva`
(la única tool `dangerous=True` de este paquete) pasa por el mismo gate
genérico que ya corrigió el hallazgo CRITICAL "`POST /v1/conversations/{id}/
confirm` ejecutaba una tool `dangerous` sin revisar su flag de plan"
(`HOTFIXES_PENDIENTES.md`, `_tool_requires_flags_satisfechos` en
`conversations.py`) — ese archivo está fuera de las rutas que este WP puede
tocar, y el fix ya es genérico (no específico por nombre de tool), así que no
hace falta ningún tratamiento especial para `preparar_reserva` ahí.

**Tests nuevos**: `apps/api/tests/test_v7_sweep_viajes.py` —
`test_todas_las_rutas_de_viajes_exigen_require_tools_travel`,
`test_planes_matriz_tools_travel_coincide_con_architecture_14c`,
`test_ninguna_tool_de_travel_multiplexa_acciones_con_permisos_distintos`.

---

## BARRIDO C — Evidencia legal/cumplimiento vs. rollback de sesión

`docs/cumplimiento/barrido-evidencia-v6.md` (WP-V6-03) ya había verificado
"Seguro" los 4 sitios de escritura de `viajes.py` (`put_credentials`,
`delete_credentials`, `put_rastreo_credentials`, `delete_rastreo_credentials`)
bajo el criterio "¿el ping de validación corre DESPUÉS de escribir?" (no) y
"¿`add_audit_log` es la última operación?" (sí). Este WP tenía el encargo de
**(1) confirmar eso intacto y (2) barrer los sitios de escritura RESTANTES**
(creación de borradores de reserva, confirmaciones, tracking) que ese barrido
no cubrió porque `packages/travel` no formaba parte de su alcance.

### (1) Las 4 credenciales — re-verificado intacto, ahora con aserciones AST

La verificación de 2026-07-09 (v6) fue lectura humana. Este WP la repite con
una aserción **estructural** (`ast`, no solo lectura) — el mismo tipo de
chequeo que, aplicado a tiempo, habría detectado directamente el bug real de
`_apply_optout` (`HOTFIXES_PENDIENTES.md`): ahí la escritura de evidencia NO
era la última sentencia de la función, y un barrido que solo miró "¿algo toca
la sesión DESPUÉS de que la función retorna?" (tratándola como caja negra) se
lo perdió por completo.

| handler | ping antes de persistir | `add_audit_log` es la última sentencia |
|---|---|---|
| `put_credentials` | ✅ (`_ping_amadeus` en línea menor que `vault.put`/`repo.add_audit_log`) | ✅ |
| `delete_credentials` | N/A (no valida nada, solo borra) | ✅ (con `return` idempotente ANTES si no hay cuenta) |
| `put_rastreo_credentials` | ✅ (`_ping_aftership` antes) | ✅ |
| `delete_rastreo_credentials` | N/A | ✅ (mismo `return` idempotente) |

### (2) Sitios de escritura restantes

| sitio | efecto externo irreversible previo | veredicto |
|---|---|---|
| `preparar_reserva`/`_crear_reserva_draft` (`packages/travel/edecan_travel/tools.py`) — creación de borradores de reserva | Ninguno (nunca llama a Amadeus, ver BARRIDO A) | **Seguro.** Único `INSERT` de todo el paquete. Una `Tool` nunca es dueña de `ctx.session` — la sesión vive y comitea a nivel de la REQUEST/turno completo. La garantía real la dan `edecan_core.agent.Agent._run_turn` y `edecan_api.routers.conversations._stream_approved_confirmation` (fuera de las rutas de este WP), que envuelven CADA `tool.run(ctx, args)` en su propio `try/except Exception` sin dejarlo escapar hacia el límite de la transacción HTTP — el MISMO mecanismo que ya protege a `LanzarCampanaTool` (`docs/cumplimiento/barrido-evidencia-v6.md`). Verificado con dos regresiones: una `FakeSession` sin `.commit()` (fallaría con `AttributeError` si el código lo llamara) y una variante con `.commit()` que revienta si se invoca — el camino feliz de `preparar_reserva` nunca lo toca en ninguno de los dos casos. |
| **Confirmaciones** | — | **No existe ningún endpoint de confirmación propio en `viajes.py`** (a diferencia de `ads.py::confirmar_borrador`) — verificado estructuralmente (ningún path contiene `confirm`/`reserva`/`orders`/`book`). Un borrador `orders(kind='purchase')` solo puede avanzar de estado vía el router genérico `commerce.py::confirm_order`, que YA tiene su propio `commit()` explícito antes del 501 de `kind` no manejado (WP-V6-03, ver "Verificación de los 4 fixes previos" en `barrido-evidencia-v6.md`) — fuera de las rutas de este WP, re-confirmado intacto por lectura (no se tocó). |
| `GET /v1/viajes/rastreo/{numero}` (tracking) | — | **Seguro — no hay nada que proteger.** Confirmado que el cuerpo de `rastreo()` no contiene `add_audit_log`/`INSERT`/`UPDATE` en absoluto: es un proxy de solo lectura de punta a punta hacia `provider.rastrear(...)`, sin ninguna escritura a la base de datos. |

**Tests nuevos**: `apps/api/tests/test_v7_sweep_viajes.py` —
`test_put_credentials_ping_amadeus_corre_antes_de_persistir`,
`test_put_rastreo_credentials_ping_aftership_corre_antes_de_persistir`,
`test_credenciales_add_audit_log_es_siempre_la_ultima_sentencia_de_la_funcion`,
`test_delete_credentials_idempotente_no_llega_a_escribir_nada_si_no_hay_cuenta`,
`test_rastreo_no_escribe_ninguna_evidencia`,
`test_viajes_router_no_tiene_ningun_endpoint_de_confirmacion_propio`.
`packages/travel/tests/test_tools.py` —
`test_preparar_reserva_nunca_comitea_su_propia_sesion`,
`test_preparar_reserva_con_session_que_revienta_si_comitea_sigue_ok`,
`test_crear_reserva_draft_row_none_lanza_runtimeerror_tras_el_insert_ya_ejecutado`
(este último ancla, además, que el caso defensivo "Postgres no devuelve la
fila del `RETURNING id`" sigue lanzando `RuntimeError` DESPUÉS de que el
`execute()`/`flush()` del INSERT ya corrieron — el `raise` nunca decide si
hubo o no escritura).

---

## BARRIDO D — Esquema real de `orders`

`_crear_reserva_draft` (`packages/travel/edecan_travel/tools.py`) hace
`INSERT INTO orders (tenant_id, user_id, kind, status, descripcion, monto,
moneda, meta) VALUES (...)`. El encargo explícito era compararlo columna por
columna contra el modelo `Order` real (`packages/db/edecan_db/models.py`) y
la migración que lo creó — nunca contra un docstring/README propio del
paquete (el bug real de referencia: `reuniones.py`/`process_meeting.py` en
v6, donde el `FakeSession` de los tests mockeaba filas con el MISMO esquema
equivocado que el código, invisible a la suite).

### Verificación estática (contra el modelo SQLAlchemy real)

`edecan_db.models.Order.__table__.columns.keys()` →
`{id, tenant_id, user_id, kind, status, descripcion, monto, moneda, simbolo,
lado, cantidad, meta, confirmed_at, executed_at, created_at, updated_at}`.

| columna del INSERT | existe en `Order` real | nota |
|---|---|---|
| `tenant_id` | ✅ | |
| `user_id` | ✅ | |
| `kind` | ✅ | literal `'purchase'` — dentro del `CHECK` real (`kind IN ('payment', 'purchase', 'trade')`) |
| `status` | ✅ | literal `'draft'` — dentro del `CHECK` real (`status IN ('draft', 'confirmed', 'executed_paper', 'cancelled', 'expired')`) |
| `descripcion` | ✅ | `NOT NULL`, siempre provista |
| `monto` | ✅ | nullable, siempre provista por `preparar_reserva` (`monto > 0` validado antes) |
| `moneda` | ✅ | `NOT NULL server_default='USD'`, siempre provista |
| `meta` | ✅ | `NOT NULL server_default='{}'::jsonb`, siempre provista (`CAST(:meta AS jsonb)`) |
| *(omitidas a propósito)* `simbolo`, `lado`, `cantidad` | ✅ (nullable) | Correcto omitirlas — `Order.simbolo`/`lado`/`cantidad` son `nullable=True` sin default forzoso, quedan `NULL`. |
| *(omitidas a propósito)* `confirmed_at`, `executed_at` | ✅ (nullable) | Igual — quedan `NULL` hasta que `commerce.py::confirm_order` las toque. |
| *(no declaradas)* `id`, `created_at`, `updated_at` | ✅ (con `server_default`) | Postgres las genera solo (`gen_random_uuid()`/`now()`). |

**Casts verificados** (patrón de bug HOTFIXES puntos 3-5, `:nombre::cast` sin
espacio rompe el regex de bind-params de SQLAlchemy): `:tenant_id ::uuid` y
`:user_id ::uuid` — **con el espacio requerido**. `meta` usa `CAST(:meta AS
jsonb)` (sintaxis de función, no el operador `::`) — inmune a ese patrón por
construcción.

### Verificación empírica (Postgres real, no solo estática)

Se levantó un Postgres desechable (`docker run --name edecan-v7-viajes-pg
-e POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan -e POSTGRES_DB=edecan
-p 55472:5432 pgvector/pgvector:pg16`), se corrió `alembic upgrade head`
(las 8 migraciones reales, `0001_initial` → `0008_v6_expansion`) y se
ejecutó, con un script standalone (creado, ejecutado y borrado del
scratchpad — no se dejó en el repo), el SQL **copiado literal** de
`_crear_reserva_draft` bajo `SET LOCAL ROLE app_user` (el mismo rol con Row-
Level Security que usa producción, no una conexión de owner que bypassa
RLS) tras crear un `tenant`/`user` reales para satisfacer el FK. Resultado:

```
INSERT OK, order_id=b23851f9-043d-4b89-b741-a2682c3840e4
RELEASE/ASSERTS OK — columnas y valores coinciden con el modelo Order real.
```

La fila se releyó con una conexión de owner y se comparó campo a campo
(`kind='purchase'`, `status='draft'`, `simbolo`/`lado`/`cantidad`/
`confirmed_at`/`executed_at` en `NULL`, `meta` con el JSON exacto) — sin
discrepancias. Contenedor borrado inmediatamente después (`docker rm -f
edecan-v7-viajes-pg`), confirmado con `docker ps -a` que no quedó nada
huérfano (solo un contenedor AJENO de otro work package concurrente,
`edecan-v7-resid-pg`, no tocado).

**Tests nuevos**: `apps/api/tests/test_v7_sweep_viajes.py` —
`test_crear_reserva_draft_insert_columnas_existen_en_el_modelo_order_real`
(extrae las columnas del `INSERT` por `inspect.getsource` + las compara
contra `Order.__table__.columns` real, y confirma que toda columna NOT NULL
sin default que el INSERT omite sería un bug — no es el caso hoy) y
`test_crear_reserva_draft_usa_valores_permitidos_por_los_check_constraints_
reales` (extrae los valores permitidos de los `CheckConstraint` reales de la
tabla vía introspección SQLAlchemy, no un literal hardcodeado a mano). No se
dejó un test `@pytest.mark.integration` nuevo para la parte empírica: vive
en `apps/api/tests` (que sí depende de `edecan-db`), y la combinación
estática (columnas + CHECK constraints reales) ya queda de guardia en CI sin
necesitar Postgres — el rol de la verificación empírica de este WP era
confirmar que ese análisis estático no se le escapaba nada, no dejar
infraestructura nueva corriendo en cada `make test`.

---

## Archivos tocados por este WP

| archivo | tipo de cambio |
|---|---|
| `packages/travel/tests/test_travel_byo.py` | **Nuevo** (15 tests) — BARRIDO A |
| `packages/travel/tests/test_tools.py` | Extendido (+3 tests, 23→26) — BARRIDO C |
| `apps/api/tests/test_v7_sweep_viajes.py` | **Nuevo** (12 tests) — BARRIDO A/B/C/D consolidado, lado `apps/api` |
| `apps/api/tests/test_viajes_router.py` | Corrección de docstring únicamente (matriz de `PLANES` desactualizada) — cero tests nuevos/eliminados, sigue en 36 |
| `docs/viajes.md` | Sección nueva "Auditoría v7 (WP-V7-02)" al final |
| `docs/cumplimiento/barrido-v7-viajes.md` | **Nuevo** (este documento) |

**Ningún archivo de producción se modificó** — `apps/api/edecan_api/routers/
viajes.py`, `packages/travel/edecan_travel/{amadeus,tracking,providers,
tools}.py` quedan bit-a-bit idénticos a como llegaron a este WP. Los 4
barridos concluyeron "ya correcto" en los 5 archivos de producción; lo que
faltaba era la cobertura de regresión dedicada, ahora agregada.

---

## Verificación final

```
uv run --all-packages pytest -q packages/travel apps/api/tests/test_viajes_router.py \
  apps/api/tests/test_v7_sweep_viajes.py -m "not integration"
```
→ **147 passed, 0 failed** (99 de `packages/travel` completo + 36 de
`test_viajes_router.py` + 12 de `test_v7_sweep_viajes.py`).

```
uv run --all-packages pytest -q apps/api/tests -m "not integration"
```
(suite completa de `apps/api`, no solo viajes — control de regresión) →
**1106 passed, 22 deselected (todos `@pytest.mark.integration` gateados por
`DATABASE_URL`), 0 failed**.

```
uv run --all-packages ruff check <8 archivos tocados/creados por este WP>
uv run --all-packages ruff format --check <mismos 4 archivos .py nuevos/editados>
```
→ limpio en ambos.

Contenedor Docker desechable de la verificación empírica (BARRIDO D)
confirmado borrado (`docker ps -a` sin `edecan-v7-viajes-pg`).
