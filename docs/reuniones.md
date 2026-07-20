# Reuniones (transcripción + minutas)

Edecán transcribe una reunión grabada (audio o video que ya subiste) con el **STT de
tu propia cuenta** (Deepgram) y genera minutas — resumen, decisiones, acciones y
temas — con **tu propio modelo de lenguaje** (`ARCHITECTURE.md` §15).

100% bring-your-own, sin excepción ([`credenciales.md`](./credenciales.md)): Edecán nunca tiene ni usa una cuenta propia de
Deepgram, ni un modelo de lenguaje propio, para procesar reuniones de ningún tenant.

## Disclaimer de consentimiento — léelo primero

> **Recuerda: asegúrate de contar con el consentimiento de todos los participantes
> para grabar y transcribir esta reunión.**

Ese texto es el disclaimer OBLIGATORIO, mostrado SIEMPRE — en el resultado de la tool
del agente (`resumir_reunion`) y en un banner visible junto al botón "Transcribir y
resumir" de `/app/reuniones` — nunca oculto detrás de una configuración o un enlace.

**Posición legal del producto**: obtener el consentimiento de todos los participantes
para grabar y transcribir una conversación es responsabilidad exclusiva de quien la
graba (leyes de consentimiento de grabación varían por jurisdicción — algunas exigen
el consentimiento de una sola parte, otras el de todas). Edecán no verifica ni puede
verificar que ese consentimiento exista — no es una plataforma de grabación, solo
procesa un archivo que el usuario ya subió por su cuenta. Por eso el producto se
limita a **recordarlo siempre, de forma visible**, en cada punto de entrada de esta
funcionalidad — igual que la telefonía (`docs/voz-telefonia.md`, consentimiento de
llamadas/SMS) y la clonación de voz (consentimiento grabado y verificado) tienen su
propio disclaimer/flujo de evidencia, adaptado a que acá no hay grabación en vivo que
Edecán controle, solo un archivo que ya existe.

## Bring-your-own: STT (Deepgram) y LLM

| Paso | Proveedor | Credencial | Sin conectar |
|---|---|---|---|
| Transcripción (STT) | Deepgram | `PUT /v1/credentials/voice/stt` (`{"provider": "deepgram", "api_key": "..."}`) | `StubSTT` — transcripción de prueba offline, la reunión igual se completa con un aviso claro (ver abajo) |
| Minutas (LLM) | el que hayas conectado (`PUT /v1/credentials/llm`) | igual que cualquier otra tool que use el LLM | el job termina en `status='error'` (sin LLM no hay minutas que generar — no hay "minuta de prueba" razonable) |

`packages/meetings/edecan_meetings/stt.py` resuelve el STT del tenant replicando el
mismo patrón "tenant → stub, SIN paso de plataforma" que ya usa el resto del repo
(`edecan_voice.tenant` para TTS, `apps/api/edecan_api/routers/voice.py::_stt_para_tenant`
para STT en la voz web) — nunca lee `DEEPGRAM_API_KEY` de `.env`/`Settings` de
plataforma como fallback.

**Si no conectaste Deepgram**: la reunión igual se procesa de punta a punta (mismo
criterio "el job nunca revienta por falta de configuración" que ya siguen podcasts y
generación de contenido) — la transcripción sale como el texto fijo de prueba del
stub, y el campo `error` de la reunión queda con un aviso legible ("No conectaste tu
propio proveedor de voz (STT)...") aunque el `status` final sea `'done'`. Las minutas
igual se generan (a partir de ese texto de prueba, así que van a ser poco útiles) si
tenés un LLM conectado.

## Flujo

```
Chat (agente) o /app/reuniones (UI)
        │
        ▼
resumir_reunion (Tool)             POST /v1/reuniones {file_id, titulo?}
requires_flags={"tools.meetings"}   valida mime audio/*|video/* + dueño
dangerous=False                     INSERT meetings (status='pending') + encola
        │                                   │
        │  valida archivo, encola           │  MISMA transacción corta
        │  process_meeting                  │  (INSERT + enqueue juntos)
        │  {file_id, titulo, user_id}       │
        │  (NUNCA inserta la fila           ▼
        │   ella misma — ver el         202 Accepted
        │   docstring de tools.py)
        ▼
process_meeting (job, worker)
  1. carga o crea la fila meetings, status='running'
  2. descarga el archivo de S3
  3. ffmpeg: extrae audio -> WAV mono 16 kHz
  4. STT del tenant (Deepgram bring-your-own, fail-closed a stub)
  5. sube la transcripción como un archivo nuevo (texto plano)
  6. LLM del tenant (bring-your-own, resuelto PEREZOSO -recién con
     transcript en mano-): arma el prompt de minutas, parsea la respuesta
  7. guarda resumen / decisiones / acciones / temas / duración, status='done'
        │
        ▼
GET /v1/reuniones, GET /v1/reuniones/{id} — resultado visible en /app/reuniones
(la UI hace polling cada 5s mientras la reunión esté pending/running)
```

Dos caminos para encolar `process_meeting`, según quién lo dispare (ver el docstring
de `packages/meetings/edecan_meetings/tools.py`, sección "Por qué la tool NO inserta
la fila `meetings` ella misma", para la justificación completa):

- **Desde el chat** (tool `resumir_reunion`): la tool NUNCA toca la base de datos —
  solo valida el archivo y encola `process_meeting {file_id, titulo, user_id}`. El
  handler del worker crea la fila `meetings` desde cero como primer paso.
- **Desde `/app/reuniones`** (`POST /v1/reuniones`): el router inserta la fila
  `meetings` y encola `process_meeting {meeting_id}` dentro de la MISMA transacción
  HTTP corta — sin la carrera de "lee tu propia escritura" que sí existiría si un
  turno de chat completo (potencialmente varios segundos, con más tool calls después)
  insertara y encolara antes de comitear.

## Endpoints (`/v1/reuniones/*`, flag de plan `tools.meetings`)

- `POST /v1/reuniones {file_id, titulo?}` → `202 Accepted` con la reunión recién
  creada (`status='pending'`). `404` si el archivo no existe/no es tuyo, `400` si no es
  audio/video.
- `GET /v1/reuniones` → lista todas tus reuniones, más recientes primero.
- `GET /v1/reuniones/{id}` → detalle completo (resumen, decisiones, acciones, temas,
  `transcript_file_id`, duración, error).
- `DELETE /v1/reuniones/{id}` → `204`, borra **solo la fila de la reunión**. El
  archivo original y la transcripción (ambos filas `files` normales, visibles en
  `/app/archivos`) se quedan intactos a propósito — no son "propiedad exclusiva" de
  la reunión, son archivos normales del tenant que el agente puede seguir consultando
  con `consultar_documentos`. Si también querés borrar esos archivos, hacelo aparte
  desde `/app/archivos`.

Todos exigen el flag de plan `tools.meetings` (`403` si el plan no lo trae) y
`Authorization: Bearer <token>` como el resto de la API (`401` sin sesión).

## Herramienta del agente

`resumir_reunion` (`requires_flags={"tools.meetings"}`, `dangerous=False`): recibe
`archivo` (id de un audio/video ya subido) y `titulo` opcional, valida que exista y
sea audio/video, encola la transcripción+minutas en segundo plano y devuelve el
disclaimer de consentimiento. No es `dangerous` — no mueve dinero ni actúa sobre nada
externo, solo procesa un archivo que el propio tenant ya subió.

## Limitaciones

- **Requiere `ffmpeg` instalado en la máquina** (self-host) o en la imagen del worker
  (hosted) — es un binario del SISTEMA, nunca una dependencia de Python
  (`packages/meetings/edecan_meetings/audio.py`, mismo criterio que
  `edecan_docanalysis.video`/`edecan_creative.podcast`). Se detecta **en caliente**
  (`shutil.which("ffmpeg")`, o `FFMPEG_PATH` si lo fijaste) — sin él, la reunión
  termina en `status='error'` con un mensaje instructivo (`brew install ffmpeg` /
  `apt install ffmpeg` / enlace de descarga para Windows), nunca un traceback críptico.
- Tope de tamaño del archivo de origen: 300 MB (reuniones de hasta ~2-3 horas en
  calidad razonable). Archivos más grandes se rechazan con un mensaje claro antes de
  tocar disco.
- La transcripción se acota a 60 000 caracteres al armar el prompt de minutas (cota de
  seguridad de contexto del LLM) — reuniones extremadamente largas generan minutas
  basadas solo en la primera parte de la transcripción; la transcripción completa
  (sin acotar) siempre queda disponible como archivo en `/app/archivos`.
- No hay descarga directa del archivo de transcripción desde `/app/reuniones` — el
  producto todavía no tiene un endpoint de descarga de contenido de `files` en
  absoluto (`GET /v1/files/{id}` solo devuelve metadatos, igual en `/app/archivos`);
  el link a la transcripción te lleva a esa página, donde el archivo ya aparece
  listado.
- `duración` se redondea al segundo entero más cercano antes de guardarse
  (`meetings.duracion_segundos` es `INTEGER` en Postgres real, no `NUMERIC` —
  verificado empíricamente contra Postgres real en la re-verificación de v7,
  `docs/cumplimiento/barrido-v7-reuniones-analista.md`): una reunión de "1.5s"
  de audio queda registrada como `2`, nunca con precisión de sub-segundo. Sin
  impacto práctico (nadie necesita fracciones de segundo en la duración de una
  reunión), documentado para que no sorprenda a quien lea la columna directo.

`edecan_schemas.queue.JOB_TYPES` (`process_meeting`, 12º valor) y
`edecan_schemas.plans` (flag `tools.meetings`, `✔` en
`free_selfhost`/`hosted_pro`/`hosted_business`, `✖` en `hosted_basic`) están
pinned de verdad desde que el linchpin de v6 (fase v6) aterrizó —
`edecan_api.main.V6_ROUTER_NAMES` ya incluye `"reuniones"` y `"analista"`.
Re-verificado contra Postgres real en v7 (`docs/cumplimiento/barrido-
v7-reuniones-analista.md`): el esquema completo de `meetings` (columnas,
CHECK de `status`, tipos) coincide exactamente con
`packages/db/alembic/versions/0008_v6_expansion.py`/`edecan_db.models.Meeting`.
