# Barrido v7 de `docs/api.md` — sincronización bidireccional contra los routers reales (fase v7)

Este documento registra el barrido **programático** de `docs/api.md` (987
líneas antes de este WP) contra los routers HTTP/WebSocket REALMENTE
montados por `edecan_api.main.create_app()` hoy — motivado porque el propio
`docs/api.md` ya se desincronizó dos veces antes (v4: fallback tenant→
plataforma que ya no existía, corregido en v4/HOTFIXES; v6: `docs/api.md`
§13.g documentó explícitamente que "ningún WP lo tocó en v4" y quedó como
deuda aceptada hasta que fase v6 hizo la primera pasada de esa deuda).

## Método usado (programático, no a ojo)

1. **Volcado de rutas reales**: script en el scratchpad de la sesión (no
   vive en el repo, según instrucción) que hace
   `from edecan_api.main import create_app` (mismo factory real que usa
   producción, incluyendo el montaje defensivo de `V2_ROUTER_NAMES` ...
   `V6_ROUTER_NAMES` y el guard `importlib.util.find_spec("edecan_premium")`)
   y vuelca **todas** las rutas montadas, ejecutado siempre como
   `uv run --all-packages python <script>` (nunca `uv run` a secas, que poda
   el workspace — `docs/seguridad-modelo-amenazas.md`). Dos fuentes de verdad
   complementarias, porque esta versión de FastAPI/Starlette instalada en el
   workspace (`fastapi==0.139.0`/`starlette==1.3.1`) envuelve cada
   `include_router()` en un `fastapi.routing._IncludedRouter` perezoso que ya
   no expone `.path`/`.methods` planos en `app.routes`:
   - `app.openapi()["paths"]` para **todas las rutas HTTP** (GET/POST/PUT/
     PATCH/DELETE) — la propia fuente de verdad de FastAPI, independiente de
     la representación interna de rutas.
   - Recorrido recursivo manual de `app.routes` (expandiendo
     `_IncludedRouter.original_router.routes` y cualquier `.routes` anidado)
     para capturar **rutas WebSocket**, que `openapi()` no expone.
   - Settings: `Settings()` por defecto (`ENV=dev`, no hay `.env` real en el
     repo) — nunca credenciales reales; no hizo falta el patrón de
     `apps/api/tests/conftest.py` (`test_settings`) porque `create_app()` no
     abre conexión real a Postgres al construirse (solo por request, vía
     `Depends(get_tenant_session)`).
2. **Diff bidireccional mecánico**: segundo script que extrae de
   `docs/api.md` todo patrón `` `METHOD /v1/...` `` (o `/healthz`) dentro de
   backticks, normaliza nombres de parámetro de path (`{id}`, `{mission_id}`,
   etc. → `{}`) y lo compara contra el volcado real. Usado como red de
   seguridad ADICIONAL a la lectura manual sección por sección (no como
   único método): varias secciones de `docs/api.md` usan atajos de prosa
   deliberados que un regex simple no resuelve solo (`` `PUT` / `DELETE
   /v1/x/credentials` ``, `` `GET|POST /v1/rrhh/empleados` ``, rutas
   relativas dentro de listas entre paréntesis como en "Rutas v4/v5") — cada
   resultado del diff mecánico se triangeó a mano contra el texto real antes
   de decidir si era un hallazgo o ruido de formato.
3. **Verificación de flags/status codes**: no hay un registro central de
   "qué flag gatea qué endpoint" en el código (cada router lo resuelve
   inline) — se hizo `grep -rn "\.flags\.get(" apps/api/edecan_api/routers/*.py`
   (33 ocurrencias, todas revisadas una por una) más lectura directa de cada
   router con un `HTTPException`/`status_code=` no trivial, comparado
   endpoint por endpoint contra lo que dice `docs/api.md` y contra las
   tablas de flags pinned en `ARCHITECTURE.md` §10.13/§13.c/§14.c/§15.c.
4. **Revisión de fallback-a-plataforma prohibido**: `grep -n "plataforma\|incluidos\|incluido"` sobre `docs/api.md` completo — cada
   ocurrencia leída en contexto.

## Conteo de endpoints: antes vs. después

| | Endpoints HTTP (method+path) | Paths HTTP únicos | WebSocket | Total |
|---|---|---|---|---|
| **Reales (código)** | 191 | 145 | 2 | 193 |
| **Documentados ANTES de este WP** | 189 | — | 1 (`/v1/companion/ws`) | 190 |
| **Documentados DESPUÉS de este WP** | 191 | — | 2 | 193 |

**Gap cerrado: 3 endpoints reales que no aparecían documentados en ningún
lugar de `docs/api.md`** (confirmado por conteo manual sección-por-sección Y
por el diff mecánico, ambos coincidieron exactamente en estos 3):

1. `GET /v1/missions/{id}/detalle` (fase v6, observabilidad enriquecida de
   misiones — `response_model=MissionDetalleOut`).
2. `POST /v1/remote/sessions/{id}/input` (fase v4, "fase 2" de control
   remoto real — input de teclado/mouse).
3. `WS /v1/twilio/media` (Media Streams, interrupciones naturales en
   llamadas — `ARCHITECTURE.md` §15.h).

Tras las correcciones, la segunda corrida del diff mecánico (ver "Corridas
del script" abajo) da **0 endpoints documentados que no existan en el código
real** y **0 endpoints reales sin ninguna mención en la doc** (los 20
resultados que el diff mecánico todavía reporta en el sentido "existe en la
app real pero no matchea la extracción" son ruido de formato ya verificado a
mano — ver el bloque "Falsos positivos del diff mecánico, verificados uno
por uno" más abajo — no gaps reales).

## Correcciones aplicadas a `docs/api.md`

Todas las correcciones alinearon la DOC al CÓDIGO (nunca al revés), tal como
exige el enunciado del WP. Ninguna requirió tocar ningún router.

1. **`GET /v1/connectors` — conteo y contenido desactualizados.** La doc
   decía "hasta 9 entradas en total" y el ejemplo JSON no incluía
   `whatsapp` — pero `connectors.py::list_connectors` (línea 351) concatena
   CUATRO grupos, no tres, desde que fase v3 agregó `whatsapp_entry`: son
   10 entradas reales. Corregido el conteo, la prosa y el ejemplo JSON.
2. **`POST /v1/reuniones` — valor de `status` inicial incorrecto.** La doc
   decía `status="queued"`; el código real (`reuniones.py`, línea 272)
   inserta `'pending'` — el mismo valor que exige el `CHECK` real de la
   migración `0008_v6_expansion.py` (`status IN ('pending', 'running',
   'done', 'error')`; `'queued'` la revienta). Este es el MISMO patrón de
   bug que ya afectó a `docs/api.md` en v4 (fallback stale) y a
   `packages/meetings/README.md` con el mismo esquema — el propio
   `reuniones.py` documenta el historial en su docstring, citando
   explícitamente a `docs/api.md` como ejemplo de esa clase de bug. Cerrado
   aquí. También se agregó una nota aclarando que `decisiones`/`acciones`/
   `temas` no son columnas propias sino que se leen y aplanan desde el único
   blob `minutos JSONB` — para que un futuro lector no asuma columnas que no
   existen (la forma de la respuesta HTTP no cambia, solo el mapeo interno).
3. **`/v1/vehiculos` — afirmación de flag incorrecta.** La doc decía "Sin
   flag de plan adicional (más allá de la sesión autenticada)". Verificado
   contra el código: los 6 endpoints de `vehiculos.py` pasan por
   `Depends(require_vehicles_flag)`, que exige `FLAG_TOOLS_VEHICLES`
   (`"tools.vehicles"`) sin excepción. Corregido con el mínimo cambio
   posible (una sola cláusula) — se respetó al pie de la letra la
   instrucción del WP de "documentar tal como está hoy... NO expandirlo ni
   recortarlo": no se agregó tabla de endpoints ni se tocó el resto del
   párrafo/enlace a `vehiculos.md`.
4. **`/v1/remote` — sección con contenido stale y un endpoint entero sin
   documentar.** La doc afirmaba "Prototipo P1 solo-vista: ningún endpoint
   de este router inyecta teclado/mouse — eso es P2, diseñado (no
   implementado)". Falso desde fase v4 ("fase 2",
   `ARCHITECTURE.md` §13.c): `POST /v1/remote/sessions` ahora acepta
   `kind: "view"|"control"` y existe `POST /v1/remote/sessions/{id}/input`
   (gatea `companion.remote_input`, además de `companion.remote_view`).
   `control-remoto.md` (dueño de otro WP, no tocado) ya documentaba esto en
   detalle — `docs/api.md` simplemente nunca se actualizó. Corregido: título
   de sección, flag, el bullet de `POST /sessions` (con `kind`) y un bullet
   nuevo completo para `POST /sessions/{id}/input` con sus 6 códigos de
   estado reales.
5. **`GET /v1/voz/voces` — flag ambiguo por posición.** El título de la
   sección dice "flag `voice.cloning`" pero ese endpoint puntual gatea
   `voice.web` (verificado en `voz_avanzada.py`, `_require_voice_web`) —
   mismo criterio que la tool `listar_voces` (`ARCHITECTURE.md` §14.e: listar/
   sintetizar no clona nada). Un lector podía asumir que el flag del título
   aplicaba a los 4 bullets de la sección; ahora queda explícito que es la
   única excepción.
6. **`POST /v1/files` — cuota de almacenamiento no documentada.** El código
   (`files.py::_check_storage_quota`) responde `429` si el archivo entrante
   superaría `limits.storage_mb`; la doc no lo mencionaba en absoluto.
   Agregado.
7. **`POST /v1/conversations/{id}/messages` — cuota de mensajes no
   documentada.** El código (`conversations.py::_check_message_quota`)
   responde `429` si se agotó `limits.messages_per_day` — y lo hace ANTES de
   construir el `StreamingResponse`, así que es un `429` HTTP normal (JSON),
   nunca un evento `error` dentro del stream SSE. Ninguno de los dos hechos
   estaba en la doc. Agregado.
8. **`WS /v1/twilio/media` — endpoint completo sin ninguna mención.** No
   aparecía en ningún lugar de `docs/api.md`. Agregada una fila en la tabla
   de rutas de la extensión comercial externa `edecan_premium`, más una subsección corta con el gate real
   (`TWILIO_MEDIA_STREAMS_ENABLED`) y la limitación conocida (falta
   `app.state.settings` en `create_app()`, ya documentada en
   `ARCHITECTURE.md` §15.h y `docs/voz-telefonia.md` — ver "Hallazgos NO
   delegados" abajo, no es un hallazgo nuevo de este WP), con enlace al
   detalle completo en `voz-telefonia.md`.

### Verificación del punto (4) del enunciado — fallback a plataforma prohibido

`grep -n "plataforma\|incluido"` sobre el `docs/api.md` completo (antes y
después de las correcciones): **cero promesas de fallback a credenciales de
plataforma y cero menciones de "minutos/números incluidos"**. Todas las
ocurrencias de "plataforma" describen correctamente lo contrario (p. ej.
"nunca cae a un proveedor de voz de plataforma", "el `client_id` de la
plataforma" refiriéndose al registro legítimo de la app OAuth) o son
coincidencias de palabra sin relación (p. ej. "las 4 plataformas conectadas"
= canales de mensajería). Sin cambios necesarios en este punto — ya estaba
correcto desde el fix de v4.

## Hallazgos de código — NO se delegan (verificado, no hay ninguno nuevo)

El enunciado pedía registrar aquí cualquier cosa que pareciera un BUG del
código (flag equivocado, status code que debería ser otro) encontrado
durante el diff, sin tocar el router. **Resultado: no se encontró ningún bug
de código nuevo.** Los 8 hallazgos de arriba son, sin excepción, casos de
`docs/api.md` desactualizado respecto a un código que ya está bien — no al
revés. Un caso limítrofe, ya rastreado en otro lado, se aclara para que no
se interprete como una delegación nueva de este WP:

- **`WS /v1/twilio/media` inalcanzable en producción** (falta
  `app.state.settings = settings` en `edecan_api/main.py::create_app()`,
  dentro del bloque `if importlib.util.find_spec("edecan_premium")`) — esto
  **ya está documentado como "Limitación conocida — pendiente de un WP de
  seguimiento"** tanto en `ARCHITECTURE.md` §15.h ("brecha conocida, sin
  dueño asignado todavía") como en `docs/voz-telefonia.md` (sección
  "Interrupciones naturales (beta)"). Este WP solo agregó la referencia
  cruzada correspondiente en `docs/api.md` (punto 8 de arriba); NO es un
  hallazgo nuevo y NO se abre una entrada nueva de seguimiento para evitar
  duplicar la ya existente.

## Corridas del script (verificación de concurrencia)

Por instrucción del WP ("otros WPs de v7 pueden ajustar flags/status en
paralelo"), el volcado de rutas + el diff se corrieron DOS veces, con todas
las ediciones de `docs/api.md` ya aplicadas entre ambas corridas:

- **Corrida 1** (antes de editar `docs/api.md`): estableció el inventario
  real de 191 endpoints HTTP + 2 WebSocket, y el diff manual inicial contra
  la doc original.
- **Corrida 2 — final** (`2026-07-09 07:27:14 -05`, con `docs/api.md` ya
  100% editado): `diff` byte-a-byte contra el volcado de rutas de la corrida
  1 → **sin cambios** (ningún WP paralelo movió el `path`/`method` de ningún
  endpoint entre ambas corridas). Además se re-verificó a mano, con `grep`
  directo sobre el código fuente en ese mismo momento, que las 9 piezas de
  evidencia puntuales usadas para las correcciones seguían siendo ciertas:
  el flag `FLAG_TOOLS_VEHICLES` en `vehiculos.py`, el literal `'pending'` en
  `reuniones.py`, `FLAG_REMOTE_INPUT` en `remote.py` (2 usos), la ruta
  `/{mission_id}/detalle` en `missions.py`, `whatsapp_entry` en
  `connectors.py::list_connectors`, `_require_voice_web` en
  `GET /voces` de `voz_avanzada.py`, la ruta WS `/media` en
  `media_streams.py`, el `429` de `files.py`, y el `429` pre-stream de
  `conversations.py`. Las 9 confirmadas sin cambios. El diff mecánico
  final (script #2) se corrió también sobre esta segunda captura de rutas:
  **0 endpoints documentados inexistentes, 0 endpoints reales completamente
  sin mención** (los 20 "solo en real" restantes son ruido de formato de
  regex, listado abajo, ya verificado a mano contra el texto real de la
  doc).

### Falsos positivos del diff mecánico, verificados uno por uno

El segundo script (regex sobre backticks) no entiende tres convenciones de
prosa que `docs/api.md` ya usaba ANTES de este WP para las secciones tipo
tabla-resumen de v4/v5 ("Rutas v4/v5 (montaje defensivo)"):
`` `PUT` / `DELETE /v1/x/credentials` `` (método y path en backticks
separados), `` `GET|POST /v1/x` `` (dos métodos pegados con `|` antes de un
solo path) y rutas relativas dentro de una lista entre paréntesis (p. ej.
"`GET /rastreo/{numero}`" sin repetir `/v1/viajes` porque el prefijo ya está
en el encabezado `###` de la sección). Los 20 casos reportados por el diff
mecánico final caen en una de estas tres categorías — confirmado leyendo el
texto real de `docs/api.md` en cada caso (`/v1/vehiculos`, `/v1/viajes`,
`/v1/rrhh/*`, `/v1/ads/credentials`, `/v1/devices/push*`): todos están
documentados, solo que con una convención de prosa que el regex no separa
en tokens `(método, path)` limpios.

## Archivos tocados por este WP

- `docs/api.md` — 9 correcciones (ver arriba). De 987 a 1002 líneas.
- `docs/cumplimiento/barrido-v7-apimd.md` — este documento.

Ningún router, test, `apps/web`, ni otro `docs/*.md` se tocó.
