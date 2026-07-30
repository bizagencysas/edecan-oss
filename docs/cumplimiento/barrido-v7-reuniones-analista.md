# Barrido v7 — Reuniones + Analista (fase v7)

Re-verificación completa de `reuniones`/`analista` (v6, fase v6): el bug
crítico de esquema de `meetings` (§ "el caso índice" — `file_id`/`decisiones`/
`acciones`/`temas`/`'queued'` asumidos vs. el real `source_file_id`/`minutos`
JSONB/`'pending'`) ya se había corregido en v6 (`docs/roadmap.md`, "v6
completado", punto 2), pero **nunca tuvo una ronda de re-verificación
completa contra Postgres real** — solo contra `FakeSession`, que mockea filas
con el MISMO esquema que asume el código (ciego a un `UndefinedColumnError`/
`CheckViolationError`/error de tipo real). Este documento cubre los 4
barridos pedidos (A: BYO, B: plan-flag, C: evidencia, D: esquema) y dónde
quedó cada hallazgo.

**Veredicto general: el fix de esquema de v6 era correcto** — la
re-verificación empírica contra Postgres real (Docker desechable + `alembic
upgrade head`) no encontró NINGÚN `UndefinedColumnError`/`CheckViolationError`
nuevo. Sí encontró y corrigió dos bugs reales, más finos, que solo Postgres
real (no `FakeSession`) podía revelar — ver BARRIDO D y BARRIDO C abajo.

---

## BARRIDO D — Esquema (prioridad máxima, "el caso índice")

### Metodología

```
docker run -d --name edecan-v7-meet-pg -e POSTGRES_USER=edecan \
  -e POSTGRES_PASSWORD=edecan -e POSTGRES_DB=edecan -p 55474:5432 \
  pgvector/pgvector:pg16
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:55474/edecan \
  uv run --all-packages alembic -c packages/db/alembic.ini upgrade head
```

`alembic upgrade head` corrió limpio hasta `0008_v6_expansion` (8
migraciones, sin errores). Se ejercitaron DIRECTO (llamando a las funciones
Python reales, sin pasar por FastAPI/ASGI) contra ese Postgres real:

- `reuniones.crear_reunion`/`obtener_reunion` — el `INSERT INTO meetings`
  real de `POST /v1/reuniones` (nuevo test `@pytest.mark.integration` en
  `apps/api/tests/test_reuniones_router.py`,
  `test_integracion_crear_y_obtener_reunion_contra_postgres_real`).
- `process_meeting.handle()` completo (camino `{file_id, titulo, user_id}` —
  el que ejercita MÁS SQL propio: INSERT inicial, SELECT
  `connector_accounts`, INSERT `files` de la transcripción, UPDATE final,
  INSERT `usage_events`) — STT/LLM/ffmpeg siguen monkeypatcheados (cero red
  externa real), pero **cada sentencia SQL corrió contra Postgres real**
  (nuevo test `apps/worker/tests/test_process_meeting.py::
  test_integracion_camino_feliz_contra_postgres_real`).

Ambos tests **pasan** contra Postgres real. Contenedor bajado al terminar
(`docker rm -f edecan-v7-meet-pg`), verificado con `docker ps -a` (ver
"Verificación final" abajo).

### Columna por columna — `reuniones.py` (`apps/api/edecan_api/routers/reuniones.py`)

| sitio | SQL | veredicto |
|---|---|---|
| `_insertar_reunion` | `INSERT INTO meetings (tenant_id, user_id, source_file_id, titulo, status) VALUES (..., 'pending') RETURNING *` | **Correcto.** `source_file_id`/`titulo`/`status` son columnas reales de `0008_v6_expansion.py`; `'pending'` es el único valor inicial que acepta el CHECK real. Confirmado contra Postgres real. |
| `_listar_reuniones`/`_obtener_reunion` | `SELECT * FROM meetings WHERE tenant_id = ... [AND id = ...]` | **Correcto.** Sin riesgo de columna — `SELECT *`. |
| `_borrar_reunion` | `DELETE FROM meetings WHERE id = ... AND tenant_id = ...` | **Correcto.** |

Ya estaba corregido desde v6 — esta ronda solo lo re-verificó contra
Postgres real por primera vez (antes solo `FakeSession`).

### Columna por columna — `process_meeting.py` (`apps/worker/edecan_worker/handlers/process_meeting.py`)

| sitio | SQL | veredicto |
|---|---|---|
| `_seleccionar_reunion`, `_marcar_running`, `_marcar_error` | `SELECT`/`UPDATE ... WHERE tenant_id = :tenant_id AND id = :id` (UUID crudos, sin `::uuid`) | **Correcto** — confirmado empíricamente que asyncpg acepta objetos `uuid.UUID` de Python nativos sin necesitar el cast `::uuid` que sí usa `reuniones.py` (ese router pasa `str(uuid)` en su lugar; ambos estilos son válidos, son paquetes distintos). |
| `_insertar_reunion` (worker, distinta función que la de `reuniones.py` aunque mismo nombre) | `INSERT INTO meetings (id, tenant_id, user_id, source_file_id, titulo, status, created_at, updated_at) VALUES (..., 'running', ...) RETURNING *` | **Correcto.** Confirmado contra Postgres real. |
| `_guardar_resultado` | `UPDATE meetings SET status='done', resumen=:r, minutos=:m::jsonb, transcript_file_id=:t, duracion_segundos=:d, error=:e, updated_at=:n WHERE ...` | **Bug real encontrado y corregido — ver abajo.** El resto de columnas (`resumen`/`minutos::jsonb`/`transcript_file_id`/`error`) confirmado correcto contra Postgres real. |
| `_subir_transcript` | `INSERT INTO files (id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, created_at, updated_at) VALUES (..., 'ready', ...)` | **Correcto.** Columnas verificadas contra `0001_initial.py` (tabla `files`, sin cambios desde v1) y confirmado contra Postgres real — `status='ready'` es un valor válido del CHECK. |
| `edecan_meetings/stt.py::resolver_config_stt_del_tenant` | `SELECT id FROM connector_accounts WHERE tenant_id = ... AND connector_key = ... ORDER BY created_at DESC LIMIT 1` | **Correcto.** Tabla/columnas de v1, sin cambios; confirmado contra Postgres real (la reunión de prueba, sin `connector_accounts` sembrada, cayó correctamente a `StubSTT`). |

### Hallazgo real #1 (confirmado y corregido): `duracion_segundos` — `float` sin redondear contra columna `INTEGER`

**Síntoma** (solo visible contra Postgres real, invisible con
`FakeSession`): `meetings.duracion_segundos` es `INTEGER` tanto en
`0008_v6_expansion.py` como en `edecan_db.models.Meeting` — pero
`edecan_meetings.audio.duracion_wav_segundos` (puro Python, `frames /
framerate`) devuelve un `float` con precisión de sub-segundo, y
`process_meeting.py` lo pasaba tal cual al parámetro `:duracion_segundos`.
Probado empíricamente contra Postgres real (script standalone antes de
escribir el fix, luego confirmado con el test de integración): asyncpg
**nunca rechaza el float** (no revienta, ninguna excepción) — pero lo
**trunca en silencio hacia cero** al bindearlo contra la columna `integer`:

```
{1.1: 1, 1.5: 1, 1.9: 1, 2.5: 2, 0.4: 0}   # entrada -> lo que quedó guardado
```

Nunca redondea (`1.9` no sube a `2`, `2.5` no sube a `3`) — es truncación
(`int()`), no `round()`. Hasta un segundo completo de precisión se perdía de
forma silenciosa, dependiente de un detalle no documentado del driver — el
tipo de bug que ni un `UndefinedColumnError` ni un test con `FakeSession`
detectan jamás, porque no hay excepción y `FakeSession` no aplica tipos de
columna.

**Fix**: `process_meeting.py::_guardar_resultado` ahora redondea
explícitamente (`round(duracion_segundos)`) ANTES de armar el parámetro SQL
— documentado en el propio código (ver el comentario en la función y la
sección "Re-verificación empírica v7" del docstring del módulo). Severidad
baja en la práctica (nadie necesita sub-segundos en la duración de una
reunión), pero el comportamiento ahora es intencional/redondeado-a-la-más-
cercana en vez de una truncación implícita del driver.

**Tests**: `apps/worker/tests/test_process_meeting.py::
test_camino_feliz_crea_reunion_desde_file_id_stub_stt` actualizado
(`round(1.5) == 2`, con `isinstance(..., int)`); el nuevo test de integración
`test_integracion_camino_feliz_contra_postgres_real` confirma el valor
REALMENTE guardado en Postgres (`2`, tipo `int`) tras un viaje de ida y
vuelta completo. `packages/meetings/README.md` corregido (`NUMERIC` →
`INTEGER`, con el detalle del hallazgo).

### FakeSession de los tests — ¿usan hoy las columnas de la migración?

| archivo | veredicto |
|---|---|
| `apps/api/tests/test_reuniones_router.py::_reunion_row` | **Sí, ya coincidía** — `source_file_id`/`minutos`/`status='pending'` (mismas columnas reales, sin cambios necesarios). Se le sumaron 2 tests nuevos (`obtener_reunion`/`borrar_reunion` "sin flag", ver BARRIDO B) y la sección de integración. |
| `apps/worker/tests/test_process_meeting.py::_meeting_row` | **Casi — un valor de fixture obsoleto corregido:** `test_camino_feliz_con_meeting_id_ya_existente` sembraba la fila preexistente con `status="queued"` (vocabulario viejo, nunca válido contra el CHECK real) en vez de `"pending"` — no rompía el test en sí (`FakeSession` no aplica el CHECK, y `'queued'` no está en `_ESTADOS_TERMINALES` así que la lógica igual seguía el camino correcto), pero documentaba mal el estado real que produce `POST /v1/reuniones`. Corregido a `"pending"` con un comentario explicando por qué. |

---

## BARRIDO A — BYO del STT (¿con las credenciales de quién transcribe?)

**Veredicto: correcto, ya estaba bien construido, sin cambios de código.**

`packages/meetings/edecan_meetings/stt.py::resolver_stt_del_tenant` resuelve
"tenant → stub", SIN paso de plataforma — replica el único precedente real
del repo (`apps/api/edecan_api/routers/voice.py::_stt_para_tenant`), lee
`connector_accounts`/`TokenVault` del TENANT (`connector_key="voice_stt"`,
mismo string EXACTO que `edecan_api.deps.VOICE_STT_CONNECTOR_KEY` — ahora
pinneado por un test, ver abajo), y cae a `StubSTT()` fail-closed ante
CUALQUIER fallo (sin cuenta, sin bundle, JSON corrupto, sesión caída) — nunca
lee `DEEPGRAM_API_KEY`/`VOICE_STT_PROVIDER` de `Settings`/`.env` de
plataforma. El módulo ni siquiera acepta un parámetro `settings`/`api_key`
de plataforma en su firma, así que no hay forma de que se cuele
estructuralmente. `process_meeting.py` (worker) hace lo mismo: sin STT
propio → `StubSTT` con `meetings.error` accionable (nunca degrada
silenciosamente en el sentido de "usar la voz de alguien más"), y si el
tenant SÍ tiene credencial pero Deepgram falla de verdad (ej. 401), el job
marca `status='error'` y relanza — nunca cae a stub como si nada.

**Cobertura de test ya existente (patrón `test_voice_byo.py`, no hizo falta
un archivo nuevo):**

- `packages/meetings/tests/test_stt.py::
  test_resolver_stt_nunca_usa_credencial_de_plataforma` — fija
  `DEEPGRAM_API_KEY=CLAVE_DE_PLATAFORMA_NUNCA_DEBE_USARSE` en el entorno y
  confirma que sin credencial del tenant, el resultado sigue siendo
  `StubSTT` (nunca lee la variable de entorno).
- `apps/worker/tests/test_process_meeting.py::
  test_sin_credencial_stt_nunca_llama_deepgram_real` — sin ningún
  `respx.mock` activo, confirma que el job nunca intenta hablar con la API
  real de Deepgram si el tenant no conectó nada.
- `apps/worker/tests/test_process_meeting.py::
  test_error_real_de_deepgram_marca_error_y_relanza` — con credencial real
  del tenant pero Deepgram devolviendo 401, confirma `status='error'` +
  re-raise (nunca degrada a stub).

Nuevo (BARRIDO A, esta ronda): `apps/api/tests/
test_v7_sweep_reuniones_analista.py::
test_voice_stt_connector_key_no_diverge_entre_apps_api_y_edecan_meetings` —
pinnea que `edecan_meetings.stt.VOICE_STT_CONNECTOR_KEY` y
`edecan_api.deps.VOICE_STT_CONNECTOR_KEY` (duplicados a propósito entre
paquetes hermanos, `ARCHITECTURE.md` §10.1) siguen siendo el mismo string
EXACTO (`"voice_stt"`) — si divergieran, `resolver_stt_del_tenant` dejaría
de encontrar la credencial que sí guardó `PUT /v1/credentials/voice/stt`.

---

## BARRIDO B — Plan-flag

**Veredicto: correcto en ambos routers, con 3 huecos de COBERTURA DE TEST
cerrados (el comportamiento real ya era el correcto).**

### `reuniones.py` — los 4 endpoints exigen `tools.meetings`

| endpoint | `Depends(...)` real | cobertura de test ANTES de este WP | cobertura AHORA |
|---|---|---|---|
| `POST ""` (`crear_reunion`) | `_require_tools_meetings` | `test_crear_reunion_sin_flag_returns_403` | sin cambios (ya cubierto) |
| `GET ""` (`listar_reuniones`) | `_require_tools_meetings` | `test_listar_reuniones_sin_flag_returns_403` | sin cambios (ya cubierto) |
| `GET /{id}` (`obtener_reunion`) | `_require_tools_meetings` | **ninguna** | `test_obtener_reunion_sin_flag_returns_403` (nuevo) |
| `DELETE /{id}` (`borrar_reunion`) | `_require_tools_meetings` | **ninguna** | `test_borrar_reunion_sin_flag_returns_403` (nuevo) |

Confirmado además por introspección ESTÁTICA de las firmas reales (no
depende de invocar cada endpoint):
`test_v7_sweep_reuniones_analista.py::
test_reuniones_los_4_endpoints_dependen_de_require_tools_meetings`.

`ResumirReunionTool` (`resumir_reunion`, la tool de chat equivalente):
`requires_flags = frozenset({"tools.meetings"})` — mismo flag EXACTO que el
router, pinneado por `test_v7_sweep_reuniones_analista.py::
test_resumir_reunion_exige_el_mismo_flag_que_el_router`.

### `analista.py` — deliberadamente SIN ningún flag

Confirmado que la decisión es genuinamente deliberada y consistente en las
tres fuentes que la documentan (el docstring del propio router,
`edecan_docanalysis/__init__.py`, `docs/analista.md`) — las 8 tools de chat
de `edecan_docanalysis` (`analizar_tabla`, `predecir_serie`,
`detectar_anomalias`, etc.) tampoco declaran `requires_flags`, y los 4
endpoints REST son la MISMA capacidad de análisis por una superficie
determinista distinta, así que gatearla con un flag distinto sería
inconsistente con el resto del paquete.

**Ningún endpoint reutiliza (por dispatch compartido) una capacidad que en
otro lado sí tiene flag**: verificado leyendo los 5 helpers puros de
`edecan_docanalysis` que este router consume
(`descargar_archivo_de_tenant`/`analizar_tabla_bytes`/
`extraer_columnas_bytes`/`predecir`/`detectar_anomalias`/`generar_svg`) —
ninguno toca `ctx.llm`/`edecan_llm` (ya lo pinnea
`test_analista_router.py::test_el_router_no_importa_nada_de_edecan_llm`, un
test AST que recorre los imports reales del módulo), ninguno dispara un job
de fondo ni una tool `dangerous`, y ninguno lee `connector_accounts`/
`TokenVault` de ningún proveedor externo — es determinismo puro sobre bytes
que el propio tenant ya subió.

Nuevo (esta ronda, pinnea la decisión con dos ángulos distintos):

1. **Introspección estática** — `test_v7_sweep_reuniones_analista.py::
   test_analista_ningun_endpoint_depende_de_un_gate_de_flag`: los 4
   endpoints solo dependen de `{get_current_user, get_tenant_session,
   get_repo, get_settings}` — ninguna función `_require_*`. Revienta de
   inmediato si alguien agrega un flag por accidente en el futuro.
2. **Contraste end-to-end (HTTP real)** —
   `test_mismo_plan_sin_meetings_bloquea_reuniones_pero_no_analista`: con el
   MISMO plan (`hosted_basic`, el único de los 4 planes reales con
   `tools.meetings=False`), `GET /v1/reuniones` da `403` y `GET
   /v1/analista/archivos` da `200 []` — la comparación directa que motivó la
   decisión de diseño, nunca antes pinneada en un solo test.

---

## BARRIDO C — Evidencia (escrituras + rollback)

**Veredicto: `reuniones.py` seguro por diseño (sin cambios); `analista.py`
no aplica (sin escrituras); `process_meeting.py` tenía un bug real,
corregido.**

### `reuniones.py` — sin bug, docstring ya lo razonaba explícitamente

`crear_reunion` hace `INSERT INTO meetings` (status='pending') → `enqueue(...)`
→ `repo.add_audit_log(...)`, TODO en la MISMA sesión/transacción de la
request (`get_repo` depende de `get_tenant_session` — FastAPI cachea la
resolución de dependencias por request, así que `repo.session is session`,
confirmado leyendo `apps/api/edecan_api/deps.py`). El propio módulo ya
documentaba, ANTES de este WP, por qué esto es seguro sin necesitar un
commit explícito (a diferencia de `crear_clon_voz`,
`docs/seguridad-modelo-amenazas.md` puntos 8/9): la fila `meetings` recién creada NO es
evidencia legal/cumplimiento (consentimiento, audit log de una acción de
terceros, opt-in/opt-out, envío real de SMS/llamada) — es un ítem de
trabajo. Si `enqueue`/`add_audit_log` fallan DESPUÉS del `INSERT`, el
ROLLBACK automático de `get_tenant_session` se lleva la fila recién
insertada junto con todo lo demás — y **ese es el comportamiento correcto**:
mejor "nada pasó" (cliente ve un 500, ningún job fantasma referenciando una
fila que existe) que una fila `meetings` huérfana en `status='pending'` que
ningún job procesará. Re-verificado esta ronda, sin cambios de código —
confirmado además que `borrar_reunion` tiene el mismo patrón seguro (DELETE
+ audit_log en la misma transacción; si `add_audit_log` falla, ambos se
revierten juntos, consistente).

### `analista.py` — no aplica

Los 4 endpoints son de solo lectura — ni siquiera crean un archivo nuevo (a
diferencia de `generar_grafico`/`exportar_analisis` por chat). Cero
`INSERT`/`UPDATE`/`DELETE` en todo el archivo. Nada que barrer.

### `process_meeting.py` — bug real encontrado y corregido: `usage_events` compartía transacción con el resultado ya exitoso

**Síntoma**: `_guardar_resultado` (el `UPDATE meetings SET status='done',
resumen=..., minutos=..., ...` — el resultado real que el tenant está
esperando) y `repo.add_usage_event(...)` (telemetría de facturación,
`kind="llm_tokens"`) corrían dentro de la MISMA `async with
deps.session_factory(None) as session:` — mismo patrón que
`docs/seguridad-modelo-amenazas.md` puntos 8/9, aplicado esta vez a la escritura
PRINCIPAL del handler en vez de a evidencia legal en sentido estricto. Un
fallo en `add_usage_event` (fila con constraint que choca, blip de conexión
entre las dos sentencias de la misma transacción) se llevaba puesto el
`UPDATE status='done'` YA EXITOSO — y el `except Exception as exc:` exterior
entonces marcaba la reunión `status='error'` con el mensaje del fallo de
`usage_events`, pese a que la transcripción y las minutas SÍ se habían
generado correctamente. Peor todavía: `'error'` es un estado TERMINAL
(`_ESTADOS_TERMINALES`), así que **ningún reintento automático habría vuelto
a procesar esa reunión jamás** — el tenant habría visto una reunión
"fallida" para siempre, sin ningún camino de recuperación salvo volver a
subir el archivo y re-pagar el costo de STT/LLM ya incurrido una vez.

**Fix**: `add_usage_event` se movió a SU PROPIA transacción corta,
DELIBERADAMENTE fuera del `try` principal — si falla, se registra con
`logger.exception(...)` pero NUNCA revierte ni reintenta el resultado ya
entregado (mismo criterio "best-effort" que cualquier telemetría
secundaria, y mismo patrón de aislamiento por escritura que ya usa
`campaigns.handle` desde v6 para targets de campaña).

**¿Un fallo del STT tras escribir deja estado consistente?** Sí, ya antes de
este WP: cada escritura intermedia (marcar `running`, subir transcript,
insertar `files`) ocurre en su PROPIA transacción corta y comitea
independientemente ANTES de que el siguiente paso (que sí puede fallar)
empiece — a diferencia del patrón puntos 8/9 (una única transacción larga
con múltiples escrituras), este handler ya usaba "commit conforme se
avanza" desde v6, así que un fallo de STT/LLM a mitad de camino solo pierde
el trabajo de ESE paso, nunca revierte lo ya comiteado en pasos previos. El
ÚNICO punto donde dos escrituras compartían transacción era exactamente el
que se corrigió arriba.

**¿Reintento duplica?** Documentado en el docstring del módulo (sección
nueva "Reintento y duplicación", BARRIDO C): `_ESTADOS_TERMINALES` hace que
un reintento sobre un job ya resuelto (éxito o error) sea un no-op seguro —
nunca reprocesa ni duplica. El único escenario de duplicación real
posible requeriría que el PROCESO se caiga a mitad de `handle()` (crash
duro, sin que el `except`/`_marcar_error` llegue a correr) Y que el broker
redespache el mismo mensaje mientras la fila sigue en `status='running'` —
en ese caso `_subir_transcript` insertaría una SEGUNDA fila `files`/objeto
S3 (huérfano el intento anterior, el resultado final en `meetings` queda
correcto porque la última escritura gana). Riesgo idéntico al que ya asume
cualquier otro handler multi-paso de `apps/worker` bajo semántica
at-least-once del broker (`generate_podcast.py`, `ingest_file.py`) — no hay
un mecanismo de deduplicación general en este repo para ese caso límite, y
agregar uno solo para `process_meeting` está fuera de alcance de este WP
(exigiría una pieza de arquitectura compartida). Revisado y documentado a
propósito, no un hallazgo nuevo que arreglar acá.

**Test nuevo**: `apps/worker/tests/test_process_meeting.py::
test_fallo_en_usage_event_no_revierte_el_resultado_ya_guardado` — mockea
`add_usage_event` para que lance, confirma que `handle()` YA NO relanza (el
fallo se atrapa y se loguea) y que el único `UPDATE meetings ... status`
sigue siendo el de `'done'` (nunca aparece un segundo `UPDATE` con
`status='error'` pisándolo).

**Nota sobre el contador `commits` de `FakeSession`** (convención de otros
barridos de evidencia, ej. `docs/cumplimiento/barrido-evidencia-v6.md`):
`process_meeting.py` NUNCA llama `session.commit()` explícito en ningún
punto — a diferencia de los routers HTTP con una única transacción larga por
request (donde SÍ hace falta un commit explícito a mitad de camino para que
algo sobreviva un `raise` posterior), este handler ya usa `async with
deps.session_factory(None) as session:` por separado para cada escritura
(commit IMPLÍCITO al cerrar cada bloque) — el `_FakeSession` de este archivo
por eso no necesita ni tiene un método `commit()`/contador (nada lo llama).
La verificación equivalente y más directa acá es la que hace el test de
arriba: contar cuántas veces aparece `status = 'error'`/`status = 'done'` en
`session.llamadas` — el mismo tipo de evidencia (¿sobrevivió la escritura
que importa?), adaptada a que este handler comitea "sobre la marcha" en vez
de al final de una única transacción.

---

## Deliverables — archivos tocados

| archivo | qué cambió |
|---|---|
| `apps/api/edecan_api/routers/reuniones.py` | Sin cambios de código — re-verificado (BARRIDO D/C), correcto tal cual. |
| `apps/api/edecan_api/routers/analista.py` | Sin cambios de código — re-verificado (BARRIDO B/C), correcto tal cual. |
| `apps/worker/edecan_worker/handlers/process_meeting.py` | 2 fixes: redondeo explícito de `duracion_segundos` (BARRIDO D) + `usage_events` en transacción propia, best-effort (BARRIDO C). Docstring del módulo ampliado con 2 secciones nuevas ("Reintento y duplicación", "Re-verificación empírica v7"). |
| `packages/meetings/README.md` | `duracion_segundos NUMERIC` → `INTEGER` (con el detalle del hallazgo); nota de actualización sobre `JOB_TYPES`/`FLAG_TOOLS_MEETINGS`/`V6_ROUTER_NAMES` (ya pinned de verdad, ya no "pendiente"). |
| `docs/reuniones.md` | 3× `'queued'` → `'pending'`; nota de duración redondeada; sección "Limitaciones" corregida (`JOB_TYPES`/flag/router YA pinned, con referencia a este informe). |
| `docs/analista.md` | Sin cambios — ya estaba sincronizado con el código real. |
| `apps/api/tests/test_reuniones_router.py` | Docstring/comentarios corregidos (router SÍ está en `V6_ROUTER_NAMES`, `tools.meetings` SÍ pinned); 2 tests nuevos (`obtener_reunion`/`borrar_reunion` sin flag); sección de integración nueva contra Postgres real (`db_real`, `_seed_tenant_y_archivo`, `test_integracion_crear_y_obtener_reunion_contra_postgres_real`). |
| `apps/api/tests/test_analista_router.py` | Sin cambios — ya cubría lo necesario. |
| `apps/api/tests/test_v7_sweep_reuniones_analista.py` | **Nuevo.** 9 tests: introspección estática de flags (2), contraste HTTP end-to-end (1), constantes duplicadas BYO/disclaimer/flag-de-tool (3), esquema/vocabulario (3). |
| `apps/worker/tests/test_process_meeting.py` | Fixture `status="queued"` → `"pending"`; assert de `duracion_segundos` actualizado al valor redondeado; 1 test nuevo (BARRIDO C, usage_events aislado); sección de integración nueva contra Postgres real (`db_real`, `_seed_tenant_y_archivo`, `test_integracion_camino_feliz_contra_postgres_real`). |
| `docs/cumplimiento/barrido-v7-reuniones-analista.md` | Este documento. |

## Fuera de alcance — anotado, no tocado

- `apps/web/src/lib/api-reuniones.ts` — responsable de la fase v7. Verificado
  (solo lectura) que YA está corregido (`ReunionStatus = "pending"`, no
  `"queued"`) y re-confirmado sin regresión por `docs/cumplimiento/
  barrido-v7-ux.md` (otro WP en vuelo). Nada que anotar de nuevo aquí.
- `docs/api.md` — fuera de las rutas editables de este WP; `docs/
  cumplimiento/barrido-v7-apimd.md` (otro WP) ya encontró y corrigió el
  mismo `status="queued"` desincronizado ahí.
- `apps/api/edecan_api/routers/reuniones.py` líneas 71/83/120 (`try/except
  ImportError` de `FLAG_TOOLS_MEETINGS`, comentario "linchpin de v6 todavía
  no aterrizó el flag"): **revisado, NO es un hallazgo** — mismo patrón
  exacto y misma redacción que `apps/api/edecan_api/routers/viajes.py`
  (`FLAG_TOOLS_TRAVEL`, landed desde v5, comentario sin actualizar desde
  entonces) — es la justificación evergreen de por qué existe el guard
  defensivo (tolerancia a aterrizajes parciales en paralelo), no una
  afirmación de estado actual que haya que mantener al día línea por línea.
  Se dejó tal cual por consistencia con el resto del repo.

---

## Verificación final

Postgres desechable (`edecan-v7-meet-pg`, puerto `55474`) usado para BARRIDO
D y las 2 pruebas `@pytest.mark.integration` nuevas — bajado al terminar:

```
docker rm -f edecan-v7-meet-pg
docker ps -a   # sin edecan-v7-meet-pg; solo contenedores de OTROS WPs en vuelo
```

```
uv run --all-packages ruff check apps/api/edecan_api/routers/reuniones.py \
  apps/api/edecan_api/routers/analista.py \
  apps/worker/edecan_worker/handlers/process_meeting.py packages/meetings/ \
  apps/api/tests/test_reuniones_router.py apps/api/tests/test_analista_router.py \
  apps/api/tests/test_v7_sweep_reuniones_analista.py \
  apps/worker/tests/test_process_meeting.py
```
→ **All checks passed!**

`ruff format --check` sobre los mismos archivos marca 6 como "would
reformat" (`reuniones.py`, `test_analista_router.py`, `process_meeting.py`,
`packages/meetings/edecan_meetings/{stt,tools}.py`, `packages/meetings/
tests/test_tools.py`) — **verificado que es un drift de formato
PREEXISTENTE de todo el repo** (`ruff 0.15.20` instalado en este entorno
difiere de cómo se formateó el código originalmente), no algo introducido
por este WP: los 2 hunks que `ruff format --diff` propone en
`process_meeting.py` caen en `_seleccionar_reunion`/`_insertar_reunion`
(funciones que este WP NUNCA tocó), y el mismo síntoma aparece en archivos
de OTRO dominio completamente ajeno a este WP y nunca tocados
(`apps/api/edecan_api/routers/voz_avanzada.py`, confirmado como control).
Ninguna de las líneas que este WP escribió/editó necesita reformateo (los 3
archivos de test donde SÍ se corrió `ruff format` explícito tras cada
edición no aparecen en la lista de 6). Deliberadamente NO se corrió `ruff
format` de archivo completo sobre código preexistente ajeno a este WP, para
no introducir un diff no relacionado con el paquete de trabajo.

```
uv run --all-packages pytest -q apps/api/tests apps/worker/tests packages/meetings/tests -m "not integration"
```
→ **1 failed, 1387 passed, 23 deselected** en la primera corrida. El único
fallo, `apps/worker/tests/test_v7_evidencia.py::
test_run_automation_save_run_terminal_es_independiente_de_la_sesion_del_turno`,
es **ajeno a este WP**: el archivo no está entre las rutas que este paquete
puede tocar, el traceback es un `KeyError: 'external_account_id'` dentro de
`edecan_worker.deps._build_mcp_tools` (dominio MCP, fase v7 en vuelo aparte,
nada que ver con `reuniones`/`analista`/`process_meeting`), y **pasa limpio
en aislamiento** (`uv run --all-packages pytest
apps/worker/tests/test_v7_evidencia.py::test_run_automation_save_run_terminal_es_independiente_de_la_sesion_del_turno`
→ `1 passed`) — es una fuga de estado entre archivos de test de OTRO
dominio en vuelo concurrente (mismo patrón ya documentado en
`docs/seguridad-modelo-amenazas.md`, "fuga de tareas asyncio entre archivos de test"),
no una regresión de este paquete. Re-corriendo la suite completa
excluyendo ESE único test ajeno: **1388 passed, 24 deselected, 0 failed**
(170.47s) — cero fallos atribuibles a `reuniones`/`analista`/`process_meeting`
en las 3 carpetas.

```
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:55474/edecan \
  uv run --all-packages pytest -q \
  apps/api/tests/test_reuniones_router.py apps/worker/tests/test_process_meeting.py -m integration
```
→ **2 passed** (los 2 tests de integración nuevos, contra Postgres real).
