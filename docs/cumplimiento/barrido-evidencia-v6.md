# Barrido de evidencia legal/cumplimiento vs rollback de sesión (v6, WP-V6-03)

Este documento registra el barrido dedicado del **patrón de bug (b) de v5**:
escrituras de evidencia legal/cumplimiento (consentimiento, `audit_log`,
registros de opt-in/opt-out, marcas de intento de envío) que un rollback de
excepción se lleva puestas por no comitear antes de un `raise` alcanzable en
el mismo camino de código. Referencias canónicas del fix correcto (todas
leídas antes de escribir una línea de este WP): `HOTFIXES_PENDIENTES.md`
puntos 8 y 9, la sección "crear_clon_voz", y el código real de
`apps/api/edecan_api/routers/remote.py::get_frame`,
`commerce.py::confirm_order`, `ads.py::confirmar_borrador`,
`voz_avanzada.py::crear_clon_voz`.

**Regla de oro** (verificada empíricamente en este repo, ver los docstrings
de los 4 archivos de arriba): el commit debe ser la ÚLTIMA operación de
sesión de esa rama — comitear y SEGUIR usando la misma sesión dentro del
`async with session.begin()` revienta con
`InvalidRequestError: Can't operate on closed transaction inside context
manager`.

---

## Hallazgo real #1 (confirmado y corregido): `premium/edecan_premium/campaigns.py::handle`

**Síntoma**: todo el lote de una campaña (hasta `MAX_TARGETS_PER_STEP=10`
`campaign_targets`) corría dentro de UNA sola
`async with deps.session_factory(None) as session:`. Tras enviar SMS/
llamadas REALES vía Twilio (efecto de red irreversible) y marcar
`status='done'` con `_mark_target` (que solo hace `execute()` + `flush()`,
nunca `commit()`), el `enqueue(...)` final de re-encolado (red a SQS/DB,
fuera del `try/except` del loop) podía lanzar por un blip de red — esa
excepción salía del `async with` → ROLLBACK de TODO el bloque → los
`status='done'` de mensajes YA ENVIADOS volvían a `'pending'` → el
dispatcher genérico del worker (`edecan_worker.main._handle_message`)
reintentaba el job completo → **RE-ENVÍO de SMS/llamadas reales a los
mismos destinatarios (duplicados = exposición TCPA directa)**. El mismo
mecanismo aplicaba a un crash de proceso a mitad de lote y a los marks
`'error'`.

**Fix (dos capas), en `premium/edecan_premium/campaigns.py`**:

1. **Durabilidad por target**: `handle` se reestructuró para que la carga
   inicial (campaña + ids de targets pendientes) ocurra en una sesión corta
   de solo lectura, y **cada target se procese en su PROPIA
   sesión/transacción** (`async with deps.session_factory(None) as
   session:` por target): re-chequea consentimiento/cupo/horario dentro de
   ESA sesión, envía (si aplica), marca `done`/`error`, y sale del bloque
   (commit real) — así un envío real nunca queda sin su marca durable antes
   de tocar el siguiente target.
2. **Cliente de Twilio reconstruido por target, no cacheado**:
   `telephony.for_tenant(vault, session, tenant_id)` GUARDA la `session` que
   recibe (la usa después para `_enforce_compliance`/`_audit_and_meter`) —
   cachear el cliente entre targets ataría su auditoría a la sesión del
   PRIMER target, ya cerrada, reventando con `InvalidRequestError` en el
   segundo. Se reconstruye fresco dentro de la sesión de CADA target;
   `client_error` cachea solo el FALLO de resolución (no depende del
   target) para no reintentar `for_tenant` una vez que ya se sabe que el
   tenant no tiene Twilio conectado.
3. **`enqueue` fuera de cualquier sesión abierta**: `remaining` se calcula
   en su propia sesión corta DESPUÉS de que la última sesión por-target ya
   comiteó; `enqueue` corre totalmente fuera de cualquier sesión. Si falla,
   el retry del job es seguro: solo quedan `pending` los targets que de
   verdad nunca se tocaron.

**Tests** (`premium/tests/test_campaigns.py`, reescrito con una fixture
`_FakeDb`/`_FakeSession`/`_FakeSessionFactory` que simula de verdad la
semántica transaccional — commit al salir limpio, rollback y descarte al
propagar una excepción — a diferencia de la `_FakeSession` única y
compartida de la versión anterior, que no podía detectar este bug en
absoluto):

- `test_handle_cada_target_comitea_en_su_propia_sesion` — 4
  sesiones/commits independientes para un lote de 2 targets (carga +
  2 targets + conteo final), nunca 1.
- `test_handle_tercer_target_falla_no_afecta_los_ya_marcados_done` — envía
  2, el 3º falla contra Twilio: los 2 primeros quedan `done` y sobreviven;
  un "retry" del job no genera llamadas duplicadas.
- `test_handle_falla_reencolado_no_deshace_los_ya_enviados` — **regresión
  directa del bug real**: el `enqueue` final lanza `RuntimeError`; los 2
  targets ya enviados quedan `done` (no se revierten); un retry posterior no
  los vuelve a tocar.
- `test_handle_sin_twilio_conectado_marca_error_todo_el_lote_sin_reintentar`
  — verifica la optimización de `client_error` (una sola resolución fallida
  de `for_tenant`, no una por target).
- Los 8 tests preexistentes (camino feliz SMS/voz, sin consentimiento, cupo
  agotado, fuera de horario, fallo de Twilio, campaña/targets ausentes)
  migrados a la nueva fixture, mismo comportamiento observable.

Además, `premium/tests/test_v6_evidencia.py` (archivo nuevo, dedicado) repite
en forma condensada y autocontenida los DOS escenarios que pedía
explícitamente el enunciado del paquete de trabajo ("envía 2, el 3º lanza" /
"el enqueue final lanza"), más un test de extremo a extremo que encadena
`compliance.grant_consent` (evidencia real de consentimiento) con
`campaigns.handle` (su consumidor real) para probar la cadena completa.

**Verificado**: `uv run --all-packages pytest -q premium/tests/test_campaigns.py premium/tests/test_v6_evidencia.py`
→ 16 passed. Suite completa de `premium/` → 106 passed (ver sección de
verificación final).

---

## Resto del barrido — `premium/`

| archivo | veredicto | evidencia / test |
|---|---|---|
| `compliance.py` (`grant_consent`/`register_optout`) | **Sin bug en `compliance.py` en sí — pero el veredicto original de "ningún hueco en `twilio_router.py`" era INCORRECTO, ver corrección 2026-07-09 abajo** | Ninguna de las dos comitea su propia sesión (por diseño: no abrieron esa sesión, no les corresponde decidir cuándo cierra su transacción). Único invocador real de `grant_consent` fuera de `edecan_premium`: `apps/api/edecan_api/routers/consents.py::create_consent` — el único `raise` posible (`ValueError` → 400) ocurre ANTES de cualquier escritura (`grant_consent` valida `kind`/`source` antes del primer `INSERT`); si no lanza, el handler retorna limpio y el commit implícito de `get_tenant_session` persiste todo. Único invocador real de `register_optout`: `premium/edecan_premium/twilio_router.py::_apply_optout` (`/gather` y `/sms`, WP-V6-12, solo VERIFICADO, no tocado) — ambas rutas abren una sesión DEDICADA y CORTA (`async with request.app.state.get_session(tenant_id) as session:`) solo para el opt-out, sin volver a tocarla después. ~~**No se encontró ningún hueco en `twilio_router.py`**~~ **Corrección (2026-07-09):** esa conclusión trató `_apply_optout()` como caja negra atómica y solo verificó que nada toca la sesión DESPUÉS de que retorna — nunca miró DENTRO de la función, donde `register_optout` corría PRIMERO y un `UPDATE campaign_targets` sin relación con la evidencia corría DESPUÉS, en la misma sesión, sin `commit()` entre medio. Un fallo de ese UPDATE (deadlock/lock contention con `campaigns.handle`, que escribe la misma tabla) sí perdía la evidencia recién escrita en el rollback automático — bug real, corregido reordenando (evidencia al final). Ver `HOTFIXES_PENDIENTES.md`, entrada "tercera recurrencia del patrón de rollback (puntos 8/9) — `_apply_optout`", para el detalle completo, la verificación y los tests de regresión. Tests nuevos: `test_grant_consent_nunca_comitea_su_propia_sesion`/`test_register_optout_nunca_comitea_su_propia_sesion` en `premium/tests/test_compliance.py`. |
| `telephony.py` (`_audit_and_meter`, registro de uso/minutos) | **Sin bug — orden correcto, y la durabilidad depende del caller (ya arreglado)** | La auditoría (`audit_log` + `usage_events` vía `limits.register_usage`) se escribe DESPUÉS de que Twilio ya respondió con éxito (nunca se reclama evidencia de un envío que no ocurrió) y `TwilioTenantClient` nunca comitea su propia sesión (recibida del llamador). La durabilidad real depende de ESE llamador: `campaigns.handle` (arreglado arriba, sesión por target) y `edecan_premium.tools` (ver abajo, seguro por la arquitectura del agente). Tests nuevos en `premium/tests/test_telephony.py`: `test_start_call_registra_auditoria_despues_de_la_llamada_real`/`test_send_sms_registra_auditoria_despues_de_la_llamada_real` (orden verificado con un log compartido entre el transporte HTTP falso y la sesión falsa) + `test_start_call_nunca_comitea_su_propia_sesion`. |
| `tools.py` (`LanzarCampanaTool`/`LlamarContactoTool`/`EnviarSmsTool`) | **Sin bug — el patrón "escribe evidencia y luego raise" SÍ existe en `LanzarCampanaTool.run()`, pero está protegido por una capa distinta que ya lo hace seguro** | `LanzarCampanaTool.run()` inserta `campaigns`/`campaign_targets` y LUEGO puede lanzar si `enqueue()` falla — pero una `Tool` nunca es dueña de `ctx.session` (esa sesión vive y comitea a nivel de la REQUEST completa del turno de chat, compartida con más tool calls y la persistencia de mensajes) así que una `Tool` NUNCA debe comitear por su cuenta. La garantía real vive un nivel más arriba: `edecan_core.agent.Agent._run_turn` (líneas ~193-202) y `edecan_api.routers.conversations._stream_approved_confirmation` (líneas ~541-547, la que de hecho ejecuta `lanzar_campana` por ser `dangerous=True`) envuelven CADA `tool.run(ctx, args)` en su propio `try/except Exception` que convierte la excepción en un `ToolResult` de error SIN dejarla escapar hacia el límite de la transacción HTTP — así que el commit implícito de fin de request persiste los INSERTs igual, sin importar si `enqueue()` falló. `LlamarContactoTool`/`EnviarSmsTool` ni siquiera llegan a este caso: atrapan TODAS las excepciones esperables de `telephony`/`compliance` internamente. Tests nuevos en `premium/tests/test_tools.py` (archivo nuevo): 12 tests, incluido `test_lanzar_campana_enqueue_falla_propaga_pero_ya_escribio_la_evidencia` (documenta el comportamiento real: la Tool deja propagar, nunca comitea, y la evidencia sobrevive de todos modos por la capa de arriba). |

## Resto del barrido — `apps/api/edecan_api/routers/`

Los 12 sitios de escritura de evidencia (`repo.add_audit_log`/`INSERT INTO
audit_log` directo) de los 11 archivos pinned se trazaron uno por uno,
línea por línea (no solo grep), buscando cualquier `raise` alcanzable
DESPUÉS de la escritura en el mismo camino de código (incluye `Depends`
posteriores, validaciones, y pings de red tipo `_ping_*` que lanzan
`HTTPException`).

| archivo | sitios de escritura | veredicto |
|---|---|---|
| `auth.py` | `register` (1) | **Seguro.** El único `add_audit_log` es la penúltima operación; después solo hay `create_token_pair` (JWT, cómputo puro en memoria, no red/DB) y `return`. |
| `billing.py` | `_handle_checkout_completed`/`_handle_subscription_updated` (2, invocados desde `stripe_webhook`) | **Seguro.** Toda la validación de firma/payload ocurre ANTES de estas funciones; `add_audit_log` es la última operación de cada una; `stripe_webhook` solo hace `return {"status": "ok"}` después. |
| `connectors.py` | `callback`, `connect_twilio`, `connect_whatsapp`, `connect_bot_token` (4) | **Seguro.** Los 4 pings/verificaciones de red (`exchange_code`, `_verify_twilio_phone_ownership`, `_verify_whatsapp_phone_ownership`) ocurren ANTES de persistir; `add_audit_log` es la última operación de cada handler. `callback` además usa un patrón notable: `async with get_session(tenant_id) as session:` DEDICADO solo para el connect (no la sesión de request vía `Depends`), ya aislado correctamente. |
| `credentials.py` | `put_llm_credentials`, `delete_llm_credentials`, `put_voice_stt_credentials`, `put_voice_tts_credentials`, `delete_voice_credentials`, `put_images_credentials`, `delete_images_credentials`, `put_search_credentials`, `delete_search_credentials` (8) | **Seguro — responde explícitamente la pregunta del enunciado ("¿hace ping DESPUÉS de escribir?"): NO, en los 8 sitios el ping (`_ping_anthropic`/`_ping_openai_compat`/`_ping_ollama`/`_ping_cli_binary`/`_ping_deepgram`/`_ping_elevenlabs`/`_ping_brave`/`_ping_tavily`) ocurre siempre ANTES de `vault.put`+`add_audit_log`.** Ya cubierto además por `test_credentials_router.py::test_put_llm_anthropic_key_rechazada_no_guarda_nada`. |
| `consents.py` | `create_consent` (1) | **Seguro** (ya lo señalaba el enunciado: "el raise está en el except del propio insert" — confirmado: `compliance.grant_consent` valida `kind`/`source` y lanza `ValueError` ANTES de cualquier `INSERT`). |
| `erp.py` | `create_producto`, `update_producto`, `create_movimiento` (3, vía el helper local `_audit`) | **Seguro.** `SkuDuplicadoError`/`StockInsuficienteError`/`ValueError` (409/422) se lanzan desde `edecan_business.*` ANTES de que el router llame a `_audit`; después de `_audit`, solo `return`. Ya cubierto por `test_erp_router.py::test_create_producto_sku_duplicado_returns_409`. Test nuevo (ángulo distinto — `commits` explícito) en `apps/api/tests/test_v6_sweep_evidencia.py`. |
| `rrhh.py` | `aprobar_nomina_endpoint`, `cancelar_nomina_endpoint` (2, vía el mismo patrón `_audit`) | **Seguro.** `EstadoNominaError` (409) se lanza ANTES de `_audit`; ninguna de las dos rutas hace una llamada de red (nunca mueven dinero real, ver el guardrail del propio módulo) así que no hay ni siquiera un `_ping` que pudiera fallar después. Ya cubierto por `test_rrhh_router.py::test_aprobar_nomina_doble_aprobacion_returns_409` ("Nunca llegó al UPDATE ni al audit_log"). Test nuevo (`commits` explícito) en `apps/api/tests/test_v6_sweep_evidencia.py`. |
| `viajes.py` | `put_credentials`, `delete_credentials`, `put_rastreo_credentials`, `delete_rastreo_credentials` (4) | **Seguro.** Mismo patrón que `credentials.py`: `_ping_amadeus`/`_ping_aftership` ANTES de persistir; `add_audit_log` es la última operación. |
| `mensajes.py` | `enviar_mensaje` (1) | **Seguro.** El envío real (`_enviar_crudo`, red) ocurre ANTES del `add_audit_log`, con su propio `try/except MessagingClientError` → 400 (nada se audita si el envío falló); después de auditar, solo se construye un `EnviarMensajeOut` a partir de datos ya conocidos y se retorna. |
| `negocios.py` | — (ninguno) | **N/A.** No hay ningún `add_audit_log`/`INSERT INTO audit_log` en este router — toda la lógica de facturas vive en `edecan_business` (fuera de las rutas que este WP puede tocar). Nada que auditar aquí. |
| `perfil.py` | — (ninguno) | **N/A.** `user_profiles` es memoria/personalización del asistente, no evidencia legal/cumplimiento en el sentido del enunciado (consentimiento/audit_log/opt-in-opt-out); tampoco hay ningún `add_audit_log`. `rebuild_perfil` solo hace `enqueue(...)` sin escritura previa que proteger. |

**Test nuevo**: `apps/api/tests/test_v6_sweep_evidencia.py` (4 tests) —
complementa (no repite) la cobertura ya exhaustiva de `test_erp_router.py`/
`test_rrhh_router.py`/`test_credentials_router.py`/`test_connectors.py` con
una verificación DIRECTA, vía el stack HTTP real y una `FakeSession` con
contador de `commits`, de que `erp.py`/`rrhh.py` (los únicos dos routers de
este WP con un helper `_audit()` propio, SQL crudo, calcado de
`commerce.py._audit`) nunca llaman `session.commit()` por su cuenta —
precisamente PORQUE ninguno de los dos tiene un `raise` alcanzable después
de escribir la evidencia, nunca necesitaron el patrón de commit explícito.

### Addendum (2026-07-09): `hooks.py` había quedado fuera de este barrido

La lista de "11 archivos pinned" de arriba (`auth.py`, `billing.py`,
`connectors.py`, `credentials.py`, `consents.py`, `erp.py`, `rrhh.py`,
`viajes.py`, `mensajes.py`, `negocios.py`, `perfil.py`) nunca incluyó
`apps/api/edecan_api/routers/hooks.py` — no fue un "seguro" verificado por
este WP, simplemente no se recorrió. Un hallazgo posterior confirmó que SÍ
tenía el mismo bug (`trigger_hook`: `audit_log` seguido de `enqueue(...)` sin
commit de por medio, sin `try/except`). Corregido y documentado en
`HOTFIXES_PENDIENTES.md`, sección "RESUELTO (2026-07-09): mismo patrón de
rollback (puntos 8/9) en `POST /v1/hooks/{automation_id}`" — no se repite el
detalle acá para no duplicar la fuente de verdad. Con este fix, la cobertura
de "escritura de audit_log seguida de una operación que puede lanzar" en
`apps/api/edecan_api/routers/` queda completa: los 11 archivos de la tabla de
arriba + `remote.py`/`commerce.py`/`ads.py`/`voz_avanzada.py` (los 4 fixes
previos, ver "Verificación de los 4 fixes previos" más abajo) + `hooks.py`
(este addendum).

---

## Resto del barrido — `apps/worker/edecan_worker/handlers/`

Pregunta clave por handler (la del enunciado): **¿hay efecto EXTERNO
irreversible (red) seguido de marca durable que un `raise` posterior
deshaga, causando doble efecto en el retry?**

| archivo | efecto externo irreversible | veredicto |
|---|---|---|
| `ingest_file.py` | Ninguno real (embeddings/visión LLM son cómputo puro recalculable, no un envío a un tercero) | **Seguro.** Si algo falla a mitad de lote (embeddings/LLM), la excepción propaga sin ser tragada (verificado con un test nuevo) y toda la sesión hace rollback — un reintento del job completo recalcula todo desde cero, limpio e idéntico (nada "ya enviado" que pueda duplicarse). |
| `send_reminder.py` | El push nativo (`channel="mobile"`) SÍ es un efecto externo que puede fallar | **Ya seguro por diseño, antes de este WP.** El mensaje + `reminders.status='sent'` se escriben y comitean DENTRO del `async with` (que cierra antes de intentar el push); el push corre DESPUÉS, fuera de cualquier sesión, envuelto en su propio `try/except` que nunca escapa. Exactamente el patrón que este WP persigue en todos lados. Ya cubierto exhaustivamente por `test_send_reminder.py::test_send_reminder_channel_mobile_push_falla_no_revienta_el_job`. |
| `sync_connector.py` | El refresh de un token OAuth es idempotente/seguro de reintentar, no hay duplicación real | **Seguro.** Cada fila se procesa en su propio `try/except` (`_refresh_one`) que nunca escapa hacia `handle()` — el fallo de un token no puede tumbar la sesión compartida por el resto del lote. Ya cubierto por `test_sync_connector.py::test_sync_connector_un_fallo_no_detiene_los_demas`. |
| `generate_content.py` | Ninguno (llamada al LLM, cómputo recalculable; sin productor real en producción hoy) | **Seguro.** Nada se escribe antes de la llamada al LLM; si falla, la excepción propaga sin haber escrito nada que proteger. |
| `memory_consolidate.py` | Ninguno real en las 3 fases (LLM = cómputo recalculable; dedup = SQL puro) | **Seguro en fases 1 y 3** (cada una con su propio `try/except Exception` que nunca propaga). **Hallazgo menor, fuera de alcance, documentado sin corregir**: la fase 2 (dedup, entre 1 y 3) no tiene su propio `try/except` — un fallo de Postgres ahí SÍ propagaría y haría rollback de toda la sesión del job, incluida la extracción de la fase 1. No calza en el patrón de este WP (fase 2 no tiene ningún efecto externo/red) — la única consecuencia es que un reintento del job recalcula la fase 1 desde cero, sin ningún efecto duplicado hacia el exterior ni pérdida de evidencia legal. |

**Test nuevo**: `apps/worker/tests/test_v6_evidencia.py` (3 tests) — llena el
hueco real que faltaba: los tests preexistentes de `generate_content`/
`memory_consolidate` cubren respuestas MALFORMADAS del LLM (JSON inválido,
etc.), pero ninguno ejercitaba una excepción REAL del proveedor (un 500 de
verdad) para confirmar que esa excepción se propaga limpia (`ingest_file`/
`generate_content`, donde SÍ debe propagar) o se atrapa limpia
(`memory_consolidate` fase 1, donde NO debe propagar).

---

## Verificación de los 4 fixes previos (solo asserts, sin ediciones)

Confirmado por lectura + grep que las 4 líneas de commit explícito siguen
intactas, en el mismo lugar y con la misma lógica que documentan sus propios
docstrings:

- `apps/api/edecan_api/routers/remote.py::get_frame`/`send_input` — 2
  `await db_session.commit()` (líneas 690 y 928), cada uno inmediatamente
  antes de su `raise HTTPException(403, ...)` de denegación.
- `apps/api/edecan_api/routers/commerce.py::confirm_order` — 3
  `await session.commit()` (líneas 408, 418, 456): el 501 de `COMMERCE_MODE`
  no soportado, el 400 de `PaperBroker` con holdings insuficientes, y el 501
  de `kind` no manejado.
- `apps/api/edecan_api/routers/ads.py::confirmar_borrador` — 1
  `await session.commit()` (línea 576), antes del 502 cuando Meta rechaza el
  push de la campaña.
- `apps/api/edecan_api/routers/voz_avanzada.py::crear_clon_voz` — 2
  `await session.commit()` (líneas 573 y 599): sin ElevenLabs conectado, y
  cuando ElevenLabs rechaza el clon.

Suites propias re-ejecutadas sin tocar código: `test_remote_router.py` +
`test_commerce_router.py` → 86 passed; `test_ads_router.py` +
`test_voz_avanzada.py` → 62 passed. Ningún archivo de estos 4 aparece en la
lista de archivos escritos por este WP.

---

## Verificación final

```
uv run --all-packages pytest -q premium apps/api/tests apps/worker/tests -m "not integration"
```
→ **1334 passed, 13 deselected (todos `@pytest.mark.integration` gateados
por `DATABASE_URL`), 0 failed.**

`uv run --all-packages ruff check`/`ruff format --check` sobre los 8
archivos Python que este WP escribió/tocó → limpio. `make lint` (repo
completo) → limpio.

`make test` (suite completa del monorepo, `uv run --all-packages pytest` sin
filtro): la colección se interrumpe por un error AJENO a este WP —
`packages/core/tests/test_agent_extra_tools.py` (creado 2026-07-09 ~04:32
por otra sesión/paquete de trabajo concurrente, fuera de las rutas que este
WP puede tocar) hace `from test_agent import (...)` para reusar helpers de su
archivo hermano — un import que solo resuelve cuando pytest corre con
`packages/core` como rootdir (`cd packages/core && pytest tests/
test_agent_extra_tools.py` → 9 passed en aislamiento) y revienta la
COLECCIÓN completa del monorepo cuando se corre junto con el resto de
paquetes. No es un archivo de este WP, no se tocó (mismo criterio de toda la
sesión: no editar código de un workflow concurrente en vuelo) — se dejó
reportado como una tarea de seguimiento separada. Con ese único archivo
excluido (`--ignore=packages/core/tests/test_agent_extra_tools.py`), la
suite COMPLETA del monorepo (`packages`, `apps`, `premium`) da:

**4053 passed, 18 skipped, 0 failed** (159.57s) — cero regresiones
atribuibles a este paquete de trabajo en ningún paquete del repo, incluidos
los 4 fixes previos verificados arriba y los 25 archivos de "producción"
que este barrido concluyó que no necesitaban cambios.
