# Barrido v7 — Voz avanzada + podcasts: plan-flag, esquema, BYO y evidencia (WP-V7-03)

Este documento registra el barrido dedicado de voz avanzada (clonación, v5) y podcasts
(v6) — `apps/api/edecan_api/routers/voz_avanzada.py`, `apps/worker/edecan_worker/handlers/
generate_podcast.py`, `packages/voice/edecan_voice/` — contra los cuatro patrones de bug que
las auditorías v4-v6 encontraron repetidamente en el resto del repo (`HOTFIXES_PENDIENTES.md`,
`docs/cumplimiento/barrido-evidencia-v6.md`, leídos completos antes de escribir una línea):

- **BARRIDO B** — flag de plan de grano fino aplicado a la superficie correcta: el router
  sirve DOS capacidades (`voice.cloning` para clones, `tools.podcast` para podcasts) desde el
  mismo archivo — patrón del bug de `usar_computadora`/`companion.remote_input`/
  `companion.ide` (un dispatch/router compartido, un flag fino solo aplicado a una de las dos
  superficies).
- **BARRIDO D** — esquema SQL asumido vs. esquema real (patrón del bug de v6 en
  `reuniones.py`/`process_meeting.py`).
- **BARRIDO A** — bring-your-own fail-closed: el TTS de `generate_podcast` nunca debe caer a
  `ELEVENLABS_API_KEY`/cadena AWS de plataforma.
- **BARRIDO C** — escritura de evidencia legal/cumplimiento perdida en un rollback de sesión
  (patrón de los puntos 8/9 de `HOTFIXES_PENDIENTES.md`, ya resuelto una vez en
  `crear_clon_voz` — verificar que sigue intacto y que no quedó ningún otro sitio sin cubrir).

**Resumen ejecutivo**: BARRIDO B, BARRIDO D y BARRIDO A confirmaron que el código ya estaba
correcto — el router ya distinguía los dos flags endpoint por endpoint, el esquema real
coincide exacto con el código (verificado además de forma empírica contra Postgres real), y
el bring-your-own de TTS (incluido el gate `EDECAN_LOCAL_MODE` de Polly) sigue intacto. No
hizo falta ningún fix en esos tres frentes — se agregaron tests nuevos que PINNEAN esas
garantías para que una regresión futura no pase desapercibida. BARRIDO C sí encontró y cerró
**un hallazgo real** (no del patrón "evidencia legal", sino su primo operativo: un audio
huérfano en S3 si el fallo llega justo después de subir) en `generate_podcast.py` — detalle
en su sección.

---

## BARRIDO B — plan-flag: `voice.cloning` vs `tools.podcast` en el mismo router

**Metodología**: se listaron los 7 endpoints de `voz_avanzada.py` (`GET /voces`, `POST/GET/
DELETE /clones*`, `POST/GET/GET /podcasts*`) y se verificó, leyendo el código (no solo
`grep`), qué dependencia de FastAPI gatea cada uno:

| Endpoint | Dependencia | Flag que exige | Veredicto |
|---|---|---|---|
| `GET /v1/voz/voces` | `_require_voice_web` | `voice.web` | Correcto (`ARCHITECTURE.md` §14.e: listar voces/sintetizar NO gatean `voice.cloning`, misma capacidad que `/v1/voice/speak`) |
| `POST /v1/voz/clones` | `_require_voice_cloning` | `voice.cloning` | Correcto |
| `GET /v1/voz/clones` | `_require_voice_cloning` | `voice.cloning` | Correcto |
| `DELETE /v1/voz/clones/{id}` | `_require_voice_cloning` | `voice.cloning` | Correcto |
| `POST /v1/voz/podcasts` | `_require_tools_podcast` | `tools.podcast` | Correcto |
| `GET /v1/voz/podcasts` | `_require_tools_podcast` | `tools.podcast` | Correcto |
| `GET /v1/voz/podcasts/{id}` | `_require_tools_podcast` | `tools.podcast` | Correcto |

A diferencia del bug real de `usar_computadora` (un ÚNICO dispatch table —
`edecan_companion.actions.ACTIONS`— que multiplexaba acciones con permisos distintos detrás
de un solo flag base), acá cada endpoint tiene su PROPIA dependencia (`_require_voice_cloning`
vs. `_require_tools_podcast`), cada una gateando un string literal distinto (`_FLAG_VOICE_
CLONING = "voice.cloning"` / `_FLAG_TOOLS_PODCAST = "tools.podcast"`, ninguno reutilizado
entre los dos grupos de endpoints) — no hay dispatch compartido que confundir. Se revisó
también `packages/voice/edecan_voice/tools.py` (`ListarVocesTool`/`SintetizarVozTool`, las
únicas 2 tools de agente de este paquete): ninguna de las dos toca `voice.cloning` ni
`tools.podcast`, ambas gatean solo `voice.web` — ya pinned en
`packages/voice/tests/test_voice_tools.py` desde antes de este WP.

### Por qué hacía falta un test NUEVO además de esta lectura

La matriz de planes REAL (`edecan_schemas.plans.PLANES`, `ARCHITECTURE.md` §14.c) trae
`voice.cloning` y `tools.podcast` con el MISMO valor en los 4 planes existentes hoy
(`free_selfhost`: ambos `True`; `hosted_basic`: ambos `False`; `hosted_pro`/`hosted_business`:
ambos `True`) — así que ningún `plan_key` real de hoy puede, por sí solo, DEMOSTRAR que cada
endpoint exige el flag de su propia capacidad en vez de un flag compartido por error: las dos
hipótesis ("cada endpoint exige el suyo" vs. "los dos endpoints comparten un flag,
copiado/pegado del otro grupo") predicen exactamente el mismo resultado observable contra los
4 planes reales tal como están hoy. Ya lo señalaban, cada uno en su archivo,
`test_voz_avanzada.py` ("`voice.cloning` no está pinned todavía..." — comentario desactualizado
desde que WP-V5-01 aterrizó la fila real en `PLANES`, no corregido en este WP para no tocar el
mecanismo de tests que sigue funcionando) y `test_podcasts_router.py` (usa `plan_key` real
directo, sin necesitar el truco de `dependency_overrides`, precisamente porque hoy alcanza —
pero eso mismo es lo que no distingue las dos hipótesis de arriba).

**Fix**: `apps/api/tests/test_v7_sweep_voz.py` (archivo nuevo, no reemplaza a los otros dos)
aísla los dos flags a mano — parte de los flags REALES de un plan real
(`edecan_api.deps.flags_for_plan("hosted_pro")`, que lee `edecan_schemas.plans.PLANES` de
verdad) y sobreescribe SOLO `voice.cloning`/`tools.podcast` (importados como constantes reales
de `edecan_schemas.plans`, nunca strings inventados) en las DOS direcciones que ningún plan
real deja ver hoy: `voice.cloning=True, tools.podcast=False` y `voice.cloning=False,
tools.podcast=True`. Con `pytest.mark.parametrize` sobre las dos direcciones, 6 tests
(`test_crear_clon_exige_su_propio_flag_no_el_de_podcast`,
`test_listar_clones_exige_su_propio_flag_no_el_de_podcast`,
`test_revocar_clon_exige_su_propio_flag_no_el_de_podcast`,
`test_crear_podcast_exige_su_propio_flag_no_el_de_cloning`,
`test_listar_podcasts_exige_su_propio_flag_no_el_de_cloning`,
`test_obtener_podcast_exige_su_propio_flag_no_el_de_cloning` — 12 casos en total) confirman
que:

- Con el flag PROPIO apagado (sin importar el del otro grupo): `403`, `fake_session.llamadas
  == []` (el gate corta antes de tocar la sesión).
- Con el flag PROPIO encendido (sin importar el del otro grupo): el endpoint llega a SU
  PROPIA validación (`400`/`404`/`200`, nunca `403`).

Más un test unitario (`test_tools_de_voz_no_gatean_cloning_ni_podcast`) que importa las clases
reales de `edecan_voice.tools` y confirma `requires_flags == frozenset({"voice.web"})`.

**Verificado**: `uv run --all-packages pytest -q apps/api/tests/test_v7_sweep_voz.py -m "not
integration"` → **13 passed**. Suite completa de `apps/api/tests`/`apps/worker/tests` (ver
verificación final) sin regresiones.

---

## BARRIDO D — esquema SQL asumido vs. esquema real

**Metodología**: cada `INSERT`/`UPDATE`/`SELECT` de `voz_avanzada.py`,
`generate_podcast.py`, `edecan_voice/tenant.py` y `edecan_voice/tools.py` se comparó columna
por columna contra las migraciones reales (`packages/db/alembic/versions/
0007_v5_expansion.py` para `voice_consents`, `0008_v6_expansion.py` para `podcasts`,
`0001_initial.py` para `files`/`connector_accounts`/`usage_events`) y contra
`packages/db/edecan_db/models.py` — nunca contra el docstring de este mismo router (que sí
documenta el esquema, pero como referencia derivada, no como fuente).

| Tabla | Columnas que el código lee/escribe | Migración real | Veredicto |
|---|---|---|---|
| `voice_consents` | `id, tenant_id, user_id, voice_name, attestation, status, consent_file_id, provider_voice_id, created_at, updated_at` (nunca `meta`, que trae `DEFAULT '{}'`) | `0007_v5_expansion.py`: mismas columnas, mismos tipos/nullability (`consent_file_id` nullable sin FK, `status` con `CHECK ('attested','revoked')`) | Coincide exacto |
| `podcasts` | `id, tenant_id, user_id, titulo, guion, status, file_id, error, created_at, updated_at` | `0008_v6_expansion.py`: mismas columnas (`guion jsonb NULL` — el router/handler SIEMPRE proveen un valor explícito en el INSERT, la nullability de esquema es inofensiva; `status` con `CHECK ('pending','running','done','error')`) | Coincide exacto |
| `files` (usada por `_subir_archivo` del router y por el INSERT crudo del handler) | `id, tenant_id, user_id, s3_key, filename, mime, size_bytes, status, created_at, updated_at` | `0001_initial.py`: mismas columnas; `status='ready'` (literal usado por ambos) SÍ está en el `CHECK ('uploaded','processing','ready','error')` | Coincide exacto |
| `connector_accounts` (usada por `_tts_config_del_tenant`/`resolver_config_tts_del_tenant`) | `id, tenant_id, connector_key, external_account_id, created_at` (vía `SELECT ... ORDER BY created_at DESC LIMIT 1`) | `0001_initial.py`: mismas columnas | Coincide exacto |
| `usage_events` (usada por `_registrar_uso_de_voz` de `edecan_voice/tools.py`) | `tenant_id, kind='voice_seconds', quantity, meta` | `0001_initial.py`: `CHECK kind IN ('llm_tokens','voice_seconds','storage_bytes','messages')` — `'voice_seconds'` es un valor válido | Coincide exacto |

**FakeSession de los tests preexistentes** (`test_voz_avanzada.py::_voice_consent_row`,
`test_podcasts_router.py::_podcast_row`, `test_generate_podcast.py::_podcast_row`): también
verificadas columna por columna contra la migración real — ninguna incluye `meta` (coincide
con que ningún `INSERT`/`SELECT *` la nombra a mano, Postgres aplica el default) ni ninguna
columna inventada. El "hallazgo invisible por mocks calcando el mismo error" que sí ocurrió en
`reuniones.py` (v6, `HOTFIXES_PENDIENTES.md`) no se repite aquí — las filas fake ya estaban
alineadas con el esquema real antes de este WP.

### Verificación empírica (hecha)

Postgres desechable, migrado de verdad hasta `head` (incluye `0007_v5_expansion` y
`0008_v6_expansion`):

```
docker run -d --name edecan-v7-voz-pg -e POSTGRES_USER=edecan -e POSTGRES_PASSWORD=edecan \
  -e POSTGRES_DB=edecan -p 55473:5432 pgvector/pgvector:pg16
DATABASE_URL=postgresql+asyncpg://edecan:edecan@localhost:55473/edecan \
  uv run --all-packages alembic -c packages/db/alembic.ini upgrade head
```

`alembic upgrade head` corrió limpio hasta `0008_v6_expansion` (las 8 migraciones en cadena,
sin error). Contra ese Postgres real se insertaron `tenants`/`users` mínimos y se ejecutó,
palabra por palabra, el SQL parametrizado EXACTO de cada función de escritura/lectura de este
WP (`_insertar_voice_consent`, `_actualizar_provider_voice_id`, `_listar_voice_consents`,
`_obtener_voice_consent`, `_revocar_voice_consent`, `_insertar_podcast`, `_listar_podcasts`,
`_obtener_podcast` del router; `_marcar_running`/`_marcar_done`/`_marcar_error`/el `INSERT INTO
files` del handler; el `SELECT`/`INSERT` de `connector_accounts` y `usage_events` de
`edecan_voice`) — las ~15 sentencias corrieron sin ningún error de columna/tipo/CHECK.
Contenedor bajado con `docker rm -f edecan-v7-voz-pg` al terminar, confirmado con `docker ps
-a` (ver la nota de limpieza en la verificación final).

**Ningún archivo de producción se modificó por este barrido** — no había ninguna discrepancia
que corregir.

---

## BARRIDO A — bring-your-own fail-closed del TTS de podcasts

**Metodología**: se releyó `generate_podcast.py`, `edecan_creative.podcast` (paquete hermano,
FUERA de las rutas editables de este WP — solo lectura), `edecan_voice/tenant.py`,
`edecan_voice/polly.py`, `edecan_voice/registry.py` y `apps/api/edecan_api/routers/
credentials.py` (sección `voice_tts`, también fuera de las rutas editables) preguntando, para
cada proveedor, la pregunta pinned de `HOTFIXES_PENDIENTES.md` ("Barrido v5"): **¿tiene al
menos un campo de credencial que el tenant deba traer?**

| Proveedor | ¿Campo de credencial del tenant? | Verificación |
|---|---|---|
| ElevenLabs (voz/clones/podcasts) | Sí, `api_key` (guardada por el tenant vía `PUT /v1/credentials/voice/tts`) | Todas las llamadas HTTP (`cloning.py`, `edecan_creative.podcast._elevenlabs_*`) reciben `api_key` por parámetro — ninguna función lee `ELEVENLABS_API_KEY` de `Settings`/`.env`. |
| Polly (voz web, NO alcanzable desde podcasts — ver nota abajo) | No — se autentica con la cadena de credenciales AWS *ambiente* del proceso | Gate doble intacto: `credentials.py::put_voice_tts_credentials` rechaza guardar `provider="polly"` fuera de `EDECAN_LOCAL_MODE=True` (línea `if provider == "polly" and not getattr(settings, "EDECAN_LOCAL_MODE", False)`); `edecan_voice.tenant.resolver_tts_del_tenant` solo pasa `allow_ambient_credentials=True` DENTRO de su propio chequeo `EDECAN_LOCAL_MODE`; `PollyTTS.__init__` exige ese flag explícito o lanza `ValueError` (segunda capa, `edecan_voice/polly.py`). Los tres puntos verificados intactos, sin cambios de este WP. |
| Stub (sin credencial, o `polly` fuera de local mode) | N/A — nunca red real | `_wav_silencio`/`_beep_wav_stub` (`edecan_creative.podcast`) 100% puro-Python. |

**Nota sobre Polly y podcasts**: `edecan_creative.podcast._cfg_elevenlabs_utilizable` (la
única puerta de entrada a un proveedor real dentro de `sintetizar_segmento`/`generar_efecto`)
exige literalmente `cfg.get("provider") == "elevenlabs"` — un tenant con `provider="polly"`
conectado NUNCA llega a intentar Polly desde el flujo de podcasts, cae directo al stub. Es más
estricto de lo necesario (no es un hueco de seguridad, es una capacidad no implementada), así
que no se tocó — mencionado aquí solo para que quede explícito que se investigó.

**`generate_podcast.py` en sí** (`apps/worker/edecan_worker/handlers/generate_podcast.py`):
`deps.settings` SOLO se lee para `S3_BUCKET` (no-secreto, nombre de bucket) — nunca se pasa a
`resolver_config_tts_tenant`/`sintetizar_segmento`/`generar_efecto`, que ni siquiera aceptan un
parámetro `settings` (confirmado leyendo la firma completa de las 3 funciones en
`edecan_creative/podcast.py`). El test preexistente
`test_generate_podcast_sin_credencial_del_tenant_nunca_usa_centinela_de_plataforma` (ya
presente antes de este WP) instala `ELEVENLABS_API_KEY=FUGA_DE_PLATAFORMA_NO_DEBE_APARECER` en
`Settings` de PLATAFORMA y confirma que el WAV resultante nunca contiene ese centinela — se
re-ejecutó sin cambios, sigue en verde.

### Aclaración de terminología: "fail-closed" del TTS de podcasts vs. `TenantLLMNotConnectedError`

El encargo de este WP pedía verificar el "mismo criterio fail-closed que
`TenantLLMNotConnectedError`" — vale la pena dejar explícito que el comportamiento de
`generate_podcast` es **fail-closed en el sentido de nunca filtrar una credencial de
plataforma** (que es la garantía que both comparten), pero **deliberadamente NO replica el
"fail-hard" de `TenantLLMNotConnectedError`** (que sí hace fallar el job por completo sin LLM
conectado). Son decisiones de producto distintas, ambas ya tomadas y documentadas ANTES de
este WP (`edecan_voice.tenant`, `edecan_creative.podcast`, `docs/voz-telefonia.md`,
`docs/credenciales.md`): sin LLM el job no puede hacer NADA útil (no hay alternativa
razonable a fallar); sin TTS propio, el podcast igual se sintetiza como audio de silencio
(`StubTTS`) y se entrega un archivo real que el tenant puede reemplazar después — la MISMA
filosofía que ya aplican `sintetizar_voz` (tool de chat) y `POST /v1/voice/speak`. Cambiar
esto a un "fail-hard" habría sido una regresión de producto no pedida explícitamente en
ningún otro documento del repo, revertiendo una decisión ya tomada y probada en 3 paquetes de
trabajo distintos (WP-V5-10, WP-V5-11, WP-V6-04) — no se tocó. `docs/voz-telefonia.md`
(sección nueva "Podcasts", ver más abajo) deja esta distinción explícita para que una
auditoría futura no vuelva a confundir los dos criterios.

**Ningún archivo de producción se modificó por este barrido** — los tres gates (ElevenLabs
por-tenant, Polly `EDECAN_LOCAL_MODE`, stub sin credencial) ya estaban correctos.

---

## BARRIDO C — evidencia legal/cumplimiento vs. rollback de sesión

### Verificación del fix ya existente (`crear_clon_voz`)

`HOTFIXES_PENDIENTES.md` documenta el fix de `crear_clon_voz` (dos `await session.commit()`
explícitos, uno antes de cada `raise HTTPException` posterior al `INSERT INTO voice_consents`)
como el ÚNICO caso de este patrón que ya tenía solución en este router. Confirmado INTACTO,
solo lectura (líneas 582 y 608 de `voz_avanzada.py`, mismos dos sitios, mismo comentario
explicando por qué):

- Sin ElevenLabs conectado → `commit()` → `400` (evidencia de `voice_consents` sobrevive).
- ElevenLabs rechaza el clon (`VoiceCloningError`) → `commit()` → `400` (evidencia sobrevive).
- Camino feliz → sin commit explícito (nada lanza después, el commit implícito de fin de
  request alcanza).

`apps/api/tests/test_voz_avanzada.py` (`fake_session.commits`) sigue verificando los tres
casos — no se tocó nada de este archivo para este barrido.

### Barrido de TODOS los demás sitios de escritura del router

Se listaron los 12 sitios de escritura de `voz_avanzada.py` (`_subir_archivo`/`repo.create_file`
×2, `_insertar_voice_consent`, `_actualizar_provider_voice_id`, `_revocar_voice_consent`,
`repo.add_audit_log` ×2, `_insertar_podcast`, `enqueue`) y se verificó, para cada uno, si hay
un `raise` alcanzable DESPUÉS en el mismo camino de código:

| Sitio | ¿Raise alcanzable después? | Veredicto |
|---|---|---|
| `GET /voces`: `cloning.listar_voces` falla → `502` | No hay ninguna escritura en este endpoint (solo lecturas) | N/A, nada que perder |
| `POST /clones`: validaciones tempranas (`nombre`/`attestation`/`consentimiento`/`muestras`) | Los 4 `raise HTTPException(400)` ocurren ANTES de `_subir_archivo`/`_insertar_voice_consent` | Seguro por orden, nada que perder |
| `POST /clones`: `_actualizar_provider_voice_id` → `repo.add_audit_log` → `return` | Nada lanza después del `UPDATE` en el camino feliz | Seguro — ya cubierto por `test_crear_clon_happy_path_returns_201_con_fila_attested` (`commits == 0`) |
| `DELETE /clones/{id}`: `_revocar_voice_consent` (UPDATE) → `if actualizado is None: raise 404` | Solo alcanzable si la fila desaparece ENTRE el `SELECT` previo y este `UPDATE` en la MISMA transacción — inalcanzable hoy (`voice_consents` nunca se borra en ningún camino del repo, confirmado con `grep -rn "DELETE FROM voice_consents"` sin resultados); y aunque ocurriera, un `UPDATE ... RETURNING` que afecta 0 filas no escribe nada que perder | Seguro, defensivo (mismo criterio que los `RuntimeError` "defensivo" ya documentados en `_insertar_voice_consent`/`_actualizar_provider_voice_id`) |
| `DELETE /clones/{id}`: `cloning.borrar_clon` (ElevenLabs) | Envuelto en `try/except VoiceCloningError` que NUNCA re-lanza (best-effort, ya documentado) | Seguro |
| `POST /podcasts`: `_insertar_podcast` → `enqueue(...)` | Si `enqueue()` lanza, no hay `try/except` — la excepción no manejada revienta la request y el rollback automático se lleva la fila de `podcasts` completa | **Diseño deliberado, no un bug** (ver docstring del router, sección "Podcasts": `podcasts` es un registro operativo, no evidencia legal — "todo o nada" es el comportamiento correcto, nunca deja una fila `'pending'` huérfana que ningún job vaya a procesar) |
| `GET /podcasts`, `GET /podcasts/{id}` | Solo lecturas | N/A |

**"Efectos" (`generar_efecto_sonido`) — no vive en el router, verificado igual por
completitud**: el encargo menciona "efectos" junto a podcasts/clones, pero
`GenerarEfectoSonidoTool`/`CrearPodcastTool` (`packages/creative/edecan_creative/tools.py`,
paquete hermano — SOLO LECTURA, fuera de las rutas editables de este WP) son tools de chat,
no endpoints de `voz_avanzada.py`. Se revisaron igual: `GenerarEfectoSonidoTool.run()` sube a
S3 + inserta `files` (`self._uploader`) como su ÚLTIMA operación de escritura antes de
construir el `ToolResult` de retorno — nada lanza después. `CrearPodcastTool.run()` solo hace
`enqueue(...)`, sin escritura propia previa que proteger. Y aunque hubiera un `raise`
alcanzable después de una escritura en cualquiera de las dos, `edecan_core.agent.
Agent._run_turn`/`edecan_api.routers.conversations._stream_approved_confirmation` envuelven
CADA `tool.run(ctx, args)` en su propio `try/except Exception` que nunca deja escapar la
excepción hacia el límite de la transacción HTTP (mismo mecanismo ya verificado para
`LanzarCampanaTool` en `docs/cumplimiento/barrido-evidencia-v6.md`) — así que el commit
implícito de fin de turno persiste cualquier escritura ya hecha, sin importar qué pase
después. Sin hallazgos, sin cambios.

**Conclusión de esta parte**: el router no necesitaba ningún fix nuevo — los dos sitios ya
corregidos siguen intactos, y el resto o bien no tiene nada que perder, o bien su diseño
"todo o nada" ya es la decisión correcta (documentada explícitamente desde que WP-V6-04
construyó el vertical de podcasts).

### Barrido del handler (`generate_podcast.py`) — hallazgo real

**La pregunta del encargo**: "si el TTS falla a mitad, ¿la evidencia/estado ya comiteado
sobrevive? ¿un reintento duplica trabajo o audio huérfano en el object store?"

- **`podcasts.status`/`error` (el "estado") SIEMPRE sobrevive**: `_marcar_running` y
  `_marcar_error` corren cada uno en su PROPIA sesión corta (`async with
  deps.session_factory(None) as session:`), que comitea al cerrarse ANTES de que el trabajo
  pesado (síntesis/ensamblado/subida) empiece o después de que cualquier excepción se
  atrape — nunca quedan atados a la transacción larga del trabajo pesado. Esto ya era
  correcto antes de este WP (ver el docstring original del módulo, sección "`status='running'`
  se comitea ANTES de empezar la síntesis").
- **Hallazgo real, corregido en este WP**: el `put_object` a S3 y la escritura de Postgres que
  lo sigue (`INSERT INTO files` + `_marcar_done`) viven en la MISMA transacción de Postgres,
  pero son DOS sistemas sin commit atómico conjunto — si `put_object` tiene éxito y la
  escritura de Postgres que sigue falla (p. ej. un blip de conectividad justo en ese
  instante), el objeto queda en S3 sin ninguna fila `files` que lo referencie: **audio
  huérfano**, exactamente la pregunta del encargo. Además, un reintento del job (mismo
  `podcast_id`, ahora en `'error'`) vuelve a sintetizar TODO desde cero y sube un archivo
  NUEVO — el objeto huérfano original nunca se limpia solo.

**Fix**: `apps/worker/edecan_worker/handlers/generate_podcast.py::handle` ahora rastrea
`s3_subido` (bandera local, `False` hasta que `put_object` termina con éxito). Si una
excepción llega DESPUÉS de eso, el bloque `except` intenta un best-effort de
`deps.s3.delete_object(Bucket=bucket, Key=s3_key)` ANTES de marcar `podcasts.status='error'`
— mismo criterio best-effort que `voz_avanzada.py::revocar_clon_voz`/
`edecan_voice.cloning.borrar_clon` (un fallo del propio borrado NUNCA enmascara ni bloquea la
excepción original: se captura aparte, se registra con `logger.warning`, y el `_marcar_error`/
`raise` de la excepción real sigue su curso exactamente igual que antes). El mensaje que
termina en `podcasts.error` sigue siendo el de la excepción ORIGINAL en los dos casos (borrado
exitoso o fallido).

**Limitación conocida, aceptada, NO resuelta por este fix** (documentada en el docstring del
handler y en `docs/voz-telefonia.md`): el best-effort reduce pero no elimina el problema —
si el propio `delete_object` también falla (S3 caído en el peor momento), el objeto queda
huérfano de verdad hasta una limpieza manual; y de todas formas un reintento del job
resintetiza TODO el guion desde cero (gasto real si el proveedor TTS es de pago), porque no
hay una clave de idempotencia por segmento — mismo criterio "el job reprocesa completo en cada
intento" que el resto del despachador (`ingest_file`, `memory_consolidate`, etc.,
`ARCHITECTURE.md` §10.11). Diseñar un mecanismo de idempotencia por segmento sería un cambio
de arquitectura mucho más grande que el alcance quirúrgico de este WP — se deja documentado
como candidato para un WP de seguimiento, no como algo resuelto aquí.

**Tests nuevos** (`apps/worker/tests/test_generate_podcast.py`):

- `test_generate_podcast_fallo_db_tras_subir_a_s3_borra_el_objeto_huerfano` — simula (con
  `_FakeSession(fallar_en_llamada=2)`, nuevo parámetro del fake local) que el `INSERT INTO
  files` revienta DESPUÉS de un `put_object` exitoso; confirma `delete_object` llamado con el
  mismo `Bucket`/`Key` de la subida, el objeto ya no está en el fake, y `podcasts.error`
  contiene el mensaje original.
- `test_generate_podcast_fallo_al_borrar_huerfano_no_enmascara_el_error_original` — el propio
  `delete_object` TAMBIÉN falla (`_FakeS3ConPut(fallar_delete=True)`, nuevo parámetro); el
  objeto "sigue existiendo" en el fake, pero `podcasts.error`/la excepción re-lanzada siguen
  siendo las ORIGINALES, nunca las del fallo de limpieza.
- Assertion nueva en el test preexistente
  `test_generate_podcast_fallo_elevenlabs_marca_error_y_relanza_nunca_cae_a_stub`: `s3.
  delete_calls == []` — si el fallo ocurre ANTES de subir nada (`s3_subido` nunca se activa),
  el best-effort de limpieza ni siquiera se intenta.

**Verificado**: `uv run --all-packages pytest -q apps/worker/tests/test_generate_podcast.py -m
"not integration"` → **20 passed** (18 preexistentes + 2 nuevos). `uv run --all-packages ruff
check apps/worker/edecan_worker/handlers/generate_podcast.py apps/worker/tests/
test_generate_podcast.py` → limpio.

---

## Documentación sincronizada

`docs/voz-telefonia.md` ganó una sección nueva **"Podcasts (mismo router que voces/clones,
flag distinto)"** (entre "Disclaimers legales de uso de voz ajena" y "Telefonía (premium)") —
el documento no mencionaba podcasts en absoluto antes de este WP (el detalle completo ya vive
en [`creatividad.md`](../creatividad.md), sin duplicarlo aquí): explica el flag distinto
(`tools.podcast`, nunca `voice.cloning`), el matiz bring-your-own (stub en vez de fail-hard,
con la aclaración explícita de por qué es distinto de `TenantLLMNotConnectedError`), que
`podcasts` es un registro operativo (no evidencia legal, "todo o nada" es correcto), y la
limitación conocida del audio huérfano/reintento que gasta síntesis de nuevo. Las secciones
"Voz web (núcleo)" y "Voces y clonación autorizada" (clonación) se releyeron completas contra
el código real verificado en este WP — sin cambios, ya estaban correctas. La sección
"Interrupciones naturales (beta)" (Media Streams) **NO se tocó**, como exige el encargo — esa
la audita WP-V7-06/WP-V7-12.

---

## Verificación final

```
uv run --all-packages pytest -q packages/voice apps/api/tests/test_voz_avanzada.py \
  apps/api/tests/test_v7_sweep_voz.py apps/worker/tests/test_generate_podcast.py \
  -m "not integration"
```

→ **176 passed, 0 failed** (comando pinned exacto del encargo).

`uv run --all-packages pytest -q apps/api/tests apps/worker/tests -m "not integration"`
(suite completa de los dos deployables tocados, para descartar regresiones fuera del comando
pinned — incluye `test_podcasts_router.py` y `test_v6_sweep_flags.py`, ninguno de los dos
tocado por este WP) → **1302 passed, 19 deselected, 0 failed**.

`uv run --all-packages ruff check` sobre los 6 archivos de producción/test tocados
(`apps/api/edecan_api/routers/voz_avanzada.py` sin cambios de código —solo se leyó—,
`apps/api/tests/test_v7_sweep_voz.py`, `apps/worker/edecan_worker/handlers/
generate_podcast.py`, `apps/worker/tests/test_generate_podcast.py`, y los archivos de
`packages/voice/` solo leídos) → limpio. `uv run --all-packages ruff format --check` señala
diferencias de formato PRE-EXISTENTES (anteriores a este WP) en varios archivos del repo,
incluidos dos que este WP sí tocó (`generate_podcast.py`, `test_generate_podcast.py`) pero
únicamente en líneas que este WP NO escribió (confirmado con `ruff format --diff`: los bloques
señalados son código/tests preexistentes, ninguna línea nueva de este WP aparece en el diff) —
consistente con que `make lint` (`uv run --all-packages ruff check .`, ver `Makefile`) nunca
incluyó `ruff format --check`; no se corrigió por ser una deriva repo-wide fuera del alcance
quirúrgico de este WP (tocaría archivos de otros paquetes de trabajo).

Verificación empírica de esquema (Postgres real + `alembic upgrade head`, ver BARRIDO D):
contenedor `edecan-v7-voz-pg` levantado, usado y **bajado con `docker rm -f`** al terminar —
confirmado con `docker ps -a` que no quedó nada huérfano.

**Archivos escritos/tocados por este WP**:

- `apps/worker/edecan_worker/handlers/generate_podcast.py` — fix del audio huérfano en S3
  (BARRIDO C) + docstring actualizado.
- `apps/worker/tests/test_generate_podcast.py` — 2 tests nuevos + 1 assertion nueva + soporte
  de `fallar_en_llamada`/`delete_object`/`fallar_delete` en los fakes locales.
- `apps/api/tests/test_v7_sweep_voz.py` — archivo nuevo (BARRIDO B).
- `docs/voz-telefonia.md` — sección "Podcasts" nueva.
- `docs/cumplimiento/barrido-v7-voz.md` — este documento.

**Sin cambios** (verificados, no hizo falta tocarlos): `apps/api/edecan_api/routers/
voz_avanzada.py`, `packages/voice/edecan_voice/*.py`, `apps/api/tests/test_voz_avanzada.py`,
`packages/voice/tests/*.py`.

**Hallazgos delegados (archivos ajenos a este WP)**: ninguno. Se leyó `edecan_creative.podcast`
(BARRIDO A/D, fuera de las rutas editables) y no se encontró ningún problema — solo
confirmación de que el bring-your-own y el esquema que consume son correctos.
