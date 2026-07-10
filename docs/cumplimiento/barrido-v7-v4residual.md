# Barrido v7 — residual v4/v5: devices/push (APNs/FCM), ERP inventario, ads, mensajes (WP-V7-07)

Este documento registra el barrido dedicado de las piezas v4/v5 que quedaron con
verificaciones **parciales** en olas anteriores: `apps/api/edecan_api/routers/{devices,erp,
ads,mensajes}.py`, `apps/worker/edecan_worker/push.py`, `packages/business/edecan_business/
inventory.py` y `packages/ads/`. Referencias leídas completas antes de escribir una línea:
`DIRECCION_ACTUAL.md`, `HOTFIXES_PENDIENTES.md` completo, `docs/cumplimiento/
barrido-evidencia-v6.md`.

**Resultado ejecutivo: cero defectos de producción encontrados.** Los seis archivos objeto de
este barrido ya estaban completos, correctos y bien probados (esquema real, bring-your-own,
paridad de flags, y evidencia legal/cumplimiento) — el hueco genuino que sí existía era la
**ausencia total de verificación empírica contra Postgres real** para este set (Barrido D), el
mismo tipo de punto ciego que causó el bug crítico de `apps/api/edecan_api/routers/
reuniones.py` en v6. Ese hueco queda cerrado con `apps/api/tests/test_v7_sweep_v4residual.py`
(13 tests nuevos: 6 empíricos contra Postgres real + 7 estructurales). Ningún archivo de
producción se modificó.

---

## Barrido D — Esquema (empírico, el núcleo de este paquete)

**Metodología**: Postgres desechable (`docker run ... -p 55476:5432 pgvector/pgvector:pg16`,
nombre `edecan-v7-resid-pg`) + `alembic upgrade head` real (aplica las 8 migraciones hasta
`0008_v6_expansion` sin error) + inspección directa del esquema (`\d products`, `\d
stock_moves`, `\d ad_drafts`, `\d devices`, `\d connector_accounts`, `\d audit_log`) comparada
columna por columna contra el SQL crudo de cada router/módulo, más tests de integración nuevos
que llaman DIRECTAMENTE a las funciones reales de los routers (no solo al SQL, el código de
producción completo: validación Pydantic + lógica de negocio + SQL) contra esa base real.
Contenedor bajado al terminar (`docker rm -f edecan-v7-resid-pg`), confirmado con `docker ps
-a` (solo quedan contenedores de otras sesiones concurrentes de este mismo repo,
`edecan-v7-meet-pg`/`edecan-v7-rrhh-pg`, no tocados).

### Resultado: las 4 tablas/columnas verificadas coinciden EXACTAMENTE con el código

| Tabla | Migración | Columnas verificadas contra el código | Veredicto |
|---|---|---|---|
| `products` | `0006_v4_expansion` | `id,tenant_id,user_id,sku,nombre,descripcion,unidad,precio,costo,stock,stock_minimo,activo,created_at,updated_at` — `packages/business/edecan_business/inventory.py` (crear/editar/listar/resumen) y `erp.py` | Coincide 1:1 |
| `stock_moves` | `0006_v4_expansion` | `id,tenant_id,user_id,product_id,delta,motivo,nota,ref,created_at,updated_at` — `inventory.registrar_movimiento` | Coincide 1:1 |
| `ad_drafts` | `0006_v4_expansion` | `id,tenant_id,user_id,provider,nombre,objetivo,presupuesto_diario,moneda,payload,status,external_id,error,confirmed_at,pushed_at,created_at,updated_at` + `CHECK ck_ad_drafts_status` — `ads.py` (`_crear_ad_draft`/`list_borradores`/`confirmar_borrador`/`cancelar_borrador`) y `packages/ads/edecan_ads/tools.py::_crear_ad_draft` | Coincide 1:1 |
| `devices` | `0003_v2_expansion` + `push_token`/`push_platform` de `0007_v5_expansion` | `id,tenant_id,user_id,nombre,plataforma,kind,status,last_seen_at,fingerprint,push_token,push_platform,created_at,updated_at` + `CHECK ck_devices_kind`/`ck_devices_status` — `devices.py` (los 8 endpoints) | Coincide 1:1 |
| `connector_accounts` | `0001_initial` | `id,tenant_id,connector_key,external_account_id,display_name,status,scopes,created_at,updated_at` — usada por `devices.py` (connector_key `"push"`), `ads.py` (`"ads"`), `apps/worker/edecan_worker/push.py::cargar_credenciales_push` | Coincide 1:1 |
| `audit_log` | `0001_initial` | `id,tenant_id,actor_user_id,action,target,meta,created_at,updated_at` — el helper `_audit` local de `erp.py`/`ads.py` (INSERT directo) y `repo.add_audit_log` (`devices.py`/`mensajes.py`) | Coincide 1:1 |

Ningún `UndefinedColumnError` ni ningún desajuste de tipo — a diferencia del hallazgo de v6
(`reuniones.py` contra un esquema "asumido" del docstring de otro paquete en vez de la
migración real), aquí los 6 archivos objeto de este barrido ya codeaban directo contra los
nombres reales de columna.

### Tests nuevos (`apps/api/tests/test_v7_sweep_v4residual.py`, grupo empírico)

Los 6 tests siguientes llaman a las funciones REALES de los routers (`erp.create_producto`,
`devices.create_device`, `ads.confirmar_borrador`, etc. — sin pasar por FastAPI/ASGI, pero es
el mismo código Python de producción) con una `AsyncSession` real:

1. `test_erp_inventory_lifecycle_contra_postgres_real` — ciclo completo de un producto
   (crear con sku duplicado rechazado → 409, listar, editar precio, movimientos de
   entrada/salida, rechazo de stock negativo salvo `motivo='ajuste'`, desactivar, resumen).
2. `test_registrar_movimiento_for_update_evita_perdida_de_actualizaciones_concurrentes` —
   ver "Guardrails" más abajo.
3. `test_devices_lifecycle_contra_postgres_real` — crear dispositivo, heartbeat, fijar/borrar
   `push_token`/`push_platform` (columnas nuevas de `0007`), revocar.
4. `test_devices_push_credentials_roundtrip_contra_postgres_real` — `PUT`/`GET`/`DELETE
   /v1/devices/push/credentials` con un `TokenVault` REAL (`LocalKeyProvider` + AES-256-GCM),
   ejercitando `tenant_keys`/`oauth_tokens`/`connector_accounts` reales — cierra el hueco de
   que la cobertura offline (`test_devices_push.py`) solo usa un `FakeVault` en memoria.
5. `test_ads_borradores_lifecycle_contra_postgres_real` — `ad_drafts` desde un `INSERT`
   calcado de `edecan_ads.tools._crear_ad_draft` hasta `confirmar_borrador` (resuelve a
   `StubAdsProvider`, cero red real, con un doble de vault que revienta si se llega a
   invocar — prueba que el `SELECT` contra `connector_accounts` corta ANTES de tocar el
   vault) y `cancelar_borrador` (409 sobre un borrador ya `pushed`).
6. `test_ad_drafts_check_constraint_status_contra_postgres_real` — defensa en profundidad:
   el `CHECK` de `ad_drafts.status` rechaza a nivel de Postgres cualquier valor fuera del
   vocabulario `draft|confirmed|pushed|error|cancelled`.

---

## Barrido A — Bring-your-own

**`apps/worker/edecan_worker/push.py` (foco principal, v5, sin barrido BYO dedicado previo)**:
releído completo línea por línea. Confirmado: `apps/worker/edecan_worker/config.Settings` NO
declara ningún campo de credencial de push de plataforma (no existe `APNS_*`/`FCM_*`/`PUSH_*`);
`cargar_credenciales_push(session, vault, tenant_id)` ni siquiera acepta un parámetro
`settings` — estructuralmente no puede leer una credencial de plataforma. A diferencia del
hallazgo de Polly en v5 (`docs/cumplimiento/barrido-evidencia-v6.md` cita ese caso: un SDK de
terceros —`aioboto3`— resolviendo credenciales AWS ambiente por dentro, invisible a un grep de
`self._settings`), `push.py` NO usa ningún SDK de terceros para autenticar: tanto APNs como FCM
se hablan con `httpx` puro, construyendo el JWT/bearer a mano con `pyjwt`+`cryptography` a
partir de los campos que trae el `TokenBundle` del tenant (`cred_apns["p8_key"]`,
`service_account["private_key"]`) — no hay ningún punto donde un SDK pudiera "ayudar"
resolviendo una credencial ambiente por su cuenta. Sin brecha.

Tests centinela nuevos (patrón `test_voice_byo.py`, en el grupo estructural de
`test_v7_sweep_v4residual.py`, corren siempre sin necesitar Postgres):
`test_worker_settings_no_declara_ningun_campo_de_credencial_push`,
`test_cargar_credenciales_push_no_acepta_ni_puede_leer_settings`,
`test_enviar_push_a_usuario_nunca_lee_deps_settings_para_credenciales` (inspecciona el código
fuente real de `enviar_push_a_usuario`, no solo la firma, para cazar un futuro
`cargar_credenciales_push(..., settings=deps.settings)` que no rompería la firma actual).

**Ads y mensajería** (ya pasaron el barrido BYO v5 según el encargo): re-verificado que
`packages/ads/tests/test_byo_ads.py` y `packages/messaging/tests/test_byo_mensajeria.py` siguen
verdes, sin releer todo el código de esos paquetes —
`uv run --all-packages pytest -q packages/ads/tests/ packages/messaging/tests/` → **todos
pasan** (ver sección de verificación final para el conteo exacto).

---

## Barrido B — Paridad de flags de plan (router ↔ tool)

| Router / endpoint | Flag exigido | Tool(s) de agente | Flag exigido por la tool | Paridad |
|---|---|---|---|---|
| `erp.py` (`_require_erp_inventory`) | `FLAG_ERP_INVENTORY` (`"erp.inventory"`) | `GestionarInventarioTool`, `EstadoInventarioTool` (`edecan_business.tools`) | `FLAG_ERP_INVENTORY` | ✔ idéntico |
| `ads.py` (`_require_tools_ads`) | `FLAG_TOOLS_ADS` (`"tools.ads"`) | `AdsResumenTool`, `AdsPrepararCampanaTool` (`edecan_ads.tools`) | `"tools.ads"` (string local, `edecan_ads` no depende de `edecan_schemas`) | ✔ idéntico |
| `mensajes.py` (`_require_messaging`) | `FLAG_CONNECTORS_MESSAGING` (`"connectors.messaging"`) | `EnviarMensajeTool`, `LeerMensajesTool` (`edecan_messaging.tools`) | `FLAG_CONNECTORS_MESSAGING` | ✔ idéntico |
| `devices.py` (rutas de push: `POST/DELETE .../push-token`, `PUT/GET/DELETE .../push/credentials`) | `FLAG_NOTIFICATIONS_PUSH` (`"notifications.push"`) | — (sin tool de agente; solo HTTP, dispositivos los registra la app nativa) | — | N/A, sin tool equivalente |
| `devices.py` (CRUD base: `GET/POST ""`, `heartbeat`, `revoke`) | ninguno (`companion`, `True` en los 4 planes) | — | — | Correcto por diseño, `ARCHITECTURE.md` §13.f |

**Nota sobre el patrón "multiplexado" de `computadora.py`**: el encargo pedía verificar que
ninguna tool/endpoint de este set multiplexe acciones con permisos DISTINTOS (el patrón que
causó el bug de `_bloqueo_por_plan` en `usar_computadora`). Verificado que no aplica aquí:
`GestionarInventarioTool` tiene 4 acciones (`crear_producto`/`editar_producto`/
`registrar_movimiento`/`desactivar_producto`) pero las 4 exigen el MISMO flag único
(`requires_flags` de la clase, sin gate interno por acción) — no hay ninguna acción "más
sensible" que otra dentro de esa tool que necesite un flag más fino, a diferencia de
`usar_computadora` (que mezclaba acciones de IDE/control remoto/base bajo un solo flag
`companion`). `AdsPrepararCampanaTool`/`AdsResumenTool` y `EnviarMensajeTool`/
`LeerMensajesTool` son tools separadas de una sola acción cada una, mismo razonamiento.

Tests estructurales nuevos (grupo 2 de `test_v7_sweep_v4residual.py`):
`test_paridad_de_flags_erp_router_vs_tools_de_agente`,
`test_paridad_de_flags_ads_router_vs_tools_de_agente` (también ancla
`AdsPrepararCampanaTool.dangerous is True`/`AdsResumenTool.dangerous is False`),
`test_paridad_de_flags_mensajes_router_vs_tools_de_agente`,
`test_devices_push_endpoints_exigen_flag_notifications_push`.

---

## Barrido C — Evidencia legal/cumplimiento (escritura antes de un `raise` alcanzable)

**`devices.py`** (nunca barrido antes por `docs/cumplimiento/barrido-evidencia-v6.md` — la
lista de "11 archivos pinned" de ese documento nunca lo incluyó): recorridos los 8 sitios de
escritura uno por uno.

| Endpoint | Escritura(s) | ¿Hay un `raise` alcanzable DESPUÉS de una escritura que sí ocurrió? |
|---|---|---|
| `create_device` | `INSERT`/`UPDATE ... RETURNING *` (rama idempotente) | No — nada lanza después del `INSERT`/`UPDATE`, solo `return`. |
| `heartbeat` | `UPDATE ... RETURNING id` | El único `raise` (404) ocurre cuando el `UPDATE` afectó CERO filas — no hay nada escrito que perder. |
| `revoke_device` | `UPDATE status='revoked'` + `repo.add_audit_log` | El 404 ocurre ANTES del audit (cuando el `UPDATE` no matcheó filas); después del audit, solo `return`. |
| `set_push_token` / `delete_push_token` | `UPDATE ... RETURNING id` | Mismo patrón que `heartbeat`: el 404 solo ocurre con cero filas afectadas. |
| `put_push_credentials` | `vault.put` + `repo.add_audit_log` | La validación de FORMA (`_validar_apns`/`_validar_fcm`) corre ANTES de tocar `connector_accounts`/vault; nada lanza después de escribir. |
| `delete_push_credentials` | `repo.delete_connector_account` + `repo.add_audit_log` | Idempotente (early return si no hay nada que borrar); nada lanza después de borrar. |

**Veredicto: `devices.py` es seguro, sin necesidad de ningún fix** — ningún endpoint tiene el
patrón "escribe evidencia real y luego un `raise` alcanzable en el mismo camino" que sí tenían
`remote.py`/`commerce.py`/`ads.py::confirmar_borrador`/`voz_avanzada.py` antes de sus fixes
(`HOTFIXES_PENDIENTES.md` puntos 8/9 y sus recurrencias).

**`ads.py`, sitios de escritura DISTINTOS de `confirmar_borrador`** (que ya tiene el fix,
verificado intacto — ver abajo): `put_credentials` (valida con `_ping_meta` ANTES de
persistir), `delete_credentials` (idempotente), `cancelar_borrador` (chequea
`_ESTADOS_CANCELABLES` ANTES del `UPDATE`; después del audit, solo `return`). Los tres sin
patrón de riesgo — ninguno necesita el mismo tratamiento que `confirmar_borrador`.

**Verificación de que el fix ya existente de `confirmar_borrador` sigue intacto** (solo
lectura, sin tocar el archivo): `apps/api/edecan_api/routers/ads.py:576`,
`await session.commit()` como ÚLTIMA operación de sesión de la rama de error, inmediatamente
antes de `raise HTTPException(502, ...)` — idéntico al patrón documentado en su propio
docstring y en `docs/ads.md` ("Auditabilidad: la confirmación humana nunca se pierde").

**`push.py` — el envío real es un efecto externo (patrón `send_reminder`, usado de
referencia)**: verificado que `apps/worker/edecan_worker/handlers/send_reminder.py` sigue el
patrón correcto — el mensaje de chat + `reminders.status='sent'` se escriben y comitean DENTRO
de su propio `async with deps.session_factory(None) as session:` (líneas ~63-110, cierra antes
de intentar el push); `push.enviar_push_a_usuario` se invoca DESPUÉS (línea ~120), fuera de
cualquier sesión abierta, envuelto en su propio `try/except Exception` (línea ~119-133) que
nunca escapa. `push.py` en sí ya es best-effort por diseño (`enviar_push_a_usuario` nunca
lanza — ver su propio docstring), así que no hay ninguna "evidencia" que `push.py` pudiera
perder: la marca durable (`reminders.status='sent'`) ya está comiteada antes de que el push
siquiera se intente. Sin cambios necesarios.

**Mensajes (`mensajes.py`) y ERP (`erp.py`)**: ya auditados como "Seguro" en
`docs/cumplimiento/barrido-evidencia-v6.md` (`enviar_mensaje`: el envío real ocurre ANTES del
`add_audit_log`; `create_producto`/`update_producto`/`create_movimiento`: las excepciones de
negocio —`SkuDuplicadoError`/`StockInsuficienteError`/`ValueError`— se lanzan ANTES de que el
router llame a `_audit`). Re-verificado por lectura completa de los dos archivos en este
barrido, sin cambios: siguen intactos.

---

## Guardrails (verificados intactos + nueva evidencia empírica)

**`inventory.py` conserva el `SELECT ... FOR UPDATE` del fix crítico v4** (`registrar_
movimiento`, línea ~379-382): confirmado por lectura directa. El test unitario preexistente
(`packages/business/tests/test_inventory.py::test_registrar_movimiento_select_inicial_usa_
for_update`) sigue verde y solo verifica el TEXTO del SQL contra un `FakeSession` — este
barrido agrega la primera verificación EMPÍRICA de este repo para este fix concreto:
`test_registrar_movimiento_for_update_evita_perdida_de_actualizaciones_concurrentes` lanza 12
`registrar_movimiento(delta=1, motivo='ajuste')` CONCURRENTES de verdad (12
sesiones/conexiones Postgres distintas vía `asyncio.gather`) sobre el mismo producto y
confirma que el `stock` final es exactamente 12 (ninguna actualización se pierde por una
carrera check-then-act) y que las 12 filas de `stock_moves` existen (ninguna se duplicó
tampoco). Sin el `FOR UPDATE`, este test sería intermitente y a veces fallaría con un stock
menor a 12.

**Ads nunca publica dinero real sin confirmación explícita**: `MetaAdsProvider.
create_campaign_paused` sigue forzando `body["status"] = "PAUSED"` como la ÚLTIMA asignación
del body (línea ~286 de `packages/ads/edecan_ads/providers.py`), después de mezclar cualquier
`payload` del tenant — ningún campo del borrador puede colar `status="ACTIVE"`. El doble gate
(`dangerous=True` en `ads_preparar_campana` + confirmación humana explícita en `POST
/v1/ads/borradores/{id}/confirmar`) sigue intacto. `test_ad_drafts_check_constraint_status_
contra_postgres_real` (nuevo) agrega una tercera capa de defensa verificada empíricamente: el
`CHECK` de Postgres en sí rechaza cualquier `status` fuera del vocabulario conocido, incluso
para un `INSERT` de SQL directo que se saltara toda la lógica de Python.

---

## Deliverables de este paquete de trabajo

- **`apps/api/tests/test_v7_sweep_v4residual.py`** (nuevo, 13 tests: 6 empíricos + 7
  estructurales) — cierra el hueco de verificación empírica contra Postgres real para las 4
  tablas de este residual (`products`/`stock_moves`/`ad_drafts`/`devices`+push).
- **Este documento** (`docs/cumplimiento/barrido-v7-v4residual.md`).
- **Ningún cambio en código de producción**: `devices.py`, `erp.py`, `ads.py`, `mensajes.py`,
  `push.py`, `inventory.py`, `packages/ads/` y los 5 archivos de test preexistentes
  (`test_devices_router.py`, `test_erp_router.py`, `test_ads_router.py`,
  `test_mensajes_router.py`, `test_push.py`) ya estaban completos, correctos y sin el patrón
  de bug que este barrido buscaba — se verificaron intactos por lectura completa + ejecución
  de su suite, sin necesitar ningún fix.
- `docs/ads.md`, `docs/notificaciones-push.md`, `docs/mensajeria.md` releídos completos y
  comparados contra el código real: ya estaban sincronizados (describen la API HTTP real, sin
  afirmar nada de UI web que WP-V7-09 esté cambiando en paralelo) — sin cambios necesarios.

## Verificación

Postgres desechable `edecan-v7-resid-pg` (puerto 55476), `alembic upgrade head` (8
migraciones, sin error) → bajado con `docker rm -f` al terminar, confirmado con `docker ps -a`
(solo quedan contenedores de otras sesiones concurrentes, no tocados).

```
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:55476/edecan \
uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_v4residual.py -m integration
```
→ **6 passed**.

```
uv run --all-packages pytest -q \
  apps/api/tests/test_devices_router.py apps/api/tests/test_devices_push.py \
  apps/api/tests/test_erp_router.py apps/api/tests/test_ads_router.py \
  apps/api/tests/test_mensajes_router.py apps/api/tests/test_v7_sweep_v4residual.py \
  apps/worker/tests/test_push.py packages/business/tests/test_inventory.py \
  packages/ads/tests/ packages/messaging/tests/ -m "not integration"
```
→ **317 passed, 6 deselected** (los 6 deselected son los empíricos de arriba, gateados por
`DATABASE_URL`).

`uv run --all-packages ruff check`/`ruff format --check` sobre los 13 archivos que este
paquete de trabajo puede tocar (6 de producción + 6 de test preexistentes + el test nuevo) →
limpio.

No se tocó `packages/vehicles/`, `apps/api/edecan_api/routers/vehiculos.py`, `conftest.py`/
`api_fakes.py` compartidos, `test_v6_sweep_flags.py`, `HOTFIXES_PENDIENTES.md`/
`DIRECCION_ACTUAL.md`, `apps/web`, `docs/api.md`, ni código de `packages/messaging/` (solo se
corrió su suite de tests, tal como pedía el encargo).
