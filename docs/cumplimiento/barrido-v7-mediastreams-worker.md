# Barrido v7 (fase v7): Media Streams (beta) + 4 handlers de worker restantes

Este documento cubre `edecan_premium.media_streams` (967 líneas,
interrupciones naturales beta de v6) y los 4 handlers de `apps/worker/edecan_worker/handlers/`
que el barrido dedicado de v6 (`docs/cumplimiento/barrido-evidencia-v6.md`)
NO cubrió: `run_automation.py`, `run_mission.py`, `automation_scan.py`,
`send_reminder_scan.py` (ese barrido cubrió `ingest_file.py`/`send_reminder.py`/
`sync_connector.py`/`generate_content.py`/`memory_consolidate.py`).

Referencias canónicas leídas antes de escribir una línea de este WP:
`docs/seguridad-modelo-amenazas.md` puntos 8/9, el fix TCPA de `campaigns.py::handle`
(fase v6), la tercera recurrencia `_apply_optout` en `twilio_router.py`
("REORDENAR en vez de comitear" cuando la sesión sigue usándose después), la
sección "fuga de tareas asyncio entre archivos de test", y
`docs/cumplimiento/barrido-evidencia-v6.md` completo (tabla §handlers y su
formato).

**Regla de oro reafirmada**: el commit debe ser la ÚLTIMA operación de sesión
de esa rama — comitear y SEGUIR usando la misma sesión revienta con
`InvalidRequestError: Can't operate on closed transaction inside context
manager`. Cuando hace falta seguir usando la sesión después de escribir
evidencia, la solución es REORDENAR (evidencia al final), no comitear a
medio camino.

---

## Parte 1 — `edecan_premium.media_streams`

### Barrido A (BYO): ¿con la credencial de QUIÉN corre el STT/TTS de streaming?

**Verificado, seguro.** `resolver_stt_tenant`/`resolver_tts_tenant` leen
EXCLUSIVAMENTE de `connector_accounts`/`TokenVault` del `tenant_id` de la
llamada (`_leer_config_voz_tenant`, connector keys `"voice_stt"`/`"voice_tts"`,
`ARCHITECTURE.md` §12.b) — Deepgram si el tenant conectó esa credencial,
ElevenLabs si conectó la suya CON `voice_id`. Sin credencial propia, caen a un
stub offline determinista (`STUB_TRANSCRIPT_TEXT`/silencio μ-law) — **nunca**
una credencial de plataforma: `grep -n "Settings\|os.environ\|DEEPGRAM_API_KEY\|ELEVENLABS_API_KEY"`
sobre el archivo completo da cero resultados fuera de comentarios/docstrings
explicando por qué. Fail-closed a un stub, no a un fallback silencioso — "¿hay
al menos un campo de credencial que el tenant trae?" responde que SÍ, y sin
eso el módulo se degrada, nunca se autentica como otra identidad. Sin cambios
necesarios.

### Barrido B (FLAG): ¿el beta está apagado por defecto en TODO camino que lo activa?

**Verificado, seguro — y más apagado de lo mínimo exigible.**

- Endpoint WS (`media_stream_endpoint`): `if not getattr(settings_obj,
  "TWILIO_MEDIA_STREAMS_ENABLED", False): await websocket.close(code=
  WS_CLOSE_FEATURE_DISABLED); return` — `False` por defecto, y `False` sin
  excepción si `app.state.settings` ni siquiera existe.
- Único camino que lo activa: `/incoming` (`twilio_router.py`, solo lectura,
  no tocado) — `_media_streams_enabled(request)` lee el MISMO flag con el
  MISMO default. Confirmado por `grep` (`media_streams\|Connect><Stream\|
  mint_media_token\|_connect_stream_twiml`) que `twilio_router.py` es el
  ÚNICO otro archivo del repo que referencia Media Streams — `campaigns.py`
  (llamadas salientes de campaña) usa `TwilioTenantClient.start_call` con
  TwiML `<Say>/<Hangup>` normal, nunca `<Connect><Stream>`.
- Hoy en producción real (`apps/api`/`apps/local`, ninguno asigna
  `app.state.settings`, ver `ARCHITECTURE.md` §15.h) el flag NUNCA se lee
  como `True` — el beta es inalcanzable incluso con la variable de entorno
  puesta, brecha conocida y documentada, dueño un WP de telefonía de
  seguimiento.

**Sobre `FLAG_VOICE_TELEPHONY` (plan-level, `edecan_schemas.plans`)**:
verificado que NO se re-chequea en cada webhook/WS — pero esto es
INTENCIONAL y consistente en TODO `twilio_router.py`, no un hueco de Media
Streams: `_authenticate`/`_resolve_tenant_by_number` (compartido por
`/incoming`, `/gather`, `/sms`, `/status`, y por extensión el handshake WS
que depende de que `/incoming` haya llegado a emitir el `<Connect><Stream>`)
solo verifica que exista una fila `connector_accounts` con Twilio conectado
+ firma válida — el flag se exige UNA vez, al CONECTAR Twilio
(`connectors.py::_require_voice_telephony`) y al otorgar consentimiento
(`consents.py::_require_voice_telephony`), no en cada webhook. Un tenant que
pierde el flag (downgrade de plan) DESPUÉS de haber conectado Twilio sigue
pudiendo recibir llamadas por `/gather` NORMAL, exactamente igual que por
Media Streams — no es una brecha específica de este módulo, vive
enteramente en `_authenticate` (`twilio_router.py`, fuera de mi alcance de
edición). **Hallazgo para informe, no corregido aquí**: si se decide cerrar
esto, el fix pertenece a `twilio_router.py::_authenticate` (re-chequear el
plan del tenant resuelto contra `FLAG_VOICE_TELEPHONY` antes de aceptar
CUALQUIER webhook), no a `media_streams.py`.

### Barrido C (EVIDENCIA): escrituras de sesión y su seguridad ante rollback

**`_aplicar_optout_real` — verificado por lectura, delega correctamente, sin
cambios.** Líneas 921-926 (numeración post-fix, ver más abajo): abre su
PROPIA sesión corta (`async with get_session(tenant_id) as session:`) y
llama a `twilio_router._apply_optout(session, tenant_id, numero)` — el
mismo `_apply_optout` YA arreglado (tercera recurrencia del patrón,
`docs/seguridad-modelo-amenazas.md`): `UPDATE campaign_targets` primero,
`compliance.register_optout` AL FINAL, como última operación de esa sesión.
Sigue intacto (`grep -n "await session.execute" edecan_premium.twilio_router`
confirma el orden sin tocar).

**Resto de escrituras de sesión — barrido exhaustivo (línea por línea, no
solo grep) del archivo COMPLETO antes del fix**: `grep -n "execute(\|INSERT\|
UPDATE\|audit_log\|usage_events\|register_usage\|commit(\|flush("` sobre
`media_streams.py` (antes de este WP) daba **una sola coincidencia**: la
llamada a `session.execute` dentro de `_aplicar_optout_real`, ya cubierta
arriba. No existía NINGÚN `audit_log`/`usage_events` propio del módulo —
"registro/metering de minutos de llamada", "audit de la llamada" y "estados
intermedios del stream" (los tres términos que pedía el enunciado barrer)
estaban, los tres, **completamente ausentes**, no "presentes pero con el bug
de rollback". Por construcción, sin escritura no hay nada que un rollback
pueda perder — pero la ausencia total de auditoría de una función que
sostiene conversaciones completas por streaming (incluida la posibilidad de
un opt-out) es en sí un hallazgo, no solo un "seguro por vacío".

**Metering de minutos de llamada (`usage_events`)** — verificado que la
duración de la llamada telefónica en sí YA se mide sin que este módulo
necesite tocar nada: `twilio_router.py::status` (webhook de estado de
Twilio, independiente de qué TwiML respondió `/incoming`) registra
`voice_seconds` con el `CallDuration` real de la llamada — se dispara igual
haya usado `<Gather>` o `<Connect><Stream>`. Lo que SÍ falta —y sigue
faltando tras este WP— es medir el consumo de Deepgram/ElevenLabs del
propio tenant (llamadas HTTP reales con costo real, aunque con credencial
BYO). **No se agregó**: `usage_events.kind` es un enum PINNED
(`llm_tokens|voice_seconds|storage_bytes|messages`, `ARCHITECTURE.md` §10.3)
sin ningún valor que le calce a este consumo sin inventar uno nuevo —
extenderlo exige tocar ese pin + una migración Alembic nueva, ninguna de las
dos está en las rutas editables de este paquete de trabajo. **Hallazgo
documentado, sin corregir, para un WP de seguimiento con alcance sobre
`ARCHITECTURE.md`/migraciones.**

**Audit de la llamada — FIX aplicado.** Se agregó una fila `audit_log`
(`action="telephony.media_stream.session"`) por conexión WS, escrita en el
`finally` de `media_stream_endpoint`, en su PROPIA sesión corta e
independiente (patrón "sesiones cortas por unidad de trabajo" de
`campaigns.handle`), envuelta en un `try/except Exception` que nunca
propaga y nunca bloquea el resto de la limpieza (`sender_task.cancel()`,
`sesion.cerrar()`, `websocket.close()`). Se coloca PRIMERA dentro del
`finally` (antes de esos tres pasos) a propósito: si alguno de ellos llegara
a lanzar algo no cubierto por su propio `contextlib.suppress`, las líneas
siguientes de ese mismo `finally` no correrían — así la evidencia de que la
llamada ocurrió nunca queda condicionada a que el resto de la limpieza
también salga limpia. Metadata: `call_sid`, `turnos` (conteo de turnos de
agente), `optout` (bool), `duracion_segundos` (wall-clock desde
`websocket.accept()`); `target` = número del llamante si se conoció, si no
el `call_sid`. Justificación de por qué SÍ se agregó esto (a diferencia del
`usage_events` de arriba, que se dejó sin corregir): `audit_log.action` NO
es un enum pinned (a diferencia de `usage_events.kind`), así que es
schema-safe sin ninguna migración; y estaba EXPLÍCITAMENTE nombrado en el
enunciado del paquete ("audit de la llamada") como categoría a barrer, con
una ausencia total (no un bug parcial) como resultado del barrido.

**"Estados intermedios del stream"**: no hay ninguno persistido en DB —
`SesionMediaStream._estado` (ESCUCHANDO/REPRODUCIENDO/CERRADA) vive
enteramente en memoria del proceso, por diseño (no hay tabla para eso en
`ARCHITECTURE.md` §10.3, y no hace falta: la llamada es efímera, no hay
"reanudar una llamada colgada" en telefonía real). Nada que auditar rollback
acá — verificado por lectura completa de la máquina de estados, sin
hallazgos.

### Asyncio (`_run_background`)

**Verificado, ya correcto — no requirió cambios.** `_run_background` (líneas
310-325, sin tocar en este WP) ya normaliza `BaseException` (`SystemExit`/
`KeyboardInterrupt`) a `RuntimeError` ANTES de que `asyncio.Task` lo vea,
mismo patrón y mismo motivo que `apps/local/edecan_local/runtime.py::
_run_background` (`docs/seguridad-modelo-amenazas.md`, "fuga de tareas asyncio entre
archivos de test") — cita expresa en el docstring del propio módulo. Los 3
`asyncio.create_task(...)` del archivo (`_emitir_frames` en
`SesionMediaStream._responder`, y `_bombear_salida` en
`media_stream_endpoint`) pasan por acá. Fallos del stream STT/TTS
(`_deepgram_transcribe`/`_elevenlabs_synthesize`) ya "nunca lanzan" por
diseño propio (caen a transcripción vacía / silencio μ-law, con
`logger.warning`) — no dependen de `_run_background` para eso, son casos
distintos y ambos ya cubiertos. Barge-in (`_interrumpir`)/`cerrar()` cancelan
limpio con `contextlib.suppress(asyncio.CancelledError)`, sin tareas
huérfanas. Confirmado con los 5 tests preexistentes de `_run_background`
(`test_run_background_deja_pasar_cancelled_error_intacto`,
`_deja_pasar_excepcion_normal_intacta`, `_normaliza_systemexit_a_runtime_error`,
`_normaliza_keyboardinterrupt_a_runtime_error`) — sin necesidad de test de
regresión nuevo, el hueco ya no existe.

### Deliverable propuesto para `docs/voz-telefonia.md` (NO editado — responsable de la fase v3)

Texto exacto propuesto para agregar al final de la sección "Interrupciones
naturales (beta)" de `docs/voz-telefonia.md` (después del diagrama de flujo
de eventos, antes de que termine la sección):

> **Auditoría de la sesión (fase v7).** Cada conexión WS deja una fila
> `audit_log` (`action="telephony.media_stream.session"`) al cerrarse, con
> `call_sid`, número de turnos, si terminó en opt-out, y la duración de la
> sesión — en su propia sesión corta e independiente, para que un fallo de
> infraestructura durante la llamada nunca le impida quedar registrada. La
> duración de la llamada telefónica en sí ya la mide `POST
> /v1/voice/twilio/status` (`voice_seconds`, igual que el flujo `<Gather>`
> clásico). **Limitación conocida, sin corregir**: el consumo real de
> Deepgram/ElevenLabs del tenant durante la sesión (llamadas HTTP con costo
> real, aunque con su propia credencial) no se mide en `usage_events` —
> `usage_events.kind` es un enum cerrado (`ARCHITECTURE.md` §10.3) sin
> ningún valor apto para esto sin una migración nueva.

---

## Parte 2 — Los 4 handlers de worker (tabla de barrido, formato
`barrido-evidencia-v6.md` §handlers)

Pregunta clave por handler: **¿hay efecto EXTERNO irreversible seguido de
una marca durable que un `raise` posterior deshaga, causando doble efecto
(o pérdida de evidencia) en el retry del despachador SQS
(`edecan_worker.main._handle_message`, hasta 5 reintentos con backoff) o en
una entrega duplicada (SQS es *at-least-once*)?**

| archivo | efecto externo irreversible | veredicto |
|---|---|---|
| `run_mission.py` | SÍ — cada paso de una misión puede invocar tools reales vía `Agent.run_turn` (SMS, MCP de terceros, etc.) | **Hallazgo real, CORREGIDO.** Ver detalle abajo. |
| `run_automation.py` | SÍ — un turno headless (`Agent.run_turn`, un ciclo ReAct que puede incluir varias tool calls) | **Hallazgo real, CORREGIDO (parcial, con riesgo residual documentado).** Ver detalle abajo. |
| `automation_scan.py` | El `enqueue(...)` en sí (red/DB) | **Seguro — ya estaba bien, verificado + test de regresión nuevo.** Ver detalle abajo. |
| `send_reminder_scan.py` | Ninguno — el handler no escribe NADA, solo lee `list_due_reminders` y encola | **Seguro por construcción — no hay evidencia que proteger.** Ver detalle abajo. |

### `run_mission.py` — hallazgo real, corregido

**Antes de este WP**: `handle()` abría UNA `async with deps.session_factory(None)
as session:` que envolvía TODO — carga, planificación inicial (`orchestrator.plan`
+ `_insert_steps` + `_update_mission(status="running", plan=...)`), Y la
ejecución COMPLETA de `orchestrator.run(mission, run_deps)`, con `save_step`/
`save_mission`/`insert_steps` cerrando sobre esa MISMA sesión, sin comitear
NADA hasta que `handle()` retornaba limpio.

`edecan_agents.orchestrator.Orchestrator.run` está documentado y verificado
como "nunca lanza" (atrapa cualquier excepción por-paso en
`_ejecutar_paso_de_ola`, y cualquier excepción irrecuperable en su propio
`try/except` de nivel superior) — así que el riesgo NO es "un tool falla" ni
"el LLM se equivoca" (ambos ya resueltos dentro del `Orchestrator`, sin tocar
la sesión de `handle()`). El riesgo real es un `BaseException` genuino
escapando de TODO ese árbol (`asyncio.CancelledError` de una cancelación de
tarea real, el proceso del worker matado a mitad de camino por un
redeploy/OOM/host-replacement — escenario que el propio
`edecan_automations.runner.run_automation` docstring ya reconoce
explícitamente como real, ver más abajo) — en ese caso, el rollback de la
ÚNICA sesión se llevaba puesta TODA la misión, incluidos pasos que YA habían
corrido con efectos externos reales y cuyo `agent_steps.status='done'` un
instante antes parecía exitoso. El reintento del despachador (o una entrega
duplicada de SQS) volvía a invocar `handle()` desde cero: sin NINGÚN paso
durable, SIEMPRE tomaba la rama "misión nueva" (replanifica desde cero,
inserta un plan nuevo/distinto encima, sin ningún `UNIQUE(tenant_id,
mission_id, seq)` que lo impida), re-ejecutando pasos con efectos externos
que YA habían ocurrido.

**Fix (dos capas, mismo espíritu "sesiones cortas por unidad de trabajo" que
`campaigns.handle`)**, en `apps/worker/edecan_worker/handlers/run_mission.py`:

1. `_make_save_step`/`_make_save_mission`/`_make_insert_steps` reciben
   `deps.session_factory` (no una `session` ya abierta): cada invocación
   abre su PROPIA sesión corta, comiteada al salir limpio. El checkpoint de
   CADA paso queda durable en el instante en que ocurre, sin depender de que
   el resto de la ejecución también termine limpio. La sesión larga original
   sigue existiendo para `ToolContext.session`/`vault` (lo que las `Tool`s
   usan — sin cambios; su propia durabilidad ya está cubierta por
   `Agent._run_turn`, verificado en `barrido-evidencia-v6.md`).
2. La planificación inicial (o la transición de un paso reanudado) también
   comitea en su PROPIA sesión corta ANTES de invocar `orchestrator.run` —
   así, cuando `save_step` empieza a llamar por sesiones independientes, las
   filas que va a `UPDATE` YA EXISTEN y están comiteadas (si no, el `UPDATE`
   de una sesión que no ve el `INSERT` sin comitear de otra sería un no-op
   silencioso, no un error — se verificó y corrigió este ordenamiento
   exacto durante el desarrollo del fix).
3. **Reanudación implícita**: la rama "misión nueva" ahora primero
   comprueba si YA existen `agent_steps` para esa `mission_id`. Si los hay
   (evidencia de un intento previo que comiteó su plan/progreso antes de
   morir), NO se replanifica ni se re-inserta — se reusa el plan existente;
   `Orchestrator.run` ya sabe distinguir pasos `done`/`skipped`/`error`/
   `cancelled` (completados) de los que siguen `pending` (a ejecutar).

**Riesgo residual admitido honestamente** (documentado en el docstring del
módulo): un paso que quedó en `status='running'` justo en el instante en
que el proceso murió (su tool pudo haber ejecutado un efecto real antes del
crash) se reintenta igual en la reanudación implícita — cerrar ESE hueco
por completo exigiría claves de idempotencia por-tool-call dentro de
`edecan_core.agent.Agent`/`ToolRegistry`, fuera de alcance de este handler.
El fix sí garantiza que ya no se pierden/repiten pasos que alcanzaron a
TERMINAR (`done`/`error`/`skipped`), la inmensa mayoría de la ventana de
riesgo real.

**Tests**: `apps/worker/tests/test_v7_evidencia.py` (nuevo, ver más abajo)
— 2 tests dedicados con un fake TRANSACCIONAL de verdad (`_FakeDb`/
`_TxFakeSession`), el ÚNICO tipo de fake capaz de demostrar esta clase de
bug/fix (el `FakeSession` preexistente de `test_run_mission_handler.py` no
modela rollback en absoluto). Los 16 tests preexistentes de
`test_run_mission_handler.py` se re-ejecutaron SIN TOCAR ni el archivo ni
sus fixtures — pasan sin cambios (la restructuración es transparente para
ese `FakeSession` no-transaccional, ya que siempre devuelve la MISMA
instancia sin importar cuántas veces se llame `session_factory`).

**Sanity-check adversarial**: se revirtió TEMPORALMENTE (durante el
desarrollo, nunca comiteado) `_make_save_step` a un comportamiento
equivalente al bug pre-fix (el write ocurre pero queda dentro de una
sesión que nunca comitea) y se confirmó que
`test_run_mission_paso_ya_terminado_sobrevive_a_un_fallo_posterior_del_turno`
FALLA como se espera — el test de regresión sí detecta la ausencia del fix,
no es un falso-positivo. Restaurado el fix real antes de continuar.

### `run_automation.py` — hallazgo real, corregido (con riesgo residual mayor que run_mission.py)

**Mismo patrón de fondo**: `_create_running_run` (INSERT
`automation_runs.status='running'`) y `_make_save_run` (UPDATE terminal:
`'done'|'error'|'waiting_confirmation'`) compartían la sesión larga de
`handle()`. `edecan_automations.runner.run_automation` está documentado
como "nunca lanza por un fallo DE NEGOCIO" (el LLM se equivocó, una tool
falló — `Agent.run_turn` ya lo atrapa) pero SÍ deja propagar un fallo DE
INFRAESTRUCTURA — y su propio docstring YA anticipaba el escenario real:
*"`save_run`... nunca `'running'`: esa fila ya la crea el llamador ANTES de
invocar `run_automation`, para que un run que se cuelga o que el worker
mata a mitad de camino siga quedando visible como `running` en vez de no
existir"* — una garantía que, con la sesión compartida de antes de este WP,
NO se cumplía de verdad: un fallo de infraestructura A MITAD del turno (tras
que alguna tool ya ejecutó un efecto externo real) se llevaba puesta la fila
`running` entera en el rollback, contradiciendo ese docstring.

**Fix**: `_create_running_run`/`_make_save_run` reciben `deps.session_factory`
(no una `session` ya abierta), cada uno abre su PROPIA sesión corta. La fila
`running` queda durable ANTES de invocar `run_automation_turn`; el UPDATE
terminal también comitea independiente.

**Riesgo residual admitido honestamente (MAYOR que en `run_mission.py`, y
documentado como tal)**: a diferencia de una misión (pasos individualmente
rastreados en `agent_steps`), una automatización es UN turno headless SIN
ningún checkpoint intermedio propio — puede incluir VARIAS tool calls dentro
de un ciclo ReAct sin marca durable entre ellas. Si el turno falla por
infraestructura DESPUÉS de que una tool ya ejecutó un efecto real pero ANTES
de la escritura terminal, el reintento del despachador sigue pudiendo
repetir esa tool call. Este fix garantiza, como mínimo, evidencia forense
durable (`automation_runs` en `'running'`, nunca desaparecida por completo)
en vez de que el intento se pierda del todo — cerrar el hueco de raíz
exigiría claves de idempotencia por-tool-call dentro de `Agent.run_turn`
(paquete `edecan_core`, fuera de alcance de este handler).

**Tests**: 2 tests nuevos en `test_v7_evidencia.py` (fake transaccional):
`test_run_automation_running_marker_sobrevive_a_un_fallo_posterior_del_turno`
(regresión directa) y
`test_run_automation_save_run_terminal_es_independiente_de_la_sesion_del_turno`
(camino feliz, confirma que `save_run` sigue llegando a `automation_runs`/
`automations.last_run_at`). Los 24 tests preexistentes de
`test_automation_handlers.py` (que cubren TAMBIÉN `automation_scan.py` y
`scheduler.py`, ver abajo) se re-ejecutaron sin tocarlos — pasan sin
cambios, mismo motivo que `run_mission.py` arriba.

### `automation_scan.py` — Seguro, ya estaba bien

**Verificado por lectura línea por línea**: `_advance_next_run(session, ...)`
corre DENTRO de su propia `async with deps.session_factory(None) as session:`
por automatización, que CIERRA (comitea) ANTES de que el `enqueue(...)`
del mismo loop corra — textualmente, el `enqueue` está FUERA de cualquier
`async with` abierto. El propio docstring del módulo ya documentaba el
razonamiento correcto: *"Adelanta `next_run_at` ANTES de encolar
`run_automation`: evita doble disparo si este barrido... vuelve a correr
antes de que ese job termine, o si el worker muere justo después de este
punto"*. Si `enqueue()` falla, el peor caso es una automatización que se
"pierde" ese ciclo (su `next_run_at` ya avanzó) — nunca una duplicación,
perfil de riesgo distinto (y más seguro) que el de `campaigns.handle`. **Sin
cambios de código.**

**Test nuevo, regresión directa** (mismo criterio que
`test_handle_falla_reencolado_no_deshace_los_ya_enviados` de
`campaigns.py`, fase v6):
`test_automation_scan_falla_el_enqueue_no_revierte_el_next_run_at_ya_avanzado`
en `test_v7_evidencia.py` — fuerza que `enqueue()` lance y confirma que
`next_run_at` sigue mostrando el valor recién avanzado (sobrevive al fallo
posterior). Cierra el hueco de que la aserción anterior solo vivía en el
docstring/lectura de código, nunca en un test ejecutable.

### `send_reminder_scan.py` — Seguro por construcción

**Verificado por lectura completa (50 líneas)**: el handler NO escribe
NADA — `repo.list_due_reminders(now=now)` es una consulta pura de solo
lectura, dentro de una sesión corta que cierra antes del loop de
`enqueue(...)` (también fuera de cualquier sesión, igual que
`automation_scan.py`). El patrón "evidencia escrita, luego perdida en un
rollback" NO PUEDE ocurrir aquí por construcción: no hay ninguna escritura
que un rollback pudiera perder. (Nota aparte, fuera del patrón que este WP
persigue: existe una ventana teórica de doble-`enqueue` si dos barridos
corren muy cerca entre sí sin que `send_reminder.py` haya marcado el
recordatorio como `'sent'` todavía — pero eso es una carrera de intervalo de
sondeo, no evidencia perdida en rollback, y depende de `SqlRepo`/
`repo.list_due_reminders`, fuera de las rutas editables de este WP; ya
mitigada en la práctica porque `send_reminder.py` está verificado en
`barrido-evidencia-v6.md` como "Ya seguro por diseño" y solo actúa sobre
recordatorios que siguen `'pending'`). **Sin cambios de código, sin test
nuevo** (los 2 tests preexistentes de `test_send_reminder_scan.py` ya cubren
el comportamiento observable; un fake transaccional no añadiría cobertura
real dado que no hay ninguna escritura que proteger).

---

## Archivos escritos por este WP

- `edecan_premium.media_streams` — auditoría de sesión (fix).
- Suite privada de la extensión (`test_media_streams.py`) — 3 tests nuevos + `_FakeSessionWs`
  ganó `.executed`/`session_cls` inyectable.
- `apps/worker/edecan_worker/handlers/run_automation.py` — sesiones cortas
  independientes para `automation_runs` (fix).
- `apps/worker/edecan_worker/handlers/run_mission.py` — sesiones cortas
  independientes para `agent_steps`/`agent_missions` + reanudación implícita
  (fix).
- `apps/worker/tests/test_v7_evidencia.py` — NUEVO, 5 tests (fake
  transaccional `_FakeDb`/`_TxFakeSession`/`_tx_session_factory`, el único
  capaz de demostrar esta clase de bug/fix).
- Este informe.

**NO tocados** (verificados solo por lectura, tal como exigía el enunciado):
`edecan_premium.twilio_router`, `campaigns.py`, `telephony.py`,
`compliance.py`; `apps/worker/edecan_worker/handlers/automation_scan.py`,
`send_reminder_scan.py` (sin cambios de CÓDIGO — sí ganaron un test nuevo el
primero, en `test_v7_evidencia.py`, no en su propio archivo);
`apps/worker/tests/test_automation_handlers.py`,
`test_run_mission_handler.py` (verificados que siguen pasando, sin
modificar); `docs/voz-telefonia.md` (texto propuesto arriba, para
fase v7).

## Verificación

La ejecución combinada de la suite privada de la extensión y `apps/worker/tests`
registró **385 passed, 1 deselected** (el deselected es `@pytest.mark.integration`
gateado por `DATABASE_URL`, ajeno a este WP), **0 failed**.

El lint combinado de `edecan_premium.media_streams`, su regresión privada y los
handlers/tests públicos del worker terminó con **All checks passed!**

```
uv run --all-packages ruff format --check <mismos 5 archivos>
```
→ **5 files already formatted.**

Confirmado por `ls -la`/timestamps que `twilio_router.py`/`campaigns.py`/
`telephony.py`/`compliance.py` no fueron escritos por este WP (solo
leídos). `automation_scan.py`/`send_reminder_scan.py` tampoco (sin diff).
