# Credenciales — bring-your-own de LLM, voz, imágenes y búsqueda, por tenant

El principio bring-your-own de `ARCHITECTURE.md` §0 es la posición de producto vigente: Edecán nunca opera cuentas de terceros en nombre de un cliente. "Self-host" y "hosted" describen únicamente **quién corre el servidor** — las credenciales de LLM, voz (Deepgram/ElevenLabs/Polly), Twilio y los conectores OAuth las trae **cada tenant**, en ambos modos. Este documento cubre LLM y voz web; para Twilio ve [`voz-telefonia.md`](./voz-telefonia.md) y para los conectores OAuth, [`conectores.md`](./conectores.md).

**Imágenes y búsqueda web** (`"images"`/`"search"`) admiten proveedores propios por tenant y nunca reutilizan una key de plataforma. Imágenes conservan un generador local de demostración cuando no hay proveedor. Búsqueda web funciona de verdad sin credenciales mediante `DuckDuckGoSearch`; Brave y Tavily son mejoras opcionales para quien quiera traer su propia cuenta. Ninguna de estas capacidades depende del modelo LLM conectado.

Esta es también la pantalla de **"Configuración"** de la app de escritorio: un campo, un botón "Conectar" que valida la credencial al toque contra el proveedor real y muestra ✅ o el error exacto — nunca un `.env` a mano.

## Qué guarda el `TokenVault`

Todo vive en la misma tabla `connector_accounts` + `TokenVault` que ya usaba Twilio desde v1 (`ARCHITECTURE.md` §10.4). Cinco `connector_key` nuevos (los primeros tres de fase v3; `"images"`/`"search"` de la corrección posterior, ver arriba), cada uno **singleton por tenant** (a diferencia de un conector OAuth normal, que puede tener varias cuentas del mismo proveedor, aquí un tenant tiene UNA config activa a la vez de cada tipo):

| `connector_key` | Qué guarda | Lo guarda | `TokenBundle.access_token` |
|---|---|---|---|
| `"llm"` | Proveedor de LLM del tenant | `PUT /v1/credentials/llm` | JSON con los campos de `LLMProviderConfig` (`kind`, `api_key`, `base_url`, `model_principal`, `model_rapido`, `extra`) |
| `"voice_stt"` | Transcripción de voz web | `PUT /v1/credentials/voice/stt` | JSON `{"provider": "deepgram", "api_key": "..."}` |
| `"voice_tts"` | Síntesis de voz web | `PUT /v1/credentials/voice/tts` | JSON `{"provider": "elevenlabs", "api_key": "...", "voice_id": "..."}` o `{"provider": "polly", "voice": "Lupe"}` |
| `"images"` | Generación de imágenes del tenant | `PUT /v1/credentials/images` | JSON `{"base_url": "...", "api_key": "...", "model": "..."}` (mismos campos que `OpenAICompatImagesProvider`, único proveedor real hoy) |
| `"search"` | Búsqueda web del tenant | `PUT /v1/credentials/search` | JSON `{"provider": "brave"|"tavily", "api_key": "..."}` |

`TokenBundle.token_type` queda en `"config"` para los cinco (distingue en runtime "esto hay que `json.loads()`" del `"bearer"` que usan los conectores OAuth normales, o el `"config"` con `scopes=[ACCOUNT_SID]` que ya usaba Twilio). El vault cifra el bundle completo con AES-256-GCM usando la data key propia del tenant (`tenant_keys`, creada perezosamente) — nunca en claro, nunca en logs (ver "Seguridad" más abajo).

A diferencia de `"llm"`/`"voice_stt"`/`"voice_tts"` (que sí resuelve `apps/api/edecan_api/deps.py`/`routers/voice.py` como parte de armar el `ToolContext`/la sesión de voz, ver la sección de abajo), `"images"`/`"search"` los resuelve la propia `Tool` en el momento de correr (`edecan_creative.providers.get_tenant_image_provider(ctx)`, `edecan_toolkit.research.get_tenant_search_provider(ctx)`), leyendo `ctx.session`/`ctx.vault`/`ctx.tenant_id` — que `ToolContext` ya trae — en vez de por un `load_tenant_*_config` centralizado en `deps.py`. No hay una "resolución por request" separada para tools individuales; cada `Tool` que necesite un proveedor bring-your-own lo resuelve ella misma, perezosamente.

Otros `connector_key` que comparten la misma tabla/vault pero NO son parte de este documento: `"twilio"` (telefonía, ver [`voz-telefonia.md`](./voz-telefonia.md)), `"google"`/`"microsoft"`/`"meta"`/`"x"`/`"youtube"`/`"slack"` (OAuth, ver [`conectores.md`](./conectores.md)), `"telegram"`/`"discord"` (bots, ver [`mensajeria.md`](./mensajeria.md)), y `"homeassistant"`/`"whatsapp"` (v3, ver [`casa-inteligente.md`](./casa-inteligente.md)).

## Orden de resolución (`apps/api`): tenant → error (LLM) / tenant → stub (voz) — SIN paso de plataforma

`apps/api/edecan_api/deps.py::get_llm_router`/`load_tenant_llm_config` y `apps/api/edecan_api/routers/voice.py` NUNCA leen `ANTHROPIC_API_KEY`/`DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` de plataforma para resolver la petición de un tenant. El orden real, en los dos puntos de `apps/api` donde el backend necesita hablar con un LLM o un proveedor de voz por cuenta de un tenant:

1. **Tenant** — ¿este tenant conectó su propia credencial (`connector_accounts` + `TokenVault` para `"llm"`/`"voice_stt"`/`"voice_tts"`)? Si sí, se usa ESA, siempre.
2. **Sin credencial propia**:
   - **LLM** — `get_llm_router` corta la request con `HTTPException(400)`, mismo criterio que `TwilioNotConnectedError` (`edecan_premium.telephony.for_tenant`, en la extensión comercial externa, que tampoco cae nunca a una cuenta compartida). El LLM **no tiene un stub equivalente ni un paso de plataforma**: no hay "modo silencioso" para el cerebro del asistente, y muchísimo menos una API key ajena pagándolo.
   - **Voz** — `edecan_voice.stubs.StubSTT`/`StubTTS` (offline, determinista: transcripción fija / WAV de silencio). Esto NO es un fallback a una credencial compartida — el stub no le pega a ningún proveedor real ni le cuesta un centavo a nadie —, así que ahí sí basta con degradar en vez de cortar la request. `edecan_voice.registry.get_stt`/`get_tts` (que sí leen `DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` de `Settings`) deliberadamente no se llaman desde `apps/api/edecan_api/routers/voice.py`.

Cualquier fallo leyendo/descifrando la config del tenant (vault caído, JSON corrupto, fila a medio escribir) se trata exactamente igual que "el tenant no conectó nada": se registra con `logger.warning` y se resuelve con el paso 2 de arriba — nunca con una credencial de plataforma.

**Corregido (2026-07-08)**: el worker (`apps/worker/edecan_worker/deps.py::Deps.llm_router_for`) ya tiene el mismo corte que `apps/api` — los jobs asíncronos que llaman al LLM (`generate_content`, `run_automation`, `run_mission`, `memory_consolidate`, `ingest_file`) lanzan `TenantLLMNotConnectedError` (nunca degradan a la config de plataforma) cuando un tenant no conectó nada. Como es un job en cola, no un endpoint HTTP, no hay a quién devolverle un `400`: la excepción se deja propagar hasta el despachador del job (`edecan_worker.main`/`edecan_local.worker_loop`), que la trata como cualquier otro fallo — reintento con backoff, luego DLQ/`status='error'` con el mensaje de la excepción (presentable al tenant) como `last_error`. Los handlers que tienen guardas de "recurso no encontrado"/"desactivado" antes de necesitar el LLM (`ingest_file` para archivos no-imagen, `run_mission`/`run_automation` para misiones/automatizaciones inexistentes o en estado terminal) resuelven el router PEREZOSO, después de esos guardas, para no fallar innecesariamente cuando el LLM ni siquiera hacía falta.

## Orden de resolución (imágenes y búsqueda web) — SIN paso de plataforma

`edecan_creative.providers.get_tenant_image_provider(ctx)` y `edecan_toolkit.research.get_tenant_search_provider(ctx)` resuelven sus proveedores dentro de la propia herramienta. Nunca consultan `IMAGES_API_KEY`/`BRAVE_API_KEY`/`TAVILY_API_KEY` de plataforma para atender a un tenant.

1. **Tenant** — ¿conectó su propia credencial (`connector_accounts` + `TokenVault` para `"images"`/`"search"`)? Si sí, se usa ESA, siempre.
2. **Sin credencial propia** — imágenes usa `StubImageProvider`, claramente marcado como demostración; búsqueda usa `DuckDuckGoSearch`, con resultados reales y sin API key. `get_image_provider(ctx.settings)`/`get_search_provider(ctx.settings)` se conservan para scripts legacy de un solo operador, pero no participan en el flujo multi-tenant.

Cualquier fallo resolviendo la credencial se trata con el paso 2: nunca rompe el chat y nunca cae a una credencial compartida.

A diferencia de LLM/voz (resueltos una vez por request en `apps/api/edecan_api/deps.py`/`routers/voice.py`, ver arriba), esta resolución corre POR CADA llamada a la tool, leyendo `ctx.session`/`ctx.vault`/`ctx.tenant_id` directamente — funciona igual en `apps/api` (chat interactivo) y en cualquier otro lugar que arme un `ToolContext` con esos tres campos poblados; en despliegues donde `edecan_creative`/`edecan_toolkit` no están instalados (p. ej. el worker de automatizaciones hoy, que no depende de esos paquetes) las tools `generar_imagen`/`buscar_web`/`comparar_precios` simplemente no están disponibles, así que la pregunta no aplica ahí.

## Endpoints (`/v1/credentials`, Bearer access)

Referencia completa con todos los campos en [`api.md`](./api.md#rutas-v3-credenciales-bring-your-own-escritorio-skills-y-nuevos-conectores) — acá solo los ejemplos `curl` de uso diario. Sustituye `$TOKEN` por tu access token (`POST /v1/auth/login`) y `$API` por la URL base de tu API (`http://localhost:8000` en desarrollo).

### Ver qué hay conectado

```bash
curl -s "$API/v1/credentials" -H "Authorization: Bearer $TOKEN"
```

```json
{
  "llm": {"kind": "anthropic", "model_principal": null, "model_rapido": null, "base_url": null, "masked": "…s9Kq"},
  "voice_stt": {"provider": "deepgram", "masked": "…9f2a"},
  "voice_tts": null,
  "images": null,
  "search": {"provider": "brave", "masked": "…7f3a"}
}
```

Nunca devuelve el secreto completo — `"masked"` es siempre `"…" + últimos 4 caracteres`, o `null` si no hay nada guardado (o el proveedor no usa API key, como Polly).

### Conectar un LLM (API key normal — Anthropic)

```bash
curl -s -X PUT "$API/v1/credentials/llm" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"kind": "anthropic", "api_key": "sk-ant-...", "validate": true}'
```

`204 No Content` si la key valida contra la API real de Anthropic; `400` con el detalle exacto del proveedor si no (p. ej. `"Anthropic rechazó la credencial (status 401): ..."`). `validate: false` guarda sin pegarle a la red (útil para scripts/migraciones, nunca el default de la UI).

Mismo endpoint para los demás `kind` (`openai_compat`, `vertex`, y — solo con la app de escritorio corriendo en `EDECAN_LOCAL_MODE=true` — `claude_cli`, `codex_cli`, `ollama`): ver la tabla completa de proveedores, qué exige cada uno y la auto-detección de un clic para los locales en [`proveedores-llm.md`](./proveedores-llm.md).

### Conectar voz web (Deepgram + ElevenLabs)

```bash
curl -s -X PUT "$API/v1/credentials/voice/stt" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider": "deepgram", "api_key": "...", "validate": true}'

curl -s -X PUT "$API/v1/credentials/voice/tts" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider": "elevenlabs", "api_key": "...", "voice_id": "21m00Tcm4TlvDq8ikWAM", "validate": true}'
```

`provider` de TTS también acepta `"polly"` (usa la cadena de credenciales AWS *ambiente* del proceso que corre el backend en vez de una API key propia — `voice_id` opcional, por defecto `"Lupe"`; `validate` no dispara ningún ping de red para ese proveedor porque no hay una key que probar). **`"polly"` SOLO se acepta con la app de escritorio corriendo en `EDECAN_LOCAL_MODE=true`** — igual que `claude_cli`/`codex_cli`/`ollama` más abajo, y por el mismo motivo: esa identidad AWS ambiente solo pertenece de verdad al tenant en modo single-user; en un servidor hospedado/compartido, dos tenants que eligieran `"polly"` compartirían la MISMA cuenta de AWS. Fuera de ese modo, `PUT` responde `400`.

### Conectar imágenes y búsqueda web

```bash
curl -s -X PUT "$API/v1/credentials/images" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"base_url": "https://api.openai.com/v1", "api_key": "sk-...", "model": "gpt-image-2", "validate": true}'

curl -s -X PUT "$API/v1/credentials/search" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"provider": "brave", "api_key": "...", "validate": true}'
```

`base_url` de imágenes acepta cualquier endpoint compatible con `POST {base_url}/images/generations` (contrato de OpenAI Images) — igual criterio que `kind: "openai_compat"` de LLM, solo cambia `base_url`/`model`. `provider` de búsqueda acepta `"brave"` o `"tavily"`. `validate: true` (default) hace UNA llamada real y liviana antes de guardar — para imágenes, `GET {base_url}/models`; para búsqueda, una búsqueda real con `k=1` (ninguno de los dos proveedores documenta un endpoint de solo-validación separado del uso real, a diferencia de Anthropic/Deepgram/ElevenLabs).

### Desconectar

```bash
curl -s -X DELETE "$API/v1/credentials/llm" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$API/v1/credentials/voice/stt" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$API/v1/credentials/voice/tts" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$API/v1/credentials/images" -H "Authorization: Bearer $TOKEN"
curl -s -X DELETE "$API/v1/credentials/search" -H "Authorization: Bearer $TOKEN"
```

`204 No Content`, idempotente. El tenant vuelve al comportamiento sin credencial propia: error explícito para LLM, stub para voz/imágenes y DuckDuckGo real para búsqueda; nunca una credencial de plataforma.

## Dónde saca cada cliente su propia credencial

| Servicio | Consigue tu API key acá | Notas |
|---|---|---|
| Anthropic (Claude, API directa) | [console.anthropic.com](https://console.anthropic.com/settings/keys) | `kind: "anthropic"`. Alternativa sin API key: Claude CLI ya autenticado en tu máquina — ver [`proveedores-llm.md`](./proveedores-llm.md). |
| OpenAI / cualquier endpoint OpenAI-compatible | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `kind: "openai_compat"` — también sirve para Groq, Together.ai, un LLM local expuesto como `/chat/completions`, etc.; solo cambia `base_url`. |
| Google AI Studio (Gemini/Vertex, camino simple) | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | `kind: "vertex"` con una sola API key — el camino recomendado por defecto. El flujo avanzado de proyecto GCP + service account es un paso aparte, ver [`proveedores-llm.md`](./proveedores-llm.md). |
| Deepgram (transcripción de voz) | [console.deepgram.com](https://console.deepgram.com/) → Settings → API Keys | `voice_stt`, `provider: "deepgram"`. Tiene crédito gratis inicial para probar. |
| ElevenLabs (síntesis de voz) | [elevenlabs.io](https://elevenlabs.io/app/settings/api-keys) → Settings → API Keys | `voice_tts`, `provider: "elevenlabs"`. `voice_id` se elige en su librería de voces (Voice Library) — copia el ID, no el nombre. |
| AWS Polly (síntesis de voz, alternativa) | Tu propia cuenta AWS (IAM) | `voice_tts`, `provider: "polly"` — no usa una API key de Edecán: la cadena de credenciales AWS estándar del cliente (variables de entorno, perfil, o rol IAM) debe estar disponible donde corra el backend. **Solo disponible con `EDECAN_LOCAL_MODE=true`** (app de escritorio, single-user) — en un despliegue hospedado/compartido esa identidad de proceso no es de un tenant en particular, así que se rechaza; usa ElevenLabs ahí. |
| OpenAI (imágenes) / cualquier endpoint compatible | [platform.openai.com/api-keys](https://platform.openai.com/api-keys) | `images`, mismo `base_url`/`api_key` que un `openai_compat` de LLM — solo cambia el `model` (uno con capacidad de generación de imágenes). |
| Brave Search API (búsqueda web) | [brave.com/search/api](https://brave.com/search/api/) → API Keys | `search`, `provider: "brave"`. Tiene un plan gratuito con límite mensual. |
| Tavily (búsqueda web, alternativa) | [app.tavily.com](https://app.tavily.com/home) | `search`, `provider: "tavily"`. También tiene un plan gratuito con límite mensual. |

## Seguridad

- **Nunca la key completa por API**: `GET /v1/credentials` solo devuelve `masked`. El backend tampoco la loguea nunca (`ARCHITECTURE.md` §0, regla dura "cero secretos reales/tokens reales en logs").
- **Cifrado por tenant**: cada tenant tiene su propia data key AES-256 (`tenant_keys`), envuelta con `LocalKeyProvider` (Fernet + `LOCAL_MASTER_KEY`, dev/self-host) o `KmsKeyProvider` (AWS KMS, prod) — nunca una clave compartida entre tenants.
- **"Pegar y validar" es fail-closed**: si `validate: true` (el default) y el proveedor rechaza la credencial, no se guarda nada — el tenant se entera al toque, con el mismo error que dio el proveedor, en vez de descubrirlo horas después en un job fallido.
- **Aislamiento multi-tenant**: igual que el resto del esquema, `connector_accounts`/`oauth_tokens` tienen Row-Level Security por `tenant_id` (`ARCHITECTURE.md` §2) — un tenant nunca puede leer ni de casualidad la credencial de otro.

## Auditoría v5 (2026-07)

`fase v5` ("Barrido de seguridad bring-your-own") releyó, línea por línea,
TODOS los proveedores bring-your-own del repo buscando el mismo patrón de
fuga de credencial que se encontró y corrigió en v4
(`packages/llm/edecan_llm/router.py::_build_provider_from_config`, ver
`docs/seguridad-modelo-amenazas.md` "v4 completado, v5 lanzado" hallazgo #1): un campo
de credencial/endpoint del tenant que llega vacío/None y se completa en
silencio con un valor de `self._settings`/`os.environ`/plataforma. **Resultado:
cero hallazgos nuevos** — los seis proveedores corregidos en v3/v4 (LLM, voz,
imágenes, búsqueda) siguen intactos, y los proveedores más nuevos (mensajería,
Twilio/premium, ads, smarthome, conectores OAuth) ya seguían el patrón
correcto desde que se construyeron. Se agregaron regresiones anti-fuga
dedicadas en cada paquete (con nombres estilo
`test_X_sin_credencial_no_filtra_la_de_plataforma`/`test_X_request_real_...`,
capturando la request HTTP real con `respx`/`httpx.MockTransport` cuando
aplica) para que un futuro cambio que reintroduzca el patrón falle en CI en
vez de descubrirse en producción.

**Corregido (2026-07-09)**: el veredicto "Limpio" de Voz web (fila de abajo)
no cubría el patrón completo. La metodología de v5 buscaba
`self._settings`/`os.environ` alimentando un campo de credencial — pero
`provider="polly"` no tiene NINGÚN campo de credencial de tenant, así que ese
grep nunca lo iba a encontrar: `edecan_voice.polly.PollyTTS` se autenticaba
con la cadena de credenciales AWS *ambiente* del proceso (`aioboto3.Session()`
sin argumentos), la MISMA identidad que ya usa la plataforma para S3/SQS
(`.env.example`, `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) — en un
despliegue hosted compartido, dos tenants que eligieran `"polly"`
terminaban sintetizando voz con la identidad AWS del OPERADOR, sin
aislamiento ni distinción entre ellos: exactamente la "llave compartida de
plataforma" que este documento prohíbe, solo que un nivel más abajo de lo
que grepeaba la auditoría (dentro de `aioboto3`, no en código propio del
repo). Corregido con el mismo criterio que `claude_cli`/`codex_cli`/`ollama`
(`_LOCAL_ONLY_LLM_KINDS`): `PUT /v1/credentials/voice/tts` con
`provider="polly"` y las dos resoluciones tenant→proveedor (`edecan_voice.
tenant.resolver_tts_del_tenant`, `apps/api/edecan_api/routers/voice.py::
_tts_para_tenant`) ahora exigen `EDECAN_LOCAL_MODE=true` — fuera de eso,
`PUT` responde `400` y la resolución cae a `StubTTS`, nunca construye
`PollyTTS` con la identidad del proceso. `edecan_voice.polly.PollyTTS` en sí
suma una segunda capa de defensa: el parámetro `allow_ambient_credentials`
(default `False`) exige un opt-in explícito para heredar la sesión AWS
ambiente — sin `session=` (tests) ni ese flag, el constructor lanza
`ValueError` en vez de heredarla en silencio, así que un caller nuevo que
olvide el chequeo de `EDECAN_LOCAL_MODE` falla ruidosamente en vez de
filtrar la credencial. Ver `packages/voice/edecan_voice/polly.py`,
`packages/voice/tests/test_voice_byo.py` y `apps/api/tests/test_voice_byo.py`
(tests nuevos/actualizados que pinnean el comportamiento correcto).

**Nota de alcance (2026-07-09)**: el "TODOS los proveedores bring-your-own
del repo" de más arriba describe el encargo real de `fase v5` — una lista
fija de dominios, no un barrido dinámico de "lo que exista en el repo hoy".
Dos dominios bring-your-own se construyeron en paralelo a este barrido y no
estaban en esa lista: `packages/travel` (Amadeus/AfterShip, dueño
`fase v5`) y notificaciones push (APNs/FCM, dueño `fase v5`). No es que
`fase v5` los revisara y salieran limpios como el resto de filas — el
barrido nunca los tocó. Verificados por separado (fuera de `fase v5`):
ambos siguen el mismo patrón bring-your-own correcto desde su primer
commit — ver las dos filas nuevas antes de `ToolContext` en la tabla de
abajo.

| Proveedor | Archivo(s) auditados | Veredicto |
|---|---|---|
| LLM | `packages/llm/edecan_llm/router.py`, `detect.py`, `apps/worker/edecan_worker/deps.py`, `apps/api/edecan_api/deps.py`, `routers/credentials.py` | **Limpio** — fix de v4 intacto (`_build_provider_from_config` sigue sin leer `self._settings` para `api_key`/`base_url` de tenant); `_ping_*` de `credentials.py` usan solo la credencial recién pegada del payload; `TenantLLMNotConnectedError` fail-closed intacto en el worker. Test nuevo: `apps/api/tests/test_credentials_byo.py` (request real con centinela). |
| Voz web (STT/TTS) | `packages/voice/edecan_voice/{registry,pipeline,deepgram,elevenlabs,polly,stubs,base}.py`, `apps/api/edecan_api/routers/voice.py` | **Corregido 2026-07-09** (ver nota arriba) — Deepgram/ElevenLabs: `get_stt`/`get_tts` (nivel-plataforma, leen `settings`) confirmado que NINGÚN caller de `apps/api`/`apps/worker`/`premium` los invoca; `_stt_para_tenant`/`_tts_para_tenant` siguen "tenant → stub" sin paso intermedio. Polly SÍ tenía el hueco: sin campo de credencial de tenant, se autenticaba con la identidad AWS ambiente del proceso (compartida entre tenants en hosted) — ahora exige `EDECAN_LOCAL_MODE=true` para construirse en absoluto, igual que `claude_cli`/`codex_cli`/`ollama`. `pipeline.voice_turn` recibe proveedores ya resueltos (sin callers hoy, solo utilidad para un endpoint futuro). Tests: `packages/voice/tests/test_voice_byo.py`, `apps/api/tests/test_voice_byo.py`, `packages/voice/tests/test_voice_tenant.py`. |
| Imágenes | `packages/creative/edecan_creative/providers.py` | **Limpio** — corregido en v4, reverificado; `get_tenant_image_provider` cae SIEMPRE a `StubImageProvider`, nunca a `get_image_provider(ctx.settings)`. Test nuevo: `packages/creative/tests/test_byo_imagenes.py` (request real con centinela + aislamiento entre dos tenants). |
| Búsqueda web | `packages/toolkit/edecan_toolkit/research.py` + `packages/browser/edecan_browser/tools.py` (solo lectura, ajeno) | **Limpio** — corregido en v4 (fase v4); `CompararPreciosTool`/`BuscarWebTool` usan `get_tenant_search_provider(ctx)`. Test nuevo: `packages/toolkit/tests/test_byo_busqueda.py`. |
| Mensajería (Telegram/Discord/Slack/WhatsApp) | `packages/messaging/edecan_messaging/{_creds,clients,whatsapp,tools}.py` | **Limpio** — `resolver_credenciales` ni siquiera acepta/lee `ctx.settings`; sin cuenta conectada lanza `MessagingNotConnectedError`, nunca degrada. Test nuevo: `packages/messaging/tests/test_byo_mensajeria.py` (con un `ctx.settings` "veneno" que revienta ante cualquier acceso). |
| Telefonía Twilio + campañas | Extensión comercial externa `edecan_premium` | **Limpio según la suite privada de la extensión** — `for_tenant`/`TwilioTenantClient` no aceptan `settings` en su firma; sin rama de error/retry que reintente con `TWILIO_*` de entorno. La regresión BYO incluye variables de entorno `TWILIO_ACCOUNT_SID`/`TWILIO_AUTH_TOKEN` centinela, ignoradas. |
| Conectores OAuth (Google/Microsoft/Meta/X/YouTube/Slack) | `packages/connectors/**` | **Limpio** — `refresh(bundle, http)` siempre usa el `bundle` recibido como parámetro (nunca un token "default"); `CONNECTORS` es singleton por proceso pero sin NINGÚN atributo de instancia (`vars(...) == {}`), así que no hay estado que mezclar entre tenants; `apps/worker/.../sync_connector.py` reconfirmado: lee/escribe el vault por `(tenant_id, connector_account_id)` exacto de cada fila. Test nuevo: `packages/connectors/tests/test_byo_refresh.py`. |
| Ads (Meta) | `packages/ads/edecan_ads/{providers,tools}.py` | **Limpio** — patrón "tenant → Stub" ya correcto, SIN nivel intermedio de plataforma (no existe ningún `ADS_*` de plataforma que pudiera usarse). Test nuevo: `packages/ads/tests/test_byo_ads.py` (request real + aislamiento entre tenants). |
| Smarthome (Home Assistant) | `packages/smarthome/edecan_smarthome/{client,tools}.py` | **Limpio** — `base_url`/`token` siempre de `bundle.scopes[0]`/`bundle.access_token`; `ctx.settings` solo se lee para `HOMEASSISTANT_TIMEOUT_SECONDS` (no-secreto). Test nuevo: `packages/smarthome/tests/test_byo_casa.py`. |
| Cotizaciones (CoinGecko) | `packages/commerce/edecan_commerce/quotes.py` | **N/A — nivel-plataforma legítimo**: CoinGecko Simple Price API es pública y no exige ninguna API key; `QUOTES_PROVIDER` es un nombre de proveedor, no una credencial (`ARCHITECTURE.md` §0, "valores NO-secretos"). Test nuevo: `packages/commerce/tests/test_byo_quotes.py`. |
| Viajes (Amadeus/AfterShip) | `packages/travel/edecan_travel/providers.py` | **Limpio — fuera del encargo original de `fase v5`** (paquete nuevo, dueño `fase v5`, construido en paralelo a este barrido, ver nota de alcance arriba); patrón bring-your-own correcto desde el primer commit, calcado de `edecan_ads.providers` — `get_tenant_travel_provider`/`get_tenant_tracking_provider` caen SIEMPRE a `StubTravelProvider`/`StubTrackingProvider`, nunca a `self._settings`/`os.environ` (el módulo no tiene ningún `self._settings` al que caer). Test: `packages/travel/tests/test_providers.py` (`test_get_tenant_travel_provider_nunca_cae_a_una_credencial_de_plataforma`, `test_get_tenant_tracking_provider_nunca_cae_a_una_credencial_de_plataforma`). |
| Notificaciones push (APNs/FCM) | `apps/worker/edecan_worker/push.py` | **Limpio — fuera del encargo original de `fase v5`** (dueño `fase v5`, construido en paralelo a este barrido, ver nota de alcance arriba); no existe NINGÚN campo de push en `edecan_worker.config.Settings` a propósito, así que es estructuralmente imposible que este módulo filtre una credencial que no sea la del propio tenant — `cargar_credenciales_push` lee EXCLUSIVAMENTE del `TokenVault` (`connector_key="push"`). Test: `apps/worker/tests/test_push.py` (`test_cargar_credenciales_push_sin_connector_account_devuelve_none`, `test_cargar_credenciales_push_sesion_lanza_devuelve_none_nunca_revienta`, `test_cargar_credenciales_push_vault_lanza_devuelve_none_nunca_revienta`). |
| `ToolContext` (`ctx.llm`/`ctx.vault`/`ctx.session`) | `apps/api/edecan_api/deps.py` + `routers/conversations.py`/`ads.py`/`mensajes.py`, `apps/worker/edecan_worker/handlers/run_automation.py` | **Limpio** — en `apps/api` los tres campos llegan siempre vía `Depends(get_llm_router)`/`Depends(get_vault)`/`Depends(get_tenant_session)` (resueltos por request, por tenant); en `apps/worker` vía `deps.llm_router_for(tenant_id)`/`deps.vault(session)` con la `session` de ESE job. Ningún constructor de `ToolContext` reutiliza un colaborador de otro tenant. |
| `apps/local` (`EDECAN_LOCAL_MODE`) | `apps/local/edecan_local/worker_loop.py` | **N/A — nivel-plataforma legítimo por diseño**: modo single-user, la config de "plataforma" ES la del propio usuario que instaló la app de escritorio (`ARCHITECTURE.md` §12.f). No se reabre. |
| `packages/vehicles`/`routers/vehiculos.py` | — | **Fuera de alcance** (`ARCHITECTURE.md` §13.e y [`vehiculos.md`](./vehiculos.md)) — no se auditó, por instrucción explícita de no invertir más esfuerzo ahí. |

Detalle completo del barrido (metodología, archivos leídos, y la constancia
de "cero hallazgos" con su razonamiento) en `docs/seguridad-modelo-amenazas.md` sección
"Barrido v5".

## Ver también

- [`api.md`](./api.md) — referencia completa de todos los campos de cada endpoint.
- [`proveedores-llm.md`](./proveedores-llm.md) — los seis proveedores de LLM en detalle (API, CLI local, Ollama, Vertex), auto-detección de un clic.
- [`voz-telefonia.md`](./voz-telefonia.md) — voz web vs. telefonía, y cómo se resuelve el proveedor de voz por tenant.
- [`configuracion.md`](./configuracion.md) — variables de entorno de PLATAFORMA (`ANTHROPIC_API_KEY`, `IMAGES_*`, `SEARCH_PROVIDER`/`BRAVE_API_KEY`/`TAVILY_API_KEY`, etc.): NINGÚN flujo de tenant de este documento las consulta ya (ver "SIN paso de plataforma" arriba) — solo las usa quien opera el servidor directamente (scripts propios, self-host de un solo tenant que llama `get_image_provider(settings)`/`get_search_provider(settings)` fuera del contexto multi-tenant, tests de cada paquete).
- [`ARCHITECTURE.md`](../ARCHITECTURE.md) §10.4 (TokenVault) y §12.b/§12.c (contratos v3 de este paquete de trabajo).
