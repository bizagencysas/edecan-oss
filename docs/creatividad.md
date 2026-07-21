# Creatividad — imágenes, documentos de oficina, podcasts y efectos de sonido

`edecan_creative` (`packages/creative/`) le da al agente seis herramientas de generación:
una imagen, tres formatos de documento de oficina (Word, PowerPoint, PDF), y (desde v5,
`ARCHITECTURE.md` §14, fase v5) un podcast completo y un efecto de sonido — cada archivo
guardado como un archivo privado del tenant (mismo mecanismo que una subida manual por
`POST /v1/files`, ver [`api.md`](./api.md)). Las cuatro primeras corresponden a la
fase v2 definida en `ARCHITECTURE.md` §11; las dos de audio son de v5 (ver la sección
["Podcasts y efectos de sonido"](#podcasts-y-efectos-de-sonido) más abajo). La lista de
herramientas de `ARCHITECTURE.md` §10.14 es solo la de v1/`edecan_toolkit`; las herramientas
nuevas de v2/v5 se fijan en `ARCHITECTURE.md` §11/§14 respectivamente, no ahí.

> **Estado de integración:** el paquete forma parte del workspace raíz y de las dependencias
> de la API. Sus herramientas se descubren automáticamente por el entry point `edecan.tools`.
> Para solicitudes de varios formatos desde una sola frase, la ruta preferida es además
> [`crear_artefactos`](./creador-universal.md), que reutiliza estos renderizadores y añade un
> manifest común con evidencia por archivo.

## Las 6 herramientas

| Herramienta | Genera | Flag de plan | `dangerous` |
|---|---|---|---|
| `generar_imagen` | `.png` vía un `ImageProvider` intercambiable | `tools.images` | No |
| `crear_documento` | `.docx` (título + secciones con encabezado/párrafos) | — | No |
| `crear_presentacion` | `.pptx` (portada + una diapositiva de título+contenido por elemento) | — | No |
| `crear_pdf` | `.pdf` (portada + cuerpo en párrafos) | — | No |
| `crear_podcast` | `.wav`/`.mp3` (guion → job asíncrono `generate_podcast`) | `tools.podcast` | No |
| `generar_efecto_sonido` | `.wav`/`.mp3` (efecto de sonido, síncrono) | `tools.podcast` | No |

Ninguna es `dangerous`: no publican nada en ningún sitio ni actúan sobre una cuenta externa
del usuario, solo generan un archivo y lo guardan como privado del tenant. Las seis
devuelven `data` con al menos `file_id`/`filename` (recuperable después por `GET
/v1/files/{id}`; `crear_podcast` es la excepción — ver más abajo, encola un job y devuelve
`{"titulo", "segmentos"}` en su lugar, el `file_id` no existe todavía en ese momento) y un
`content` en español listo para mostrar en el chat, p. ej.:

> Creé la presentación «Estrategia 2027» con 2 diapositiva(s) de contenido (más la
> portada), guardada como «estrategia-2027.pptx».

Solo `generar_imagen` está gateada por un flag de plan (`tools.images`, `docs/roadmap.md` — activo en `free_selfhost`, `hosted_pro` y `hosted_business`; **no** en
`hosted_basic`); las tres herramientas de documentos no requieren ningún flag porque no
tienen costo variable de proveedor externo (a diferencia de una imagen generada con un
proveedor real de pago).

## Cómo se guardan los archivos

Cada herramienta arma el archivo **en memoria** (nunca toca disco) y lo sube con el mismo
layout que una subida manual (`ARCHITECTURE.md` §10.14):

```
s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}
```

...e inserta la fila correspondiente en la tabla `files` (`ARCHITECTURE.md` §10.3). A
diferencia de una subida real del usuario —que nace `status="uploaded"` y pasa por el job
asíncrono `ingest_file` para extraer texto e indexarlo en `file_chunks`—, un archivo
generado por `edecan_creative` nace directamente `status="ready"`: ya está completo, no hay
nada que extraer de forma asíncrona. Sigue siendo consultable con `GET /v1/files` / `GET
/v1/files/{id}`, y cuenta para el `limits.storage_mb` del plan igual que cualquier otro
archivo (el chequeo de cuota vive en `POST /v1/files`, no en estas tools — ver
`packages/creative/README.md`, sección "Decisiones de implementación", para el porqué de no
duplicar esa medición aquí).

## Proveedores de imagen

`generar_imagen` habla con un `ImageProvider` intercambiable
(`edecan_creative.providers.get_image_provider(settings)`), seleccionado con la variable
`IMAGES_PROVIDER` (`docs/roadmap.md`):

| `IMAGES_PROVIDER` | Clase | Requiere | Notas |
|---|---|---|---|
| `stub` (default) | `StubImageProvider` | — | 100% offline y determinista: el color de fondo sale de `sha256(prompt)` (mismo prompt → mismos bytes PNG siempre) y el prompt se dibuja como texto envuelto con la fuente bitmap por defecto de Pillow. No produce fotos realistas — es el modo gratis para desarrollo, self-host sin presupuesto de imágenes, y demos. |
| `openai_compat` | `OpenAICompatImagesProvider` | `IMAGES_BASE_URL`, `IMAGES_API_KEY`, `IMAGES_MODEL` | `POST {IMAGES_BASE_URL}/images/generations` con `Authorization: Bearer {IMAGES_API_KEY}` y cuerpo `{model: IMAGES_MODEL, prompt, size, response_format: "b64_json"}`; decodifica el `b64_json` de la respuesta. Sirve cualquier endpoint que replique ese contrato (OpenAI Images, o un proxy compatible) — **el costo de cada llamada corre por cuenta del tenant/operador que configura sus propias credenciales**, la plataforma no las subsidia (misma filosofía que `OPENAI_COMPAT_BASE_URL` de `edecan_llm`, ver [`configuracion.md`](./configuracion.md)). |

Si se pide `openai_compat` pero falta alguna de las tres variables, o se pide un valor no
reconocido, `get_image_provider` cae de forma segura a `StubImageProvider` con
`logging.warning` — nunca revienta la herramienta por falta de configuración (mismo patrón
de *fallback* que `edecan_voice.registry.get_stt/get_tts`, ver
[`voz-telefonia.md`](./voz-telefonia.md)).

Las cuatro variables (`IMAGES_PROVIDER`, `IMAGES_BASE_URL`, `IMAGES_API_KEY`,
`IMAGES_MODEL`) están pinned en `docs/roadmap.md` pero, como se explica arriba, todavía
no están declaradas como campos de `Settings` en `apps/api/edecan_api/config.py` (dueño:
fase v2) ni listadas en `.env.example`. Esto **no bloquea** el uso del proveedor `stub`
hoy: `get_image_provider` lee `settings` de forma defensiva
(`getattr(settings, "CAMPO", default)`, convención dura de `docs/roadmap.md`) y, al no
encontrar `IMAGES_PROVIDER`, usa `"stub"` por defecto sin ningún error. Para activar
`openai_compat` hoy mismo en una instancia self-host sin esperar a fase v2, basta con que
`Settings` termine exponiendo esos cuatro atributos (por variable de entorno del mismo
nombre, gracias a pydantic-settings) — el resto del paquete ya está listo.

## Límites de tamaño

Para que nadie (usuario o modelo) genere un archivo absurdo por accidente:

- Máximo **100 elementos** en cualquier lista de nivel superior: `secciones` (documento),
  `diapositivas` (presentación), `parrafos` (PDF).
- Máximo **200 elementos** en listas anidadas: párrafos por sección, bullets por
  diapositiva.
- Cualquier título/encabezado se acota a **300 caracteres**; cualquier otro texto
  (párrafo, bullet, prompt de imagen) se acota a **4000 caracteres**.

Un argumento fuera de rango se recorta silenciosamente (nunca revienta la herramienta) —
mismo criterio que `edecan_toolkit._util.clamp_int` usa para límites de resultados en el
resto del toolkit. El mensaje de confirmación siempre refleja el conteo real después de
recortar, así que si el modelo pidió 150 diapositivas el usuario ve "100 diapositiva(s)" en
la respuesta, no un número engañoso.

## Limitación conocida: caracteres en el PDF

`crear_pdf` usa fpdf2 con una fuente **core** (Helvetica), que solo soporta el charset
latin-1 (ISO-8859-1) — el mismo límite que tiene cualquier PDF clásico sin una fuente
TrueType embebida. Como esta plataforma tiene la regla dura de no hacer llamadas de red al
generar un archivo (no se descarga ninguna fuente `.ttf` en este paso), el texto se sanea
antes de escribirlo:

```python
texto.encode("latin-1", errors="replace").decode("latin-1")
```

En la práctica esto significa:

- **Español funciona sin pérdida**: vocales acentuadas, `ñ`, `¿`/`¡` y el resto de la
  puntuación española están dentro de latin-1.
- **Lo que se pierde**: emojis, comillas tipográficas curvas (`“` `”`), la raya larga
  (`—`), y cualquier otro carácter fuera de latin-1 se reemplazan por `'?'` en vez de
  hacer fallar la generación.

Si más adelante se necesita soporte Unicode completo en PDF (chino, árabe, emojis, etc.),
la vía correcta es empaquetar una fuente TrueType con soporte Unicode dentro del propio
repo (`pdf.add_font(...)` con un archivo `.ttf` versionado) — deliberadamente fuera de
alcance de esta primera versión para no añadir un asset binario ni una descarga de red.

`crear_documento` (Word) y `crear_presentacion` (PowerPoint) **no tienen esta limitación**:
sus formatos son XML con codificación UTF-8 nativa, así que aceptan cualquier texto Unicode
sin sanear nada.

## Cómo lo usa el agente

`edecan_creative:get_all_tools` se expone como entry point del grupo `edecan.tools`
(`[project.entry-points."edecan.tools"]` en `packages/creative/pyproject.toml`), el mismo
mecanismo de descubrimiento que usa `edecan_toolkit`
(`edecan_core.tools.registry.ToolRegistry.load_entry_points`, `ARCHITECTURE.md` §10.7): una
vez que el paquete esté instalado en el entorno de `apps/api`/`apps/worker` (ver la nota de
estado de integración arriba), el registro de herramientas las recoge automáticamente sin
tocar código central. `ToolRegistry.specs(flags)` filtra `generar_imagen` fuera de la lista
que ve el modelo cuando el plan del tenant no trae `tools.images` activo.

## Podcasts y efectos de sonido

Desde v5 (`ARCHITECTURE.md` §14), `edecan_creative.podcast` cubre podcasts y
efectos de audio — **100% bring-your-own**: el audio se sintetiza con el proveedor de voz (TTS)
del PROPIO tenant, nunca con una credencial de la plataforma. Desde v6 (fase v6) los
podcasts tienen, además de la tool de chat, un vertical HTTP/UI completo — ver
["Endpoints REST y UI web"](#endpoints-rest-y-ui-web-podcasts) más abajo.

### Cómo funciona

- **`crear_podcast`** (tool de chat) y **`POST /v1/voz/podcasts`** (endpoint REST, fase v6)
  son los DOS puntos de entrada para pedir un podcast — ambos terminan en el MISMO job
  `generate_podcast` (`edecan_schemas.JOB_TYPES`,
  `apps/worker/edecan_worker/handlers/generate_podcast.py`) y, desde fase v6, en la MISMA
  fila de la tabla `podcasts` (una sola ruta de estado sin importar el origen — ver
  "Endpoints REST y UI web" para el detalle). `crear_podcast` sigue siendo **asíncrona**:
  valida el guion (ver "Límites del guion" abajo) y encola el job — el mismo mecanismo
  `enqueue` que usa `delegar_mision` (`packages/agents`). El resultado no está listo al toque:
  el worker resuelve el TTS del tenant, sintetiza cada segmento del guion en orden y ensambla
  el audio final, que aparece como archivo nuevo en unos minutos. `crear_podcast` devuelve
  `data={"titulo", "segmentos"}` de inmediato (todavía no hay `file_id`: el archivo no existe
  hasta que el job termina) — para seguir el estado del podcast pedido por chat, la pestaña
  "Podcasts" de `/app/voz` también lo muestra (fase v6 crea la fila `podcasts`
  correspondiente al vuelo desde el worker, no desde la tool).
- **`generar_efecto_sonido`** es **síncrona**: resuelve el TTS del tenant, pide un efecto de
  sonido (sin voz — aplausos, lluvia, timbre, etc.) y sube el resultado en el mismo turno,
  igual que `generar_imagen`. Devuelve `data={"file_id", "filename", "es_stub"}`.

### Bring-your-own: TTS del propio tenant, fail-closed

Ambas herramientas resuelven la credencial de voz (TTS) que el tenant conectó en
`PUT /v1/credentials/voice/tts` (`TokenVault`, connector_key `"voice_tts"` — la MISMA
credencial que usa `POST /v1/voice/speak` para la voz web, ver
[`voz-telefonia.md`](./voz-telefonia.md)):

| Proveedor conectado | Resultado |
|---|---|
| ElevenLabs (`provider: "elevenlabs"`, `api_key`) | Audio real en **mp3**: `crear_podcast` llama `POST /v1/text-to-speech/{voice_id}` una vez por segmento (`voice_id` del segmento, o el de la credencial si no se especifica); `generar_efecto_sonido` llama `POST /v1/sound-generation` con la descripción. |
| Sin credencial conectada (o config incompleta/corrupta) | Audio de prueba en **wav**, 100% offline: `crear_podcast` sintetiza cada segmento como un WAV corto de silencio; `generar_efecto_sonido` genera un beep determinista de 1s (440 Hz) — nunca una llamada de red real, nunca gasta cuota de nadie. La respuesta lo deja explícito ("generé un tono de prueba, no un efecto real"). |

Igual que `generar_imagen`/`buscar_web`/la voz web: sin credencial propia conectada NUNCA se
cae a una clave de ElevenLabs de la plataforma — el patrón fail-closed de dos niveles
("tenant → stub", sin paso intermedio) es el mismo que corrigió el hallazgo más serio de todo
el proyecto (ver [`credenciales.md`](./credenciales.md) y
`docs/seguridad-modelo-amenazas.md`). Un fallo real hablando con ElevenLabs
cuando el tenant SÍ conectó una credencial (voz inválida, cuota agotada, etc.) se deja
propagar tal cual — nunca degrada en silencio a un stub.

**Consolidación con `edecan_voice` (fase v6):** hasta v5, la resolución de arriba vivía
DUPLICADA a propósito en `edecan_creative.podcast` (independencia de aterrizaje mientras
`packages/voice` se construía en paralelo, `ARCHITECTURE.md` §10.1). Ahora que ambos paquetes
están asentados, `packages/creative/pyproject.toml` declara `edecan-voice` como dependencia
real y `edecan_creative.podcast` delega la parte de red en las implementaciones de
`edecan_voice`: `resolver_config_tts_tenant` delega en
`edecan_voice.tenant.resolver_config_tts_del_tenant` (mismo contrato `dict|None`, verificado
línea por línea) y `generar_efecto`/la llamada a `sound-generation` delega en
`edecan_voice.cloning.generar_efecto` (traduciendo su `VoiceCloningError` a `SintesisError`
en el borde, para no cambiar el contrato público de este paquete). Los stubs offline
(silencio/beep) y el resto del comportamiento fail-closed de arriba no cambiaron — ver el
docstring de `edecan_creative.podcast` para el detalle completo de qué se consolidó y qué
sigue duplicado a propósito (la síntesis de voz de un segmento, `sintetizar_segmento`, todavía
no se tocó).

### Formatos

El formato final SIEMPRE depende del proveedor resuelto, nunca se fuerza una conversión:
`wav` sin credencial (stub), `mp3` con ElevenLabs. `crear_podcast` acepta un argumento
`formato` opcional (`"wav"`/`"mp3"`) puramente informativo — si no coincide con el proveedor
real del tenant, igual se genera en el formato real (nunca se transcodifica).

### Límites del guion (`crear_podcast`)

- Entre **1 y 30 segmentos**; cada uno con `orador` (opcional, por defecto "Orador N"),
  `texto` (obligatorio, hasta **2000 caracteres**) y `voice_id` (opcional, de ElevenLabs).
- El guion completo (todos los `texto` sumados) no puede superar **30 000 caracteres**.
- Un guion inválido nunca encola nada: `crear_podcast` devuelve el motivo exacto en español
  (`edecan_creative.podcast.validar_guion`/`GuionInvalidoError`), y el propio job vuelve a
  validar el payload antes de tocar el TTS o S3 (defensa en profundidad, mismo criterio que
  `generate_content` reverifica la conversación en vez de confiar ciegamente en el payload).

### Ensamblado y ffmpeg (dependencia del SISTEMA)

Un podcast de más de un segmento necesita unir los clips de audio en uno solo
(`edecan_creative.podcast.ensamblar_podcast`):

- **`wav`** (caso stub): se concatena **puro Python** con el módulo estándar `wave` — sin
  ffmpeg, sin dependencias nuevas.
- **`mp3`** (caso ElevenLabs): se concatena con el **ffmpeg del sistema** (concat demuxer,
  subproceso con lista de argumentos — nunca `shell=True`), calcando el patrón de
  `edecan_docanalysis.video` (extracción de frames de video, ver [`analista.md`](./analista.md)).
  Un solo segmento nunca requiere ffmpeg (se devuelve tal cual, sin ensamblar nada).

`ffmpeg` **no es una dependencia de Python**: como con `analizar_video`, en la app de
escritorio el cliente lo instala aparte.
`ffmpeg_disponible()` lo detecta en caliente (`shutil.which`, o `FFMPEG_PATH` si el entorno lo
fija) y, si falta, `crear_podcast`/el job devuelven un mensaje instructivo en vez de un
traceback:

```
Instálalo con 'brew install ffmpeg' (macOS) o 'apt install ffmpeg' (Linux/Debian/Ubuntu);
en Windows descarga el binario desde https://ffmpeg.org/download.html y agrégalo al PATH.
```

### Cómo se guarda el resultado

`crear_podcast`/`POST /v1/voz/podcasts` (vía el job) y `generar_efecto_sonido` guardan el
archivo final con el mismo layout S3 que el resto de `edecan_creative`
(`s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}`, `status="ready"` directo)
— el job inserta la fila `files` directamente desde el worker (no reusa
`edecan_creative._files.subir_archivo`, que está pensado para el contexto de una `Tool` con
`ctx.session`/`ctx.user_id`, no para un job; usa las mismas columnas exactas). El nombre de
archivo es `{título-en-slug}.{formato real}`. Desde fase v6, el job TAMBIÉN mantiene al día
la fila `podcasts` correspondiente (`status`/`file_id`/`error`) — ver la siguiente sección.

## Endpoints REST y UI web (podcasts)

Antes de v6, un podcast pedido por chat no tenía ninguna forma de consultarse desde la UI —
solo aparecía, con suerte, en `/app/archivos` cuando el job terminaba. fase v6 agrega el
vertical HTTP/UI completo, apoyado en una tabla nueva `podcasts` (`ARCHITECTURE.md` §15.b,
migración `0008_v6_expansion`, linchpin fase v6): `id, tenant_id, user_id, titulo, guion
jsonb (nullable), status, file_id, error, created_at, updated_at`, con `status ∈ {pending,
running, done, error}`.

### Endpoints (`prefix /v1/voz`, mismo router que voces/clones)

- **`POST /v1/voz/podcasts`** — `{titulo: str, guion: [{texto: str, voz?: str}]}`. Gate de
  flag `tools.podcast` (dependencia `_require_tools_podcast`, mismo patrón que
  `_require_voice_cloning` — el flag de la ACCIÓN, no el del grupo). Valida el guion con
  `edecan_creative.podcast.validar_guion` (las mismas reglas de "Límites del guion" de
  arriba), inserta la fila `podcasts` (`status="pending"`) y encola `generate_podcast` con
  `{"podcast_id": "<uuid>"}` — responde `201` con la fila creada (pinned en
  `ARCHITECTURE.md` §15.e).
- **`GET /v1/voz/podcasts`** — lista del tenant, orden `created_at` descendente.
- **`GET /v1/voz/podcasts/{id}`** — una fila; incluye `file_id` cuando `status="done"` y
  `error` (mensaje en español) cuando `status="error"`.

### Una sola ruta de estado, venga de donde venga el pedido

`apps/worker/edecan_worker/handlers/generate_podcast.py` soporta los DOS payloads del job
(ver su docstring para el detalle completo):

- **Nuevo** `{"podcast_id": "<uuid>"}` (de `POST /v1/voz/podcasts`) — la fila ya existe; el
  handler la carga, la procesa y actualiza su estado.
- **Viejo** `{"titulo", "segmentos", "user_id", ...}` (de la tool de chat `crear_podcast`,
  sin cambios en su forma) — el handler crea la fila `podcasts` al vuelo al arrancar
  (`status="pending"`), y a partir de ahí sigue la MISMA ruta que el payload nuevo — así los
  podcasts pedidos por chat también aparecen en `GET /v1/voz/podcasts`/la pestaña "Podcasts".

En ambos casos, el estado `"running"` se comitea en su propia sesión corta ANTES de empezar
la síntesis/ensamblado (si el proceso muere a mitad, la fila queda en `"running"`, nunca
`"pending"` fantasma) y, ante cualquier excepción, el handler marca `status="error"` +
`error=<mensaje>` en una sesión nueva antes de volver a lanzar la excepción (para el
reintento estándar del despachador de jobs) — un reintento sobre una fila `"error"`/`"running"`
abandonada es idempotente: `_marcar_running` no filtra por el estado actual.

### UI web: pestaña "Podcasts" en `/app/voz`

`apps/web/src/app/(app)/app/voz/page.tsx` ahora tiene dos pestañas (mismo patrón de tabs que
`/app/rrhh`: botones + `border-b-2`, sin dependencias nuevas): "Voces" (contenido sin cambios,
extraído a `components/voz/VocesTab.tsx`) y "Podcasts"
(`components/voz/PodcastsTab.tsx`, nueva):

- **Formulario**: título + un editor dinámico de segmentos (`texto` + selector de voz
  opcional, poblado con `GET /v1/voces` — el mismo endpoint que ya usa la pestaña "Voces").
  "Agregar segmento"/quitar segmento, hasta 30 segmentos (mismo límite que
  `edecan_creative.podcast.MAX_SEGMENTOS`, validado igual en el servidor).
- **Lista de podcasts**: `Badge` de estado (Pendiente/Generando…/Listo/Error, con el mensaje
  de error visible cuando aplica) y **polling suave** (`setInterval` de 3s con
  `clearInterval` en el cleanup del efecto, mismo patrón que `/app/misiones`) mientras haya
  algún podcast `pending`/`running`.
- **"Descargar" (podcasts `done`)** — limitación conocida y documentada (ver el docstring de
  `apps/web/src/lib/api-voz.ts`, sección "Descarga de un podcast done"): `GET
  /v1/files/{id}` (`apps/api/edecan_api/routers/files.py`) solo devuelve metadata hoy, para
  CUALQUIER archivo del producto — no hay ningún endpoint de descarga real (URL prefirmada de
  S3 o streaming) todavía, ni para podcasts ni para ningún archivo generado por
  `crear_documento`/`crear_presentacion`/`crear_pdf`/`generar_imagen`. `files.py` está fuera
  de las rutas que fase v6 puede tocar, así que el botón confirma que el archivo existe
  (`getFile`, el mismo `GET /v1/files/{id}` que usa `/app/archivos`) y lleva al usuario a esa
  página, donde el archivo ya aparece listado — sin inventar un mecanismo de descarga nuevo.
  Un endpoint real de descarga (URL prefirmada o streaming) queda pendiente para un WP futuro
  dueño de `files.py`.

`apps/web/src/lib/api-voz.ts` gana los tres fetchers (`crearPodcast`/`listPodcasts`/
`getPodcast`) más `getFile`, todos sobre el mismo patrón `authedFetch`/reintento-tras-refresh
que ya usaba el archivo para voces/clones.

## Fuera de alcance de esta versión (P2)

Video y animación siguen fuera de `edecan_creative`, sin fecha comprometida. Podcasts y
efectos de sonido, que antes estaban en esta misma lista, ya se cubrieron en v5 (ver arriba).
Tampoco hay edición de imágenes existentes (inpainting/outpainting) ni variaciones de una
imagen ya generada — solo generación desde cero a partir de un prompt de texto.

## Tests

```
cd packages/creative && PYTHONPATH=. pytest
```

(Hasta que fase v2 sume `packages/creative` al workspace raíz, `uv run pytest
packages/creative` desde la raíz del repo también funciona una vez que el entorno tenga el
paquete instalado — ver `packages/creative/README.md` para el detalle de qué cubre cada
archivo de test.) Todo offline y determinista: `respx` para `OpenAICompatImagesProvider` y
para ElevenLabs (`edecan_creative.podcast`), `aioboto3.Session` sustituido con `monkeypatch`
para `_files.subir_archivo`, un `FakeUploader` inyectado por constructor para las tools que
suben archivo, y `asyncio.create_subprocess_exec` mockeado para la rama mp3/ffmpeg de
`ensamblar_podcast` — ninguna llamada de red, de Postgres, ni un `ffmpeg` real requeridos. Un
único test opcional (`test_podcast.py`, marcado `@pytest.mark.integration`) sí invoca ffmpeg
de verdad si está instalado (se salta solo si no lo está); la suite estándar lo excluye con
`pytest -m "not integration"`. Los tests del job asíncrono viven en
`apps/worker/tests/test_generate_podcast.py` (fakes propios de S3/sesión, ver su docstring),
no en este paquete.

Los endpoints REST (fase v6) tienen su propio archivo,
`apps/api/tests/test_podcasts_router.py` (`test_voz_avanzada.py`, sin cambios, sigue cubriendo
`/voces`/`/clones`) — mismo patrón `FakeSession`/`enqueue` mockeado que el resto de
`apps/api/tests`, sin depender de que la migración de la tabla `podcasts` esté aplicada de
verdad. Todo junto:

```
uv run --all-packages pytest -q packages/creative apps/api/tests/test_voz_avanzada.py \
  apps/api/tests/test_podcasts_router.py apps/worker/tests/test_generate_podcast.py \
  -m "not integration"
```
