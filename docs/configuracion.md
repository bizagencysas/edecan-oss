# Configuración — variables de entorno

Todas las variables de entorno de la plataforma se declaran en `apps/api/edecan_api/config.py` (pydantic-settings, `Settings`) y están listadas con sus placeholders en [`.env.example`](../.env.example). Este documento es la referencia completa: para qué sirve cada una, si es obligatoria y cuál es su valor por defecto. La fuente normativa de esta lista es `ARCHITECTURE.md` §10.2 — si hay alguna discrepancia, ese documento manda.

Cómo leer la columna **Obligatoria**:

- **Obligatoria** — sin un valor propio la funcionalidad correspondiente no arranca o no sirve de nada (p. ej. un LLM real).
- **Obligatoria en prod** — el default de `.env.example` alcanza para desarrollo local, pero **nunca** debe usarse en una instancia real (secretos de placeholder).
- **Opcional** — tiene un default razonable y/o la funcionalidad relacionada cae a un modo `stub`/offline si se omite.
- **Solo si usas X** — solo hace falta si vas a activar ese conector/proveedor concreto.

Recuerda la regla dura del proyecto: **nunca** un secreto real fuera de tu `.env` local — ni en código, ni en commits, ni en esta documentación. Todo lo que ves abajo como valor son placeholders `TU_X_AQUI`.

## General / plataforma

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `ENV` | Opcional | `dev` | `dev` o `prod`. Cambia comportamientos como CORS, nivel de detalle de errores y qué `KeyProvider` conviene usar (Fernet local vs. KMS). |
| `PUBLIC_BASE_URL` | Obligatoria en prod | `http://localhost:8000` | URL pública donde vive la API. Se usa para construir los `redirect_uri` de OAuth (`{PUBLIC_BASE_URL}/v1/connectors/{key}/callback`) y los webhooks de Twilio. |
| `WEB_BASE_URL` | Obligatoria en prod | `http://localhost:3000` | URL pública del frontend Next.js. La API la usa para configurar CORS (solo este origen puede llamar a la API). |
| `LOG_LEVEL` | Opcional | `INFO` | Nivel de logging de todas las apps (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `JWT_SECRET` | Obligatoria en prod | `TU_JWT_SECRET_AQUI` | Clave HS256 con la que la API firma y valida los JWT de sesión (`{sub, ten, plan, typ, exp}`). Un valor de placeholder invalida cualquier expectativa de seguridad de sesión. |

## Cifrado del `TokenVault`

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `LOCAL_MASTER_KEY` | Obligatoria en prod (si no usas KMS) | `TU_LOCAL_MASTER_KEY_FERNET_AQUI` | Clave Fernet que envuelve la *data key* AES-256-GCM de cada tenant (tabla `tenant_keys`) cuando no hay `KMS_KEY_ID`. Es el modo por defecto en dev/self-host. Ver `ARCHITECTURE.md` §10.4 y [`runbooks/rotacion-claves.md`](./runbooks/rotacion-claves.md). |
| `KMS_KEY_ID` | Solo si usas AWS KMS | *(vacío)* | Si se define, el `TokenVault` usa `KmsKeyProvider` (boto3 KMS) en vez de la Fernet local para envolver/desenvolver las data keys por tenant — el modo recomendado en producción hospedada. |

## Base de datos y caché

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `DATABASE_URL` | Obligatoria en prod | `postgresql+asyncpg://edecan:edecan@localhost:5432/edecan` | Cadena de conexión async (asyncpg) a PostgreSQL 16 + pgvector. La usan `edecan_db`, Alembic y todos los servicios que abren sesión. |
| `REDIS_URL` | Obligatoria en prod | `redis://localhost:6379/0` | Caché, rate-limiting y códigos de emparejamiento del companion (`pair-code`, TTL 600s). |

## AWS (S3, SQS, KMS)

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `AWS_REGION` | Opcional | `us-east-1` | Región de AWS para S3/SQS/KMS reales en producción. |
| `AWS_ENDPOINT_URL` | Solo en dev/self-host | *(vacío; `.env.example` trae `http://localhost:4566`)* | Si se define, redirige boto3/aioboto3 a LocalStack en vez de AWS real — así es como funciona el modo desarrollo y el self-host "producción ligera" (ver [`self-hosting.md`](./self-hosting.md)). En AWS real se deja vacío y se usa el rol IAM de la tarea ECS. |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | Solo contra LocalStack | `test` / `test` (en `.env.example`) | Credenciales dummy que exige el SDK de AWS para hablar con LocalStack. **Nunca se usan contra AWS real** — ahí la identidad la da el rol IAM de la tarea ECS, no estas variables. |
| `S3_BUCKET` | Opcional | `edecan-files` | Bucket donde se guardan los archivos subidos, con prefijo por tenant (`tenants/{tenant_id}/files/{file_id}/{filename}`, ver `ARCHITECTURE.md` §10.14). |
| `SQS_QUEUE_URL` | Obligatoria en prod | *(vacío; en dev apunta a la cola de LocalStack)* | Cola principal de jobs asíncronos (`edecan-jobs`) que consume `apps/worker`. |

## LLM

**Importante — esto NO es la credencial LLM que usa el chat de ningún tenant.** Desde la corrección del modelo bring-your-own (`docs/credenciales.md` "Orden de resolución"), `apps/api` (`POST /v1/conversations/{id}/messages`, voz web) nunca lee `ANTHROPIC_API_KEY`/`OPENAI_COMPAT_*` para servir la request de un tenant: cada tenant conecta su propio proveedor en **Configuración** (`PUT /v1/credentials/llm`), o esa ruta corta con `400` — nunca degrada a la clave de abajo. Las variables de esta sección solo las usa `apps/worker` para jobs de sistema sin tenant (p. ej. `send_reminder_scan`) y, todavía, como fallback interno de `apps/worker` para tenants que no conectaron su propio LLM (hueco pendiente de corregir ahí, ver la nota en `docs/credenciales.md`).

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Solo para los jobs de sistema de `apps/worker` (ver nota arriba) | *(vacío)* | Clave de la API de Anthropic que usa el `LLMRouter` de PLATAFORMA (`AnthropicProvider`, REST directo a `https://api.anthropic.com/v1/messages`) — hoy solo alcanzable desde `apps/worker`, nunca desde el chat de un tenant vía `apps/api`. |
| `ANTHROPIC_MODEL_PRINCIPAL` | Opcional | `claude-sonnet-4-5` | Modelo al que resuelve el alias lógico `"principal"` en `LLMRouter`, salvo que el plan del tenant no tenga el flag `models.premium` (en ese caso degrada al modelo `"rapido"`). |
| `ANTHROPIC_MODEL_RAPIDO` | Opcional | `claude-haiku-4-5` | Modelo al que resuelve el alias lógico `"rapido"` — usado para tareas más económicas/latentes (p. ej. resumir, clasificar). |
| `OPENAI_COMPAT_BASE_URL` | Solo si usas un proveedor OpenAI-compatible (LLM de `apps/worker` arriba, y/o embeddings — ver más abajo) | *(vacío; `.env.example` trae `https://api.openai.com/v1`)* | Base URL de cualquier endpoint compatible con `/chat/completions` (OpenAI, Groq, Together.ai, un LLM local, etc.), usado por `OpenAICompatProvider` como alternativa intercambiable a Anthropic. |
| `OPENAI_COMPAT_API_KEY` | Solo si usas `OPENAI_COMPAT_BASE_URL` | *(vacío)* | Clave para el endpoint anterior. |

## Embeddings y búsqueda web

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `EMBEDDINGS_MODEL` | Solo si usas `OpenAICompatEmbedder` | *(vacío)* | Nombre del modelo de embeddings a usar para memoria semántica (`pgvector`). Sin configurarlo, la memoria usa `HashEmbedder` (determinista, offline, sin llamadas externas). |
| `EMBEDDINGS_DIM` | Opcional | `1536` | Dimensión de los vectores de embedding — debe coincidir con la columna `vector(1536)` de `memory_items`/`file_chunks` si la cambias. |
| `SEARCH_PROVIDER` | Opcional | `stub` | `stub`, `brave` o `tavily`. Controla qué `SearchProvider` usa la herramienta `buscar_web`. `stub` no hace ninguna llamada real. |
| `BRAVE_API_KEY` | Solo si `SEARCH_PROVIDER=brave` | *(vacío)* | Clave de la Brave Search API. |
| `TAVILY_API_KEY` | Solo si `SEARCH_PROVIDER=tavily` | *(vacío)* | Clave de la Tavily API. |

## Voz web (`voice.web`)

**Estas variables ya NO alimentan `POST /v1/voice/{transcribe,speak}` de ningún tenant** (mismo criterio que la sección LLM arriba): `apps/api/edecan_api/routers/voice.py` resuelve el proveedor de voz por tenant vía `PUT /v1/credentials/voice/{stt,tts}` y, si el tenant no conectó nada, cae directo a `StubSTT`/`StubTTS` — nunca llama a `edecan_voice.registry.get_stt`/`get_tts` (que es lo único que lee las variables de esta sección). Se documentan igual porque `edecan_voice.registry` sigue siendo parte del paquete público (`edecan_voice`) y sus propios tests las usan.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `VOICE_STT_PROVIDER` | Sin efecto en `apps/api` (ver nota arriba) | `stub` | `stub` o `deepgram`. Solo lo lee `edecan_voice.registry.get_stt`, que ningún router de `apps/api` invoca hoy. |
| `VOICE_TTS_PROVIDER` | Sin efecto en `apps/api` (ver nota arriba) | `stub` | `stub`, `elevenlabs` o `polly`. Solo lo lee `edecan_voice.registry.get_tts`, que ningún router de `apps/api` invoca hoy. |
| `DEEPGRAM_API_KEY` | Sin efecto en `apps/api` (ver nota arriba) | *(vacío)* | Solo relevante si algo fuera de `apps/api` usa `edecan_voice.registry.get_stt` directamente. |
| `ELEVENLABS_API_KEY` | Sin efecto en `apps/api` (ver nota arriba) | *(vacío)* | Solo relevante si algo fuera de `apps/api` usa `edecan_voice.registry.get_tts` directamente. |
| `ELEVENLABS_VOICE_ID` | Sin efecto en `apps/api` (ver nota arriba) | *(vacío)* | Voz por defecto de ElevenLabs si la persona (`PersonaConfig.voice_id`) no especifica una propia — solo aplica a la voz que el propio tenant conectó (`voice_id` en `PUT /v1/credentials/voice/tts`), no a esta variable de plataforma. |
| `POLLY_VOICE` | Opcional | `Lupe` | Voz por defecto de AWS Polly (usa las credenciales AWS descritas arriba) si la persona no especifica `voice_id` — sí aplica cuando un tenant elige `provider: "polly"` en `PUT /v1/credentials/voice/tts`, y SOLO con `EDECAN_LOCAL_MODE=true` (Polly no tiene una "API key" propia que el tenant traiga, usa la identidad AWS de quien opera el servidor — fuera de modo local eso sería una identidad compartida entre tenants, así que se rechaza; ver `docs/credenciales.md`). |

## Telefonía (premium, flag `voice.telephony`)

Twilio **no** se configura por variable de entorno de la plataforma: cada tenant conecta su propia cuenta (Account SID / Auth Token) desde el panel, y las credenciales quedan cifradas por tenant en el `TokenVault` (connector key `"twilio"`). Ver `ARCHITECTURE.md` §4 y §10.10, y [`voz-telefonia.md`](./voz-telefonia.md).

## OAuth por conector

Cada par `*_CLIENT_ID` / `*_CLIENT_SECRET` (o `*_APP_ID` / `*_APP_SECRET`) identifica **la aplicación OAuth de la plataforma** registrada por quien opera esta instancia — no son credenciales de ningún usuario final. Cada tenant autoriza su propia cuenta contra esa app. Ver [`conectores.md`](./conectores.md) para el paso a paso de cómo crear cada una y sus scopes exactos.

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Solo si activas el conector `google` (o `youtube`, que reutiliza el mismo proyecto de Google Cloud) | *(vacío)* | Credenciales OAuth 2.0 de Google (Gmail + Calendar + YouTube Data API v3 sobre el mismo proyecto). |
| `MS_CLIENT_ID` / `MS_CLIENT_SECRET` | Solo si activas el conector `microsoft` | *(vacío)* | Credenciales OAuth 2.0 de Microsoft identity platform (Outlook Mail + Calendar). |
| `META_APP_ID` / `META_APP_SECRET` | Solo si activas el conector `meta` (flag `connectors.social`) | *(vacío)* | Credenciales de una app de Meta for Developers (Facebook Pages + Instagram Business, Graph API). |
| `X_CLIENT_ID` / `X_CLIENT_SECRET` | Solo si activas el conector `x` (flag `connectors.social`) | *(vacío)* | Credenciales OAuth 2.0 (PKCE) de una app de X Developer Portal (API v2). |
| `SLACK_CLIENT_ID` / `SLACK_CLIENT_SECRET` | Solo si activas el conector `slack` (flag `connectors.messaging`) | *(vacío)* | Credenciales OAuth v2 de una app de Slack (Web API: envío/lectura de mensajes en canales públicos). |

## Facturación y correo transaccional (capa hospedada)

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `STRIPE_SECRET_KEY` | Solo en la capa hospedada (billing) | *(vacío)* | Clave secreta de Stripe para `POST /v1/billing/portal` y para procesar suscripciones. Un self-host que no cobra a nadie no la necesita. |
| `STRIPE_WEBHOOK_SECRET` | Solo si usas billing | *(vacío)* | Secreto para verificar la firma de `POST /v1/billing/webhook`. |
| `SES_FROM_EMAIL` | Solo si envías correo transaccional | *(vacío)* | Remitente verificado en Amazon SES para correo transaccional de la plataforma (no confundir con el conector de Gmail/Outlook del tenant, que es para SU correo). |

## Observabilidad

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `SENTRY_DSN` | Opcional | *(vacío)* | Si se define, activa el reporte de errores a Sentry. Sin ella, no se envía ningún dato a un servicio externo de observabilidad. |

## v3 — modo local, proveedores LLM nuevos, marketplace de skills

Variables nuevas de `ARCHITECTURE.md` §12g (`DIRECCION_ACTUAL.md`: pivote a app de escritorio Tauri + proveedores LLM CLI/Ollama/Vertex + marketplace abierto de skills.sh). Mismo criterio que el resto del documento: cada tool/router v3 las lee con `getattr(ctx.settings, "CAMPO", default)` y nunca revienta si falta una.

### Modo local / app de escritorio

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `EDECAN_LOCAL_MODE` | Opcional | `false` | La fija automáticamente el runner de la app de escritorio (`apps/local`, `python -m edecan_local`) al arrancar — un usuario normal nunca la toca a mano. Señala que la API corre single-user en la máquina del cliente, no en un despliegue multi-tenant hospedado. |
| `DATA_DIR` | Opcional | `~/.edecan/data` | Carpeta donde el modo local guarda datos persistentes (Postgres embebido, archivos). La fija automáticamente `apps/local`; `~` se expande a la carpeta del usuario del sistema. |
| `SERVE_WEB_DIR` | Opcional | *(vacío)* | Si se define y la carpeta existe, `edecan_api.main.create_app()` sirve ahí el export estático de `apps/web` en la raíz (`"/"`) — así el runner local expone la UI completa desde el mismo puerto que la API. Vacío = no se monta nada en `"/"`. |
| `LOCAL_API_PORT` | Opcional | `8765` | Puerto donde escucha la API en modo local. El runner (`apps/local`) hace bind SOLO en `127.0.0.1`, nunca `0.0.0.0` — ver `ARCHITECTURE.md` §12f. |
| `QUEUE_PROVIDER` | Opcional | `sqs` | `sqs` (LocalStack/AWS real, igual que hoy) o `db` (usa la tabla `jobs` como cola, sin SQS/LocalStack) — `db` es la opción recomendada para el modo escritorio de un solo usuario. |

### Proveedores LLM nuevos (CLI local, Ollama, Vertex AI)

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `OLLAMA_BASE_URL` | Opcional | `http://localhost:11434` | Dónde busca Edecán una instancia de Ollama (modelos locales del cliente) ya corriendo, para autodetectarla y ofrecerla como proveedor LLM gratuito y privado. |
| `CLAUDE_CLI_PATH` | Opcional | *(vacío = autodetectar en PATH)* | Ruta al binario `claude` (Claude Code) para usarlo como proveedor LLM vía subproceso, reusando la sesión/suscripción ya autenticada del cliente en su máquina — sin pedir una API key de Anthropic aparte. |
| `CODEX_CLI_PATH` | Opcional | *(vacío = autodetectar en PATH)* | Igual que `CLAUDE_CLI_PATH` pero para el binario `codex` (Codex CLI de OpenAI). |
| `LLM_CLI_TIMEOUT_SECONDS` | Opcional | `300` | Timeout de cada invocación de un CLI de LLM (`claude`/`codex`) como subproceso. |
| `VERTEX_MODEL_PRINCIPAL` | Opcional | `gemini-2.5-pro` | Modelo de Vertex AI para el alias lógico `"principal"`, cuando el tenant elige Vertex como proveedor (bring-your-own proyecto GCP: service account o ADC propios). |
| `VERTEX_MODEL_RAPIDO` | Opcional | `gemini-2.5-flash` | Modelo de Vertex AI para el alias lógico `"rapido"`. |

Nota: cuál proveedor usa cada tenant (API key normal, Vertex, o un CLI local) se resuelve **por tenant** vía `TokenVault` (connector key `"llm"`, ver `ARCHITECTURE.md` §12b/§12c) — estas variables de entorno solo fijan defaults/rutas de autodetección, nunca una API key de plataforma compartida.

### Marketplace de skills y smarthome

| Variable | Obligatoria | Default | Para qué sirve |
|---|---|---|---|
| `SKILLS_INDEX_URL` | Opcional | `https://skills.sh` | Índice del marketplace abierto de "Agent Skills" contra el que el toolkit de Edecán busca/instala skills reusables de terceros (`ARCHITECTURE.md` §12e). |
| `HOMEASSISTANT_TIMEOUT_SECONDS` | Opcional | `15` | Timeout de cada llamada a la API REST de Home Assistant (instancia propia del cliente, credenciales por tenant vía `TokenVault` — connector key `"homeassistant"`, §12b). |

## El wizard de primer arranque — `/v1/setup/*` (barrido v7, WP-V7-08)

Estas dos rutas (`apps/api/edecan_api/routers/setup.py`, montada defensivamente, ver
`V3_ROUTER_NAMES`) son las que la pantalla de bienvenida (`docs/primeros-pasos.md`)
consulta para decidir qué mostrarte — no son variables de entorno, pero dependen
directamente de `EDECAN_LOCAL_MODE` (arriba) y de si ya conectaste un LLM
(`PUT /v1/credentials/llm`), así que quedan documentadas aquí junto al resto de la
configuración de arranque.

### `GET /v1/setup/status`

Bearer normal. Forma REAL de la respuesta (verificada contra `apps/api/edecan_api/
routers/setup.py::get_setup_status`, no asumida):

```json
{"local_mode": false, "llm_configured": false, "version": "0.1.0"}
```

- `local_mode` — `true` solo si el proceso arrancó con `EDECAN_LOCAL_MODE=true` (lo fija
  automáticamente `apps/local`, un usuario normal nunca lo toca a mano).
- `llm_configured` — `true` solo si el tenant ya tiene una `connector_account` con
  `connector_key="llm"` Y un `TokenBundle` guardado de verdad en el vault para ella
  (mismo criterio que `routers/credentials.py::get_credentials`) — si es `false`, el
  wizard debe forzar el paso de conectar un LLM antes de dejar chatear
  (`DIRECCION_ACTUAL.md`, "configuración de pocos clicks").
- `version` — `edecan_api.__version__`, para mostrarla en la UI/reportes de bugs.

### `GET /v1/setup/detect`

Autodetección de un clic (`ARCHITECTURE.md` §12.d, `edecan_llm.detect.
detect_local_providers`) — SOLO detecta algo real cuando `local_mode` es `true` (en un
servidor hospedado, detectar los binarios/puertos del PROCESO no tiene sentido — serían
los del servidor compartido, no los de la máquina de quien mira la pantalla — así que el
endpoint responde siempre el shape vacío sin llamar a nada):

```json
{
  "local_mode": true,
  "claude_cli": {"installed": true, "path": "/usr/local/bin/claude", "version": "1.4.2"},
  "codex_cli": {"installed": false, "path": null, "version": null},
  "ollama": {"running": true, "base_url": "http://localhost:11434", "models": ["llama3.1"]}
}
```

Sin `local_mode` (el caso hospedado, default), o si `edecan_llm.detect` todavía no
aterrizó en el entorno (import perezoso con guardia): `{"local_mode": false, ...}` con
las tres claves de proveedor en su forma vacía (`installed`/`running` en `false`,
`path`/`models` vacíos) — nunca lanza, nunca omite ninguna clave.

**Nota de consistencia (barrido v7, WP-V7-08)**: `docs/api.md` §`/v1/setup` documenta
una forma de respuesta distinta y desactualizada para las dos rutas —
`{"llm_connected", "voice_connected", "onboarding_complete"}` para `/status` (los campos
reales son `local_mode`/`llm_configured`/`version`, arriba) y un campo `"mode":
"local"|"hosted"` para `/detect` (el campo real es `local_mode: bool`, no `mode: str`).
`docs/api.md` no está en las rutas que este WP puede tocar (ver
`docs/cumplimiento/barrido-v7-routers-restantes.md`); esta sección de aquí SÍ refleja el
código real, verificado leyendo `setup.py` directamente — no repite el error.

## Planes y flags (no son variables de entorno)

Los flags de producto (`voice.web`, `voice.telephony`, `connectors.social`, `campaigns`, `companion`, `models.premium`) y los límites de uso (`limits.messages_per_day`, `limits.voice_minutes_month`, `limits.storage_mb`, `limits.phone_numbers`, `limits.seats`) **no se configuran por `.env`**: viven codificados en `edecan_schemas.plans.PLANES` y se resuelven server-side según el `plan_key` del tenant — nunca se confía en lo que el JWT diga. Ver `ARCHITECTURE.md` §10.13.

| `plan_key` | Precio/mes | Voz web | Sociales | Telefonía | Campañas | Msjs/día | Min. voz/mes | Storage | Números | Asientos | Modelos premium |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `free_selfhost` | $0 | Sí | Sí | No | No | Ilimitado | Ilimitado | Ilimitado | 0 | 1 | No |
| `hosted_basic` | $19 | Sí | No | No | No | 150 | 60 | 1024 MB | 0 | 1 | No |
| `hosted_pro` | $49 | Sí | Sí | Sí | No | 600 | 300 | 10240 MB | 1 | 1 | Sí |
| `hosted_business` | $149 | Sí | Sí | Sí | Sí | 2000 | 1000 | 51200 MB | 3 | 5 | Sí |

En self-host, el tenant creado por defecto queda en `free_selfhost` — sin límites de plataforma; tus únicos límites reales son tu propia infraestructura y las cuotas de las API que conectaste.
