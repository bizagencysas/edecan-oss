# Referencia de la API HTTP (`edecan_api`)

Rutas pinned en `ARCHITECTURE.md` §10.12. Base URL por defecto en desarrollo: `http://localhost:8000` (`PUBLIC_BASE_URL`). Todos los cuerpos y respuestas son JSON salvo donde se indique lo contrario (SSE, audio, multipart).

## Autenticación

La API usa **JWT HS256** (`JWT_SECRET`), con claims
`{sub, ten, plan, typ, iat, exp, jti, sid}`:

- `sub` — id del usuario.
- `ten` — id del tenant activo.
- `plan` — `plan_key` firmado por el servidor. Los flags se derivan del catálogo server-side, no del cliente; al renovar sesión se releen membresía, estado del tenant y plan actual desde PostgreSQL. Un access token puede conservar el plan anterior como máximo durante sus 30 minutos de vida.
- `typ` — `"access"` (vida 30 minutos) o `"refresh"` (vida 30 días).
- `iat` / `exp` — emisión y expiración estándar.
- `jti` — id único del token; cada refresh se puede usar una sola vez.
- `sid` — id estable de la sesión durante sus rotaciones.

Las rutas protegidas esperan `Authorization: Bearer <access_token>`. En las tablas de abajo, la columna **Auth** usa estos valores:

| Valor | Significado |
|---|---|
| Ninguna | Pública, sin token. |
| Bearer (access) | Requiere JWT de acceso válido. |
| Bearer (access) + superadmin | Requiere además `users.is_superadmin = true`. |
| Firma Twilio | Valida el header `X-Twilio-Signature` en vez de JWT (ver [`voz-telefonia.md`](./voz-telefonia.md)). |
| Firma Stripe | Valida la firma del webhook de Stripe (`STRIPE_WEBHOOK_SECRET`) en vez de JWT. |

## Salud

### `GET /healthz`

Auth: Ninguna. Liveness: confirma que el proceso responde, sin tocar dependencias.

```json
{"status": "ok"}
```

### `GET /readyz`

Auth: Ninguna. Readiness: ejecuta `SELECT 1` en PostgreSQL y `PING` en Redis.
Devuelve `200 {"status":"ok"}` o `503 {"status":"unavailable"}`; úsalo para
decidir si la instancia puede recibir tráfico.

## Autenticación y sesión

### `POST /v1/auth/register`

Auth: Ninguna. Crea el tenant, el usuario `owner`, una `persona` con los defaults de `PersonaConfig` y devuelve tokens.

Body:

```json
{"email": "tu@correo.com", "password": "una-contraseña-fuerte", "tenant_name": "Mi Empresa"}
```

Respuesta `201` (`TokenPairOut`):

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer"
}
```

### `POST /v1/auth/login`

Auth: Ninguna.

Body: `{"email": "tu@correo.com", "password": "una-contraseña-fuerte", "totp_code": "123456"}` — misma forma de respuesta que `register` (sin recrear tenant). `totp_code` es opcional en el schema, pero **obligatorio** (y verificado) si la cuenta tiene 2FA activado (`users.totp_secret` no nulo); si falta o es inválido, responde `401`.

### `POST /v1/auth/refresh`

Auth: Ninguna directa — el refresh token no va en el header `Authorization` sino en el body; `decode_token` exige que sea un JWT válido con `typ: "refresh"`.

Body: `{"refresh_token": "eyJhbGciOiJIUzI1NiIs...", "totp_code": "123456"}`. Respuesta: nuevo par `{"access_token", "refresh_token", "token_type": "bearer"}`. Igual que en `/login`, `totp_code` es **obligatorio** (y verificado) si la cuenta tiene 2FA activado. La API relee usuario, tenant, membresía y plan, y consume el refresh de forma atómica en Redis: reutilizar el token anterior responde `401`.

### `POST /v1/auth/logout`

Auth: refresh token en el body: `{"refresh_token":"..."}`. Revoca la credencial
server-side y responde `{"revoked":true}` de forma idempotente.

`register`, `login`, `refresh` y `logout` tienen además un límite configurable
por ruta, IP e identidad hasheada; al excederlo responden `429` con `Retry-After`.

### `POST /v1/auth/totp/enable`

Auth: Bearer (access). Genera un secreto TOTP para el usuario. Respuesta: `{"secret": "JBSWY3DPEHPK3PXP", "provisioning_uri": "otpauth://totp/Edecan:tu@correo.com?secret=..."}`.

### `POST /v1/auth/totp/verify`

Auth: Bearer (access). Body: `{"code": "123456"}`. Confirma el código y activa 2FA para el usuario. Respuesta: `{"verified": true}`.

### `POST /v1/auth/totp/disable`

Auth: Bearer (access). Body: `{"password": "una-contraseña-fuerte"}`. Apaga 2FA para la cuenta — re-exige la **contraseña**, no un código TOTP (es justo lo que el usuario puede haber perdido junto con el dispositivo): es la única ruta de recuperación ante pérdida del dispositivo/app TOTP, ya que `/login` y `/refresh` exigen `totp_code` de forma incondicional en cuanto `users.totp_secret` queda seteado. Responde `404` si el usuario no existe, `400` si la cuenta no tiene TOTP habilitado, `401` si la contraseña es incorrecta. Respuesta exitosa: `{"disabled": true}`.

## Perfil y persona

### `GET /v1/me`

Auth: Bearer (access).

```json
{
  "user": {"id": "8b1c...", "email": "tu@correo.com", "is_superadmin": false, "created_at": "2026-07-01T10:00:00Z"},
  "tenant": {"id": "3fa2...", "name": "Mi Empresa", "slug": "mi-empresa", "plan_key": "hosted_pro", "status": "active", "created_at": "2026-07-01T10:00:00Z"},
  "flags": {
    "voice.web": true,
    "voice.telephony": true,
    "connectors.social": true,
    "campaigns": false,
    "companion": true,
    "models.premium": true,
    "limits.messages_per_day": 600,
    "limits.voice_minutes_month": 300,
    "limits.storage_mb": 10240,
    "limits.phone_numbers": 1,
    "limits.seats": 1
  }
}
```

### `GET /v1/persona`

Auth: Bearer (access). Devuelve la `PersonaConfig` vigente del tenant/usuario (ver [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md)).

```json
{
  "nombre_asistente": "Edecán",
  "idioma": "es",
  "tono": "cálido y profesional",
  "formalidad": 1,
  "emojis": false,
  "instrucciones": "",
  "rasgos": [],
  "memoria_activada": true,
  "voice_id": null,
  "estilo_relacion": "profesional",
  "adulto_confirmado": false,
  "consentimiento_romantico": false
}
```

### `PUT /v1/persona`

Auth: Bearer (access). Body: un `PersonaConfig` completo o parcial. Respuesta: la `PersonaConfig` resultante, con la misma forma que `GET`.

`estilo_relacion` acepta `profesional`, `coach`, `amigo` o `romantico`. Para
activar `romantico`, la misma petición debe enviar `adulto_confirmado: true` y
`consentimiento_romantico: true`. Elegir cualquier otro estilo limpia ambos
indicadores. Consulta los límites de producto en
[`estilos-de-acompanamiento.md`](./estilos-de-acompanamiento.md).

### `GET /v1/persona/preview`

Auth: Bearer (access). Renderiza el system prompt real que usaría el agente con la persona actual, sin iniciar una conversación — útil para la UI de "nivel Dios" al editar tono/instrucciones.

```json
{"system_prompt": "Eres Edecán, el asistente personal de ...\n\nTono: cálido y profesional. Trátalo de tú.\n\n[Instrucciones del usuario]\n(vacío)\n..."}
```

## Conversaciones y chat (SSE)

### `GET /v1/conversations`

Auth: Bearer (access). Lista las conversaciones del usuario, más recientes primero.

```json
[{"id": "c1a0...", "title": "Planear el viaje a Bogotá", "channel": "web", "created_at": "2026-07-01T10:00:00Z"}]
```

### `POST /v1/conversations`

Auth: Bearer (access). Body opcional: `{"title": "Nueva conversación"}`. Respuesta `201`: el objeto de conversación creado.

### `GET /v1/conversations/{id}`

Auth: Bearer (access). Devuelve la conversación con su historial de `messages` y
`pending_confirmation`. Este último vale `null` normalmente; si el turno se detuvo
en una acción peligrosa contiene exactamente la vista pública de
`confirmation.required` (`tool_call_id`, `name`, `args`) para reconstruir la tarjeta
después de reiniciar un cliente. Nunca incluye `pending_turn` ni el historial interno
del agente y se resuelve con claves Redis aisladas por tenant + conversación.

### `DELETE /v1/conversations/{id}`

Auth: Bearer (access). `204 No Content`.

### `POST /v1/conversations/{id}/messages` — turno del agente (SSE)

Auth: Bearer (access). Body: `{"text": "¿Qué tengo agendado mañana?"}` (`ChatMessageIn`). Antes de abrir el stream, revisa la cuota diaria del plan (`limits.messages_per_day` sobre `usage_events` de hoy, `-1` ilimitado): si ya se agotó, responde una `429` normal (JSON, no SSE — el chequeo corre antes de construir el `StreamingResponse`), nunca un evento `error` dentro del stream.

Los clientes pueden enviar opcionalmente `Idempotency-Key: <UUID>`. La clave queda
aislada por tenant + conversación y vive el tiempo configurado en
`CHAT_IDEMPOTENCY_TTL_SECONDS` (24 h por defecto). El primer request se reclama de
forma atómica antes de persistir el mensaje; mientras está `in_flight`, otro request
con la misma clave y body recibe `409` + `Retry-After: 1`. Al quedar `completed`, un
reintento devuelve el flujo SSE exacto sin insertar otro mensaje, consumir cuota ni
volver a ejecutar herramientas; `Idempotency-Replayed` indica `false` en el original
y `true` en el replay. Reutilizar la clave con otro body responde `409` y una clave
que no sea UUID responde `422`. Para hacer seguro el replay aun si la conexión cae,
el productor del turno queda desacoplado del socket y conserva el replay completo;
los clientes antiguos, sin cabecera, conservan el streaming incremental en vivo sin
cambios.

Respuesta: `Content-Type: text/event-stream`. Cada evento SSE tiene un `event:` (uno de los 6 nombres pinned) y un `data:` con el JSON del `AgentEvent` correspondiente (`edecan_schemas.chat`, ver `ARCHITECTURE.md` §10.7):

| `event:` | Variante de `AgentEvent` | `data:` (forma) |
|---|---|---|
| `message.delta` | `TextDeltaEvent` | `{"type": "text_delta", "text": "Mañana tienes..."}` |
| `tool.start` | `ToolStartEvent` | `{"type": "tool_start", "name": "agenda_eventos", "args": {"dia": "2026-07-08"}}` |
| `tool.progress` | `ToolProgressEvent` | `{"type": "tool_progress", "tool_call_id": "call_1", "name": "acceder_codigo_local", "elapsed_seconds": 12, "message": "Edecán sigue trabajando"}` |
| `tool.end` | `ToolEndEvent` | `{"type": "tool_end", "name": "delegar_mision", "result_preview": "Misión creada", "mission_id": "…"}` |
| `confirmation.required` | `ConfirmationRequiredEvent` | `{"type": "confirmation_required", "tool_call_id": "call_abc123", "name": "enviar_correo", "args": {"para": "..."}}` |
| `message.done` | `DoneEvent` | `{"type": "done", "usage": {"input_tokens": 812, "output_tokens": 143}}` |
| `error` | `ErrorEvent` | `{"type": "error", "message": "El proveedor LLM no respondió a tiempo"}` |

Ejemplo de stream crudo:

```
event: message.delta
data: {"type":"text_delta","text":"Mañana "}

event: message.delta
data: {"type":"text_delta","text":"tienes dos eventos: "}

event: tool.start
data: {"type":"tool_start","name":"agenda_eventos","args":{"dia":"2026-07-08"}}

event: tool.end
data: {"type":"tool_end","name":"delegar_mision","result_preview":"Misión creada","mission_id":"c0d9…"}

event: message.done
data: {"type":"done","usage":{"input_tokens":812,"output_tokens":143}}
```

### `GET /v1/conversations/{id}/message-attempts/{idempotency_key}`

Auth: Bearer (access). Recupera un turno idempotente sin volver a enviar su texto
original. Está diseñado para iOS y Android: el sistema operativo puede suspender la
app y cerrar el SSE, pero Edecán continúa trabajando en el host. El teléfono
persiste únicamente la conversación y la UUID del intento, nunca el prompt ni una
credencial.

- `202` + `Retry-After: 1` + `{"status":"in_flight"}`: el turno sigue trabajando.
- `200 text/event-stream`: replay exacto del turno terminado, con
  `Idempotency-Replayed: true`.
- `404`: conversación ajena, intento desconocido o replay ya expirado.
- `409`: estado almacenado inválido; se falla cerrado.

La consulta siempre envía `Cache-Control: no-store` y queda aislada por usuario,
tenant, conversación e intento.

El loop del agente corre como máximo **8 iteraciones** de tool-use por turno. Si una herramienta marcada `dangerous=True` (p. ej. `enviar_correo`, `publicar_social` o, en la extensión comercial externa `edecan_premium`, `llamar_contacto`, `enviar_sms`, `lanzar_campana`) no está pre-aprobada, el turno se detiene emitiendo `confirmation_required` en vez de ejecutarla.

### `POST /v1/conversations/{id}/confirm` — reanudar una herramienta pendiente (SSE)

Auth: Bearer (access). Body: `{"tool_call_id": "call_abc123", "approved": true}`.

Si `approved` es `true`, el servidor ejecuta la herramienta DIRECTO — **sin volver a llamar al LLM** — en vez de reconstruir el turno: la lee de Redis por `tool_call_id` (TTL 15 min, de un solo uso; guardada ahí cuando el turno original se detuvo en `confirmation_required`). Una llamada nueva al LLM acuñaría un `tool_call_id` distinto que jamás coincidiría con el aprobado. Si no hay confirmación pendiente para ese `tool_call_id` (expiró o ya se procesó), responde `409`. La respuesta exitosa es otro stream SSE (misma tabla de eventos de arriba) que ejecuta la herramienta y cierra con `message.done`; si `approved` es `false`, el stream termina con `message.done` sin ejecutar nada.

## Memoria

### `GET /v1/memory?q=`

Auth: Bearer (access). Filtro de substring (`SqlRepo.list_memory`, `content ILIKE '%q%'`) sobre `memory_items` del usuario — coincidencia literal insensible a mayúsculas, no búsqueda semántica ni por embeddings. `q` opcional: sin él devuelve los ítems más recientes/importantes. `k` opcional (default 20). La búsqueda semántica que usa el agente en cada turno (`MemoryStore.search` sobre `pgvector`) es un camino de código distinto y no se expone por este endpoint — ver [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md).

```json
[{"id": "m001...", "kind": "preference", "content": "Prefiere reuniones después de las 10am", "importance": 0.8, "source": "conversación"}]
```

### `POST /v1/memory`

Auth: Bearer (access). Body: `{"kind": "fact", "content": "Su hijo se llama Mateo", "importance": 0.9}`. Crea un `memory_item` manualmente (además de los que el worker consolida automáticamente vía el job `memory_consolidate`).

### `DELETE /v1/memory/{id}`

Auth: Bearer (access). Borra un `memory_item` puntual. `204 No Content`. Ver [`personalizacion-nivel-dios.md`](./personalizacion-nivel-dios.md) para cómo borrar **toda** la memoria de un usuario.

## Conectores

### `GET /v1/connectors`

Auth: Bearer (access). Devuelve un array con un elemento por conector registrado (`key`, `display_name` del conector) y sus cuentas ya conectadas del tenant anidadas en `accounts` (no separa "disponibles" de "conectados" en dos listas). El handler concatena cuatro grupos, en este orden: los conectores OAuth de `edecan_connectors.registry.CONNECTORS` (`google`, `microsoft`, `meta`, `x`, `youtube`, `slack` — 6 desde v2/fase v2, ver §10.8), la entrada fija de `twilio` (no vive en `CONNECTORS`; ver `PUT /v1/connectors/twilio/credentials` más abajo), las de los conectores de bot-token sin OAuth (`telegram`, `discord` — v2/fase v2; ver `PUT /v1/connectors/{key}/credentials` más abajo) y, al final, la entrada fija de `whatsapp` (tampoco vive en `CONNECTORS`, v3/fase v3; ver `PUT /v1/connectors/whatsapp/credentials` más abajo): hasta 10 entradas en total.

```json
[
  {
    "key": "google",
    "display_name": "Google (Gmail + Calendar)",
    "accounts": [
      {
        "id": "ca01...",
        "connector_key": "google",
        "external_account_id": "9f2a1b7c3d4e5f60",
        "display_name": "Google (Gmail + Calendar)",
        "status": "active",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly", "..."],
        "created_at": "2026-07-01T10:00:00Z"
      }
    ]
  },
  {"key": "microsoft", "display_name": "Microsoft (Outlook + Calendar)", "accounts": []},
  {"key": "meta", "display_name": "Meta (Facebook Pages e Instagram)", "accounts": []},
  {"key": "x", "display_name": "X", "accounts": []},
  {"key": "youtube", "display_name": "YouTube", "accounts": []},
  {"key": "slack", "display_name": "Slack", "accounts": []},
  {"key": "twilio", "display_name": "Twilio (telefonía)", "accounts": []},
  {"key": "telegram", "display_name": "Telegram", "accounts": []},
  {"key": "discord", "display_name": "Discord", "accounts": []},
  {"key": "whatsapp", "display_name": "WhatsApp Business", "accounts": []}
]
```

### `GET /v1/connectors/{key}/authorize`

Auth: Bearer (access). `key` ∈ `google|microsoft|meta|x|youtube|slack` (`slack`: v2/fase v2 — ver `ARCHITECTURE.md` §10.8 y `docs/roadmap.md`; mismo contrato `Connector`, sale gratis de este flujo genérico). Devuelve la URL de autorización del proveedor, ya armada con el `client_id` de la plataforma, el `redirect_uri` pinned (`{PUBLIC_BASE_URL}/v1/connectors/{key}/callback`) y un `state` firmado.

```json
{"url": "https://accounts.google.com/o/oauth2/v2/auth?client_id=...&redirect_uri=http%3A%2F%2Flocalhost%3A8000%2Fv1%2Fconnectors%2Fgoogle%2Fcallback&state=..."}
```

### `GET /v1/connectors/{key}/callback`

Auth: Ninguna directa — la validez viene del `state` firmado emitido en `authorize`, no de un JWT (el navegador del usuario llega aquí redirigido por el proveedor, sin poder adjuntar `Authorization`). Recibe `code` y `state` como query params, canjea el código por un `TokenBundle` y lo guarda cifrado en el `TokenVault`. Redirige (`302`) de vuelta a `WEB_BASE_URL` con un indicador de éxito/error.

### `PUT /v1/connectors/twilio/credentials`

Auth: Bearer (access). Twilio no es un conector OAuth: a propósito no vive en `edecan_connectors.registry`/`CONNECTORS` (aunque sí aparece listado, añadido a mano, en la respuesta de `GET /v1/connectors`) y no pasa por `authorize`/`callback` — ver `ARCHITECTURE.md` §4 y §10.10. El tenant pega su Account SID + Auth Token + número de teléfono directamente desde el panel.

```json
{"account_sid": "AC00000000000000000000000000000000", "auth_token": "00000000000000000000000000000000", "phone_number": "+525512345678"}
```

Gateado por el plan del tenant (ARCHITECTURE.md §10.13, recalculado server-side desde `plan_key`, nunca confiado del payload): responde `403` si el flag `voice.telephony` del plan es falso (`free_selfhost`, `hosted_basic`), y `429` si conectar este número superaría `limits.phone_numbers` del plan (cuenta las filas `connector_accounts` existentes con `connector_key="twilio"` del tenant). Luego valida formato (`account_sid` = `AC` + 32 hex, `auth_token` = 32 alfanuméricos, `phone_number` en E.164) y responde `400` si no cumple. Además verifica `phone_number` contra la API real de Twilio (`GET .../IncomingPhoneNumbers.json` de la cuenta, `_verify_twilio_phone_ownership`) — hallazgo de auditoría aislamiento-multi-tenant: sin esto, cualquier tenant autenticado podía declararse dueño del número de otro. Responde `400` si Twilio rechaza el Account SID/Auth Token o si el número no aparece entre los suyos, y `502` si Twilio no responde (fail closed: ante cualquier duda no persiste nada). Por último comprueba que ese número no esté ya conectado a otro tenant (`platform_repo.get_connector_account_by_external_id`, sesión sin RLS, respaldado por un índice único parcial global en `connector_accounts` para `connector_key='twilio'` — `packages/db/edecan_db/models.py`), respondiendo `409` si ya está reclamado por otro tenant. Guarda la cuenta bajo el connector key `"twilio"` en el `TokenVault` con la misma convención que lee `edecan_premium` (`TokenBundle.access_token` = Auth Token, `scopes[0]` = Account SID). `204 No Content`.

### `PUT /v1/connectors/{key}/credentials` — bots de mensajería sin OAuth (v2, fase v2)

Auth: Bearer (access). `key` ∈ `telegram|discord` (`BOT_TOKEN_CONNECTOR_KEYS`) — excepción pinned v2 sobre el contrato v1 de este router (`ARCHITECTURE.md` §10.8/§10.12, `docs/roadmap.md`), con el mismo patrón no-OAuth que `PUT /v1/connectors/twilio/credentials` de arriba: ni Telegram ni Discord tienen OAuth público, así que cada tenant crea su propio bot (BotFather / Discord Developer Portal) y pega el token del bot directamente. `404` si `key` no es `telegram` ni `discord` (incluye `twilio`, que sigue resolviendo por su ruta fija de arriba, declarada antes en el módulo).

```json
{"bot_token": "123456789:AAHtsOm5POK_bl2ZzP1zN1Y1YRXhSHwWMTk"}
```

A diferencia de Twilio, esta ruta **solo valida formato** (no vacío, longitud mínima razonable): no hay una API de "propiedad" barata contra la que verificar un token de bot antes de guardarlo — la validez real se confirma en el primer envío/lectura desde `edecan_messaging`. `400` si el token viene vacío o es demasiado corto. Guarda la cuenta bajo el connector key correspondiente (`"telegram"`/`"discord"`) en el `TokenVault`, con `TokenBundle.access_token` = el bot token y `scopes` vacío. `204 No Content`. Ver [`mensajeria.md`](./mensajeria.md) para el resto de rutas de uso (`enviar_mensaje`/`leer_mensajes`) y por qué Slack (OAuth) no necesita esta ruta.

### `DELETE /v1/connectors/{key}/{account_id}`

Auth: Bearer (access). Revoca/borra la cuenta conectada y su `TokenBundle` del vault. `204 No Content`.

Ver [`conectores.md`](./conectores.md) para el detalle de scopes y cómo registrar cada app OAuth.

## Archivos

### `POST /v1/files`

Auth: Bearer (access). `multipart/form-data` con el archivo. Primero aplica el tope duro `MAX_UPLOAD_BYTES` sin copiar el cuerpo completo a RAM (`413` si lo excede), normaliza el nombre y después revisa `limits.storage_mb` del plan (suma de `usage_events` de tipo `storage_bytes` + el tamaño entrante, `-1` ilimitado; `429` si lo superaría). Si pasa ambos controles, lo sube a `s3://$S3_BUCKET/tenants/{tenant_id}/files/{file_id}/{filename}` y encola el job `ingest_file` (worker lo trocea, extrae texto y genera `file_chunks` con embeddings).

```json
{"id": "f001...", "filename": "contrato.pdf", "mime": "application/pdf", "size_bytes": 284213, "status": "uploaded"}
```

### `GET /v1/files`

Auth: Bearer (access). Lista los archivos del usuario con su `status` (`uploaded|processing|ready|error`).

### `GET /v1/files/{id}`

Auth: Bearer (access). Detalle de un archivo puntual.

## Recordatorios, contactos y finanzas (CRUD)

Estos tres recursos siguen el mismo patrón CRUD estándar; todas las rutas requieren Bearer (access).

### `/v1/reminders`

- `GET /v1/reminders` — lista los recordatorios del usuario.
- `POST /v1/reminders` — body: `{"due_at": "2026-07-08T15:00:00Z", "message": "Llamar al contador", "channel": "web", "rrule": null}`.
- `GET /v1/reminders/{id}` — detalle.
- `PUT /v1/reminders/{id}` — actualiza campos (p. ej. `status: "cancelled"`).
- `DELETE /v1/reminders/{id}` — `204`.

Ejemplo de objeto:

```json
{"id": "r001...", "due_at": "2026-07-08T15:00:00Z", "rrule": null, "message": "Llamar al contador", "channel": "web", "status": "pending"}
```

`send_reminder_scan` (job recurrente) busca recordatorios vencidos y encola `send_reminder` por cada uno — ver `ARCHITECTURE.md` §10.11.

### `/v1/contacts`

- `GET /v1/contacts`, `POST /v1/contacts`, `GET /v1/contacts/{id}`, `PUT /v1/contacts/{id}`, `DELETE /v1/contacts/{id}`.

```json
{"id": "ct01...", "nombre": "Ana Torres", "emails": ["ana@empresa.com"], "phones": ["+573001234567"], "empresa": "Empresa SAS", "notas": "Conocida en la feria de mayo", "tags": ["cliente", "prioridad-alta"]}
```

### `/v1/finance/transactions`

- `GET /v1/finance/transactions`, `POST /v1/finance/transactions`, `GET /v1/finance/transactions/{id}`, `PUT /v1/finance/transactions/{id}`, `DELETE /v1/finance/transactions/{id}`.

```json
{"id": "tx01...", "fecha": "2026-07-05", "monto": 152300.00, "moneda": "COP", "categoria": "software", "descripcion": "Suscripción anual", "cuenta": "tarjeta-empresa"}
```

### `GET /v1/finance/summary?mes=YYYY-MM`

Auth: Bearer (access). Resumen tipo "CFO personal" del mes indicado.

```json
{"mes": "2026-07", "ingresos": 8500000.00, "gastos": -3120450.00, "neto": 5379550.00, "num_transacciones": 42, "por_categoria": [{"categoria": "renta", "total": -1800000.00}, {"categoria": "software", "total": -152300.00}, {"categoria": "salario", "total": 8500000.00}]}
```

## Voz web

Resolución de proveedor STT/TTS en dos niveles, sin ningún paso de plataforma (`docs/roadmap.md` "Modelo de credenciales: TODO lo trae el cliente"; corregido en v3 por fase v3, ver `apps/api/edecan_api/routers/voice.py`): **(1) tenant** — si conectó su propia credencial vía `PUT /v1/credentials/voice/stt`/`/tts` (ver arriba, "Rutas v3"), se usa siempre esa; **(2) stub** — si no (o si algo falla leyéndola), `StubSTT`/`StubTTS` (offline, determinista, usado en tests). `edecan_voice.registry.get_stt`/`get_tts` con `VOICE_STT_PROVIDER`/`VOICE_TTS_PROVIDER`/`DEEPGRAM_API_KEY`/`ELEVENLABS_API_KEY` de `Settings` deliberadamente NUNCA se llaman desde acá, para que ningún tenant sin credencial propia reutilice una API key de voz compartida de la plataforma.

### `POST /v1/voice/transcribe`

Auth: Bearer (access). Requiere flag `voice.web` y cuota (`limits.voice_minutes_month`) disponible. Body: audio binario (`multipart/form-data` o el content-type de audio correspondiente).

```json
{"text": "Recuérdame llamar al contador mañana a las tres"}
```

### `POST /v1/voice/speak`

Auth: Bearer (access). Requiere flag `voice.web` y cuota disponible. Body: `{"text": "Listo, lo agendé para mañana a las 3pm."}`. Respuesta: `Content-Type: audio/mpeg` (`audio/wav` únicamente cuando el proveedor resuelto es el `StubTTS` de plataforma/self-host sin ninguna credencial conectada), cuerpo binario — no JSON.

## Companion de escritorio

### `POST /v1/companion/pair-code`

Auth: Bearer (access). Genera un código corto de emparejamiento, guardado en Redis con TTL de 600 segundos.

```json
{"code": "7F3K-9QRT"}
```

### `WS /v1/companion/ws?code=`

Auth: el propio `code` de emparejamiento (de un solo uso, vía query param) hace de autenticación del dispositivo — no lleva JWT. Una vez conectado el WebSocket del companion, la API expone `ConnectionManager.send_command(tenant_id, action, params, timeout=30)` internamente e inyecta `extras["companion"]` en el `ToolContext` del agente, de forma que la herramienta `usar_computadora` pueda pedirle acciones al dispositivo emparejado con un timeout de 30 segundos.

## Uso y administración

### `GET /v1/usage`

Auth: Bearer (access). Uso del mes en curso del tenant vs. los límites de su plan. `usage` viene agregado por `kind` de `usage_events` (`messages`, `voice_seconds`, `storage_bytes`); `limits` y `flags` usan las claves con notación de punto de `edecan_schemas.plans` (`LIMIT_*`/`FLAG_*`, ver §10.13).

```json
{
  "plan_key": "hosted_pro",
  "period_start": "2026-07-01",
  "usage": {"messages": 42.0, "voice_seconds": 1080.0, "storage_bytes": 356515840.0},
  "limits": {
    "limits.messages_per_day": 600,
    "limits.voice_minutes_month": 300,
    "limits.storage_mb": 10240,
    "limits.phone_numbers": 1,
    "limits.seats": 1
  },
  "flags": {
    "voice.web": true,
    "voice.telephony": true,
    "connectors.social": true,
    "campaigns": false,
    "companion": true,
    "models.premium": true
  }
}
```

### `GET /v1/admin/tenants`

Auth: Bearer (access) + superadmin. Lista todos los tenants de la instancia (solo superadmins de la plataforma, típicamente relevante en el tier hosted).

### `GET /v1/admin/usage`

Auth: Bearer (access) + superadmin. Agregado de `usage_events` por tenant/kind, para monitoreo de costos y detección de abuso.

## Facturación (Stripe, capa hospedada)

### `POST /v1/billing/webhook`

Auth: Firma Stripe (`Stripe-Signature` verificada contra `STRIPE_WEBHOOK_SECRET`). Recibe eventos de Stripe (`checkout.session.completed`, `customer.subscription.updated`, etc.) y actualiza `subscriptions`/`tenants.plan_key`.

### `POST /v1/billing/portal`

Auth: Bearer (access). **Placeholder**: todavía no llama a la API de Stripe (el proyecto no ejecuta llamadas de red reales a servicios de pago — ver reglas del repo). Respuesta actual: `{"url": "<WEB_BASE_URL>/app/facturacion?portal=pendiente-configurar-stripe"}`, un enlace de vuelta a la propia app, no al Billing Portal de Stripe. Pendiente: reemplazar el cuerpo por una llamada real a `POST https://api.stripe.com/v1/billing_portal/sessions` y devolver la `url` que retorne Stripe.

## Telefonía OSS (`/v1/phone`)

Estas rutas se montan siempre desde `edecan_api.routers.phone`; no requieren `edecan_premium`. Las rutas autenticadas exigen el flag `voice.telephony`, la cuenta Twilio propia del tenant y, para llamadas salientes, consentimiento `voice` vigente.

| Ruta | Auth | Para qué |
|---|---|---|
| `GET /v1/phone/agent-templates` | Bearer (access) | Lista las personas reutilizables del usuario para llamadas salientes. |
| `POST /v1/phone/agent-templates` | Bearer (access) | Crea una plantilla con `name`, `agent_name`, `persona_prompt`, `default_goal`, `opening_message` e `is_default`. La primera queda predeterminada. |
| `PUT /v1/phone/agent-templates/{id}` | Bearer (access) | Actualiza una plantilla; nunca reescribe llamadas ya preparadas. |
| `DELETE /v1/phone/agent-templates/{id}` | Bearer (access) | Elimina la plantilla y conserva los snapshots históricos de llamadas. |
| `GET /v1/phone/calls` | Bearer (access) | Lista llamadas del usuario, incluida cualquier confirmación pendiente y el resumen estructurado cuando terminó. |
| `GET /v1/phone/calls/{id}` | Bearer (access) | Devuelve llamada, resumen, eventos y transcripción telefónica. |
| `POST /v1/phone/calls/prepare` | Bearer (access) | Body `{"to_e164":"+573001234567","goal":"Confirmar la entrega","agent_template_id":"uuid opcional"}`. Si omite `goal`, usa el de la plantilla elegida/predeterminada. Crea un borrador y snapshot; nunca contacta al proveedor. |
| `POST /v1/phone/calls/{id}/confirm` | Bearer (access) | Body `{"confirmed_destination":true,"confirmed_goal":true,"expected_to_e164":"+573001234567","expected_goal":"Confirmar la entrega"}`. Revalida los valores vistos por el humano, confirma y persiste antes de pedir la llamada a Twilio. |
| `DELETE /v1/phone/calls/{id}` | Bearer (access) | Cancela un borrador que todavía no salió a Twilio. |
| `POST /v1/phone/twilio/incoming` | Firma Twilio | Recibe una llamada en el número conectado y abre un hilo telefónico separado. |
| `POST /v1/phone/twilio/calls/{id}/voice` | Firma Twilio | Saludo y primer `<Gather>` de una llamada saliente. |
| `POST /v1/phone/twilio/calls/{id}/gather` | Firma Twilio | Ejecuta el siguiente turno breve del asistente y devuelve TwiML. |
| `POST /v1/phone/twilio/calls/{id}/status` | Firma Twilio | Actualiza estado y duración sin regresiones; en el primer estado terminal persiste resumen/actividad y encola un push genérico best-effort. |

La herramienta de chat `llamar_contacto` usa el mismo dispatcher transaccional. Al ser peligrosa, el agente presenta la confirmación exacta antes de ejecutarla. La conversación telefónica mantiene nombre, idioma, tono y formalidad, pero no comparte memorias ni instrucciones privadas con el interlocutor externo. `PHONE_MAX_TURNS` (default `8`) limita los turnos. Ver [`agentes-llamadas.md`](./agentes-llamadas.md) y [`voz-telefonia.md`](./voz-telefonia.md#telefonía-oss-funcional).

### `POST /v1/consents`

Auth: Bearer (access). Ruta OSS, montada siempre. Requiere flag `voice.telephony`. Body: `{"phone_e164": "+525512345678", "kind": "voice", "source": "formulario_web"}`. `kind` es `sms` o `voice`; `source` debe describir cómo se obtuvo el consentimiento. La concesión queda auditada y puede consultarse en `consents`.

## Extensión opcional legada (`edecan_premium`)

Si `edecan_premium` está instalado, `create_app()` conserva sus rutas antiguas `/v1/voice/twilio/*` para compatibilidad: SMS, campañas y el WebSocket de Media Streams. Las llamadas OSS nuevas usan `/v1/phone/*`; no se debe configurar un mismo número para apuntar simultáneamente a los dos webhooks de entrada.

### `WS /v1/twilio/media` — Media Streams (beta)

Prefix propio `/v1/twilio` (distinto del `/v1/voice/twilio` de arriba, `ARCHITECTURE.md` §15.h) — WebSocket bidireccional de audio μ-law que reemplaza el ciclo `<Gather>`/`<Say>` síncrono por interrupciones naturales (el llamante puede cortar al bot a mitad de frase). Se monta cuando `edecan_premium` está instalado y queda gateado en runtime por `TWILIO_MEDIA_STREAMS_ENABLED` (`bool`, default `False`): `create_app()` inyecta la configuración real en `app.state`, pero el operador debe habilitar explícitamente el flag y completar la identidad/agente que exige la extensión. Si el flag está apagado o el token no valida, la conexión se cierra. Detalle completo en [`voz-telefonia.md`](./voz-telefonia.md#interrupciones-naturales-beta).

## Rutas v2 (montaje defensivo)

`edecan_api.main.create_app()` monta además, de forma **defensiva**, hasta 8 routers v2 (prefijos pinned en `docs/roadmap.md`): cada uno se importa con `importlib.import_module` dentro de su propio `try/except ImportError`, así que la API sigue arrancando completa sin importar cuántos de los 8 ya aterrizaron en disco (0, algunos o todos). Hoy los 8 existen y están montados (`test_create_app_no_falla_con_los_routers_v2_reales_que_existan_hoy`). Salvo que se indique lo contrario, Auth: Bearer (access); varios grupos exigen además un flag de plan específico (`403` si el plan no lo incluye — mismo criterio de §10.13: los flags se recalculan siempre server-side, nunca se confía en el token).

### `/v1/missions` — misiones multi-agente

Flag de plan: `agents.missions`. Límite: `limits.missions_per_day` (`-1` ilimitado, `0` → `403`, agotado el cupo del día → `429`, solo se chequea al crear).

- `POST /v1/missions {objetivo}` → `201`. Crea la misión en estado `planning` y encola el job `run_mission` — la planificación/ejecución real ocurre async en el worker, nunca en el turno de esta request. `400` si `objetivo` viene vacío.
- `GET /v1/missions` — lista las del usuario, más recientes primero.
- `GET /v1/missions/{id}` — detalle (`mission` + `steps` de `agent_steps`). `404` si no es tuya.
- `GET /v1/missions/{id}/detalle` (fase v6) — observabilidad enriquecida: mismo `mission`, pero cada `step` trae `resultado_truncado` (cap de 2000 caracteres, en vez del `resultado` íntegro que sí da `GET /{id}`), `usage`/`started`/`finished`, más un bloque `agregados` (`tokens_totales_por_tipo`, `pasos_por_status`) calculado en Python sobre esas mismas filas. Mismo flag/aislamiento/`404` que `GET /{id}`. Ver [`agentes.md`](./agentes.md) sección "Observabilidad de misiones".
- `POST /v1/missions/{id}/confirm {approved}` — aprueba o cancela el step que está `waiting_confirmation`. `409` si la misión no tiene ninguna confirmación pendiente. `approved:true` reencola `run_mission` con `resume:true`; `approved:false` cancela la misión y salta los steps pendientes.
- `POST /v1/missions/{id}/cancel` — cancela una misión no terminal. `409` si ya está en `done|error|cancelled`.

### `/v1/automations` — reglas trigger → acción

Flag de plan: `automations.rules` (gatea todo el router). Límite: `limits.automations_active` — `403` (no `429`) al superarlo.

- `POST /v1/automations {nombre, descripcion?, trigger, accion, enabled?}` → `201`. `trigger.kind` ∈ `schedule` (`{rrule}`) | `webhook`. Para `webhook`, el `hook_secret` se genera server-side (`secrets.token_urlsafe`) — el cliente nunca lo propone. `400` si `engine.validate_trigger`/`validate_accion` rechaza el body.
- `GET /v1/automations` / `GET /v1/automations/{id}` — el `trigger` de un webhook siempre viaja redactado a `{"kind": "webhook", "has_secret": true, "hook_url": "..."}`; el `hook_secret` en claro **solo** aparece en la respuesta del `POST`/`PATCH` que lo generó o rotó.
- `PATCH /v1/automations/{id}` — patch parcial; cambiar a `webhook` sobre un trigger que no lo era genera un secreto nuevo (mismo criterio que el `POST`), pero un `PATCH` que no toca el `trigger` nunca rota el secreto existente en silencio.
- `DELETE /v1/automations/{id}` → `204`.
- `POST /v1/automations/{id}/probar` → `202 {"queued": true}`. Encola `run_automation` ya mismo, sin esperar su próxima corrida agendada ni un webhook, y sin alterar `enabled`/`next_run_at`.
- `GET /v1/automations/{id}/runs` — últimas 50 corridas (`automation_runs`): `id`, `status`, `detalle`, `started_at`, `finished_at`.

### `POST /v1/hooks/{automation_id}` — disparo público de un trigger webhook

Auth: **Ninguna** — a diferencia de todo lo demás en este documento, no lleva `Authorization: Bearer`. La autenticación es el secreto por automatización, presentado en el header `X-Hook-Secret` y comparado con `hmac.compare_digest`. Cualquier fallo (id inexistente, secreto incorrecto, trigger que no es `webhook`, o automatización desactivada) responde `404` uniforme — nunca `401`/`403`, para no convertir la URL en un oráculo de enumeración. Rate limit propio de `30` req/min **por automatización** (no por tenant/IP) → `429` si se supera. Éxito: `204`, deja auditoría (`automation.hook_triggered`) y encola `run_automation`.

### `/v1/ide` — IDE embebido sobre el companion de escritorio

Flag de plan: `companion.ide`. Requiere companion emparejado y conectado — `503` si no hay ninguno, `504` si no respondió a tiempo, `422` si el companion rechazó la acción (validación o permiso denegado localmente por el usuario). Este router nunca toca el filesystem del servidor: todo el trabajo ocurre en la máquina del usuario, vía `ConnectionManager.send_command`.

- `GET /v1/ide/status` → `{"connected": bool}`.
- `GET /v1/ide/tree?path=&max_depth=&max_entries=` — árbol recursivo del sandbox.
- `GET /v1/ide/file?path=` / `PUT /v1/ide/file {path, content}` — leer / sobreescribir un archivo completo.
- `POST /v1/ide/edit {path, old_string, new_string, replace_all?}` — edición quirúrgica (reemplaza `old_string`).
- `POST /v1/ide/run {command}` → `{stdout, stderr, exit_code, truncated}`.
- `POST /v1/ide/search {query, path?}` — búsqueda de texto línea por línea en el sandbox.

### `/v1/remote` — vista y control remoto

Flag de plan base: `companion.remote_view` (todo el router). Prototipo P1 de vista (*polling* HTTP, fase v2) + "fase 2" de input real de teclado/mouse (fase v4, sobre el mismo *polling*, no sobre WebRTC — eso sigue siendo diseño, ver [`control-remoto.md`](./control-remoto.md) §5) — detalle completo de los 4 candados de input en ese mismo documento §7bis/§10.

- `POST /v1/remote/sessions {consent: true, kind?: "view"|"control"}` → `201`. `consent` debe ser exactamente `true` (`422` si no). `kind` (default `"view"`, fase v4): `"control"` exige ADEMÁS el flag `companion.remote_input` (`403` si falta). `503` si no hay companion conectado. Crea la sesión en estado `pending`.
- `GET /v1/remote/sessions` / `GET /v1/remote/sessions/{id}` — listar / detalle (`404` si no es tuya).
- `GET /v1/remote/sessions/{id}/frame` → `{image_b64, width, height, seq}`. Pide un screenshot al companion (rate limit ~1 cada `REMOTE_FRAME_MIN_INTERVAL_SECONDS`, default 1s → `429`). `403` si la sesión ya fue denegada (o si el usuario acaba de rechazar la aprobación local en el companion — ese caso además marca la sesión `denied` y audita). `409` si la sesión ya `ended`. `501` si el companion no puede servir capturas (versión vieja, `ide_enabled: false`, o SO distinto de macOS). El primer frame exitoso pasa la sesión de `pending` a iniciada.
- `POST /v1/remote/sessions/{id}/input {tipo: "pointer"|"key", ...}` (fase v4) — reenvía el comando al companion (`input_pointer`/`input_key`) SOLO sobre una sesión `kind="control"` ya `active`. Exige el flag `companion.remote_input` ADEMÁS de `companion.remote_view`. `404` si la sesión no existe; `403` si no es `kind="control"` o ya fue `denied`; `409` si todavía no está `active` o ya `ended`; `429` con rate limit propio (`REMOTE_INPUT_MIN_INTERVAL_SECONDS`, default 50ms — separado del de `frame`); `501` si el companion no soporta input remoto (versión vieja, `remote_input_enabled: false`, o SO distinto de macOS); `503` si el companion se desconectó. Cada comando exige además una aprobación LOCAL en el companion (nunca `auto_approve`) — nunca se audita/persiste el contenido de `texto` en claro.
- `POST /v1/remote/sessions/{id}/end` — termina la sesión (idempotente; solo audita la primera vez).

### `/v1/commerce` — presupuestos, órdenes y holdings

Flag de plan: `commerce.orders`. **Dinero real nunca se mueve solo** (ver [`dinero-real.md`](./dinero-real.md)): las órdenes `kind="trade"` solo se ejecutan contra un broker simulado (`COMMERCE_MODE=paper`, único modo implementado — cualquier otro valor responde `501`); las `kind="payment"` solo generan un `payment_link` placeholder y exigen que el humano lo abra y apruebe manualmente — ningún proveedor de pagos real está conectado. Los drafts de más de 7 días expiran solos (`status: expired`) de forma perezosa en cada lectura.

- `GET /v1/commerce/orders?status=` / `GET /v1/commerce/orders/{id}` — `404` si no es tuya.
- `POST /v1/commerce/orders/{id}/confirm` — `draft → confirmed`, luego ejecuta según `kind` (ver arriba). `409` si la orden no está en `draft`.
- `POST /v1/commerce/orders/{id}/cancel` — `409` si el estado no es `draft`/`confirmed`.
- `GET /v1/commerce/holdings` — solo lectura; las escribe únicamente el broker paper.
- `GET /v1/commerce/budgets` / `PUT /v1/commerce/budgets {categoria, monto_mensual, moneda?}` — estado de presupuestos por categoría (% usado, alerta) y upsert de un presupuesto. `monto_mensual > 0` y `moneda` como código ISO-4217 de 3 letras ya los valida Pydantic (`422`); `400` si `categoria` queda vacía tras recortar espacios.

### `/v1/negocios` — facturación ligera + KPIs

**Sin flag de plan** — disponible en todos los planes (facturar solo consume Postgres y el `S3_BUCKET` propio del tenant, no voz/telefonía/modelos premium).

- `GET /v1/negocios/kpis?mes=YYYY-MM` (default: mes actual UTC) — KPIs de negocio del mes. `422` si `mes` es inválido.
- `GET /v1/negocios/facturas?status=` — lista.
- `POST /v1/negocios/facturas {cliente_nombre, items:[{descripcion, cantidad, precio_unitario}], impuestos_pct?, due_date?, cliente_email?, notas?, moneda?}` → `201`. Calcula totales, asigna numeración, genera el PDF y lo sube a S3; queda en `draft`. `422` en reglas de negocio que Pydantic no puede expresar (p. ej. `cliente_nombre` en blanco tras `.strip()`).
- `GET /v1/negocios/facturas/{id}` — `404` si no existe.
- `POST /v1/negocios/facturas/{id}/estado {status: sent|paid|void}` — `draft → sent → paid`; `void` desde cualquier estado no-`void`. `409` en una transición inválida (`draft` nunca es un destino válido). `404` si la factura no existe.

### `/v1/perfil` — perfil vivo del usuario

**Sin flag de plan adicional.** Ver [`perfil-vivo.md`](./perfil-vivo.md).

```json
{
  "resumen": "Fundador de una startup B2B en Bogotá, le importa mucho el tiempo con su familia.",
  "datos": {
    "identidad": {"nombre_preferido": "Ana", "nombre_completo": "Ana Torres", "pronombres": "ella", "fecha_nacimiento": "", "pais": "Colombia", "ciudad": "Bogotá", "zona_horaria": "America/Bogota", "ocupacion": "Fundadora", "idioma_preferido": "Español", "forma_de_trato": "Cercano y directo", "biografia": "Construye productos B2B."},
    "gustos": [], "proyectos": ["Lanzamiento v2"], "metas": [], "relaciones": [], "empresas": [], "habitos": []
  },
  "version": 3,
  "updated_at": "2026-07-01T10:00:00Z"
}
```

- `GET /v1/perfil` — el perfil actual, o el esqueleto vacío de arriba con `"version": 0` si el usuario todavía no tiene fila (`memory_consolidate` nunca corrió, o corrió sin nada que consolidar).
- `PUT /v1/perfil {resumen?, datos?}` — patch parcial en dos niveles. `datos.identidad` contiene los campos declarados por la persona y también admite patch parcial; la consolidación automática los conserva siempre. Las 6 categorías aprendidas se reemplazan como listas completas. Incrementa `version`.
- `DELETE /v1/perfil` → `204`. Borra la fila y su espejo en `memory_items` (`source: "perfil_vivo"`), para que el perfil deje de inyectarse en turnos futuros.
- `POST /v1/perfil/rebuild` → `202`. Encola el mismo job `memory_consolidate` que corre tras cada turno de chat (no uno especial "solo perfil"); la reconstrucción ocurre async en el worker.

## Rutas v3 (credenciales bring-your-own, escritorio, skills y nuevos conectores)

Contratos nuevos de la ola v3, pinned en `ARCHITECTURE.md` §12 (ver también `docs/roadmap.md`). `edecan_api.main.V3_ROUTER_NAMES = ("credentials", "setup", "skills", "smarthome")` — mismo montaje defensivo que ya usan los 8 routers de v2 (`ARCHITECTURE.md` §11, `V2_ROUTER_NAMES`): la API sigue arrancando completa aunque alguno de estos módulos todavía no haya aterrizado en disco. Los 4 routers de esta ola ya existen en `apps/api/edecan_api/routers/` (`credentials`, `setup`, `skills` y `smarthome`) — lo que sigue de cada uno lo documenta verificado contra los archivos reales. Salvo que se indique lo contrario, Auth: Bearer (access).

### `/v1/credentials` — LLM y voz (STT/TTS), por tenant — ya aterrizado

Corrige el hueco de diseño documentado en `docs/roadmap.md` ("Corrección de diseño: TODO bring-your-own, incluso en hosted"): antes de este router, el proveedor LLM y el de voz web (Deepgram/ElevenLabs) se resolvían desde variables de entorno de PLATAFORMA (`Settings`/`.env`, un solo valor compartido por todos los tenants — ver [`configuracion.md`](./configuracion.md)). Ahora cada tenant conecta su PROPIA credencial, cifrada en el `TokenVault` bajo una `connector_account` singleton por tenant (`connector_key` fijo: `"llm"`, `"voice_stt"` o `"voice_tts"` — a diferencia de un conector OAuth normal, un tenant solo tiene una credencial activa de cada tipo a la vez). Si el tenant no conecta nada, la API **no** cae a ningún proveedor de PLATAFORMA — ni siquiera en self-host de un solo tenant, que también debe conectar su propia credencial en esta pantalla: para LLM, `get_llm_router` corta la request con `HTTPException(400)` (`apps/api/edecan_api/deps.py`); para voz, cae a `StubSTT`/`StubTTS` (offline, determinista). Ver [`credenciales.md`](./credenciales.md) para el detalle completo del orden de resolución.

#### `GET /v1/credentials`

Nunca devuelve el secreto completo — solo `"masked"` (`"…" + últimos 4 caracteres`, o `null` si no hay credencial guardada o el proveedor no usa API key, como Polly). Cada bloque es `null` si el tenant todavía no conectó nada ahí:

```json
{
  "llm": {"kind": "claude_cli", "model_principal": null, "model_rapido": null, "base_url": null, "masked": null},
  "voice_stt": {"provider": "deepgram", "masked": "…9f2a"},
  "voice_tts": null,
  "images": null,
  "search": {"provider": "brave", "masked": "…7f3a"}
}
```

(`images`/`search` — ver la sección `/v1/credentials/images` y `/v1/credentials/search` más abajo.)

#### `PUT /v1/credentials/llm`

```json
{"kind": "anthropic", "api_key": "sk-ant-...", "model_principal": null, "model_rapido": null, "base_url": null, "extra": {}, "validate": true}
```

`kind` ∈ `anthropic`\|`openai_compat`\|`vertex`\|`claude_cli`\|`codex_cli`\|`ollama`. Campos según `kind` (los demás quedan en `null`):

- `anthropic`/`vertex`: requieren `api_key`. `vertex` hoy es solo el camino simple de API key de Gemini/Vertex — `docs/roadmap.md` lo prioriza como el default; el flujo avanzado de proyecto GCP + service account todavía no está aterrizado.
- `openai_compat`: requiere `base_url` (+ `api_key` opcional).
- `ollama`: requiere `model_principal` (el nombre de un modelo ya descargado, p. ej. `"llama3.1"`); `base_url` es opcional, por defecto `http://localhost:11434`.
- `claude_cli`/`codex_cli`: no llevan secreto — el servidor corre `<binario> --version` como subproceso (timeout 10s) para confirmar que está instalado y responde.

`validate` (default `true`): antes de guardar, hace una llamada liviana real al proveedor (o corre el CLI) para confirmar que la credencial sirve — `204 No Content` si valida; `400` con el detalle EXACTO que dio el proveedor si no (status + fragmento del cuerpo, o el `stderr` del CLI). `validate: false` guarda sin pegarle a la red (tests, migraciones).

`claude_cli`/`codex_cli`/`ollama` SOLO se aceptan si el servidor corre con `EDECAN_LOCAL_MODE=true` (`edecan_api.config.Settings`, default `False`) — el modo en el que arranca la app de escritorio (`apps/local`): apuntar un backend hospedado a un binario/puerto de la máquina del SERVIDOR no tiene sentido. Sin `EDECAN_LOCAL_MODE`, estos tres `kind` responden `400` pidiendo una API key normal o la app de escritorio.

#### `DELETE /v1/credentials/llm`

`204 No Content`, idempotente (no falla si ya no había nada conectado). El tenant se queda sin proveedor LLM: `get_llm_router` vuelve a cortar cualquier turno de chat con `HTTPException(400)` hasta que conecte uno propio de nuevo — nunca cae a un proveedor LLM de plataforma.

#### `PUT /v1/credentials/voice/stt`

```json
{"provider": "deepgram", "api_key": "...", "validate": true}
```

Único `provider` de STT soportado hoy: `"deepgram"`.

#### `PUT /v1/credentials/voice/tts`

```json
{"provider": "elevenlabs", "api_key": "...", "voice_id": "21m00Tcm4TlvDq8ikWAM", "validate": true}
```

`provider` de TTS ∈ `elevenlabs`\|`polly`. `elevenlabs` requiere `api_key` (`voice_id` opcional); `polly` NO lleva `api_key` (usa la cadena de credenciales AWS *ambiente* del proceso que corre el backend, no una key propia del tenant — `voice_id` opcional, por defecto `"Lupe"`) y por eso `validate` no dispara ningún ping de red para ese proveedor. `polly` además SOLO se acepta con `EDECAN_LOCAL_MODE=true` (mismo gate que `claude_cli`/`codex_cli`/`ollama` arriba, y mismo motivo: esa identidad ambiente solo es la del tenant en modo single-user) — fuera de ahí, `400` pidiendo `elevenlabs` o la app de escritorio.

Ambos endpoints comparten el mismo criterio fail-closed que `PUT /v1/credentials/llm`: `204` si valida (o si `validate: false`), `400` con el detalle exacto si el proveedor rechaza la credencial.

#### `DELETE /v1/credentials/voice/{canal}`

`canal` ∈ `stt`\|`tts`. `204 No Content`, idempotente. `404` si `canal` no es `stt` ni `tts`. El tenant vuelve a caer al stub offline (`StubSTT`/`StubTTS`) para ese canal — nunca a un proveedor de voz de plataforma.

### `/v1/credentials/images` y `/v1/credentials/search` — bring-your-own de imágenes y búsqueda, por tenant

`GenerarImagenTool` y `BuscarWebTool` resuelven sus proveedores por tenant, nunca con una key compartida de plataforma. Sin proveedor de imágenes, Edecán usa un resultado de demostración claramente marcado. Sin proveedor de búsqueda, usa `DuckDuckGoSearch`: internet real sin API key. Brave y Tavily quedan como conexiones opcionales. Ver [`credenciales.md`](./credenciales.md#orden-de-resolución-imágenes-y-búsqueda-web--sin-paso-de-plataforma).

#### `PUT /v1/credentials/images`

```json
{"base_url": "https://api.openai.com/v1", "api_key": "sk-...", "model": "gpt-image-2", "validate": true}
```

Los tres campos son obligatorios (`400` si falta alguno). `base_url` acepta cualquier endpoint compatible con `POST {base_url}/images/generations` (contrato de OpenAI Images) — mismo criterio que `kind: "openai_compat"` de LLM. `validate` (default `true`) hace `GET {base_url}/models` antes de guardar (reutiliza el mismo ping que `openai_compat`); `400` con el detalle exacto si el proveedor rechaza la credencial.

#### `DELETE /v1/credentials/images`

`204 No Content`, idempotente. El tenant vuelve a caer al stub offline (`StubImageProvider`) — nunca a un proveedor de imágenes de plataforma.

#### `PUT /v1/credentials/search`

```json
{"provider": "brave", "api_key": "...", "validate": true}
```

`provider` ∈ `brave`\|`tavily`. `validate` (default `true`) hace una búsqueda real con `k=1` contra el proveedor elegido antes de guardar (ninguno de los dos documenta un endpoint de solo-validación separado del uso real); `400` con el detalle exacto si rechaza la credencial.

#### `DELETE /v1/credentials/search`

`204 No Content`, idempotente. El tenant vuelve a DuckDuckGo real sin clave, nunca a un proveedor de búsqueda de plataforma.

### `/v1/setup` — wizard de primer arranque

#### `GET /v1/setup/status`

Para que la UI decida si mostrar el wizard corto de bienvenida (2–3 pasos máximo) o ir directo al chat:

```json
{"local_mode": true, "llm_configured": true, "version": "0.4.0"}
```

`local_mode` = `EDECAN_LOCAL_MODE` del servidor; `llm_configured` = el tenant ya tiene una `connector_account` con `connector_key="llm"` y un `TokenBundle` guardado (mismo criterio que `routers/credentials.py::get_credentials`); `version` = `edecan_api.__version__`.

#### `GET /v1/setup/detect`

Auto-detección de un clic (`docs/roadmap.md`, "principio de configuración de pocos clics"): expone `edecan_llm.detect.detect_local_providers` (`ARCHITECTURE.md` §12.d — mismos nombres de campo exactos, `installed`/`path`/`version` para los CLI y `running`/`base_url`/`models` para Ollama) envuelto en un campo `local_mode` adicional que decide el propio router:

```json
{
  "local_mode": true,
  "claude_cli": {"installed": true, "path": "/usr/local/bin/claude", "version": "1.4.2"},
  "codex_cli": {"installed": false, "path": null, "version": null},
  "ollama": {"running": true, "base_url": "http://localhost:11434", "models": ["llama3.1", "qwen2.5-coder"]}
}
```

**Regla dura de este endpoint**: solo detecta algo real cuando el servidor corre con `EDECAN_LOCAL_MODE=true` (`edecan_api.config.Settings`, ver `PUT /v1/credentials/llm` arriba) — es decir, EMPAQUETADO Y LOCAL en la máquina del propio cliente (la app de escritorio, `apps/local`). En un despliegue hospedado/compartido (`EDECAN_LOCAL_MODE=false`), el backend corre en un servidor que no es la máquina del tenant, así que revisar sus binarios/procesos y ofrecérselos a un tenant como si fueran "su" Claude CLI sería, en el mejor caso, inútil y en el peor, engañoso — en ese modo el endpoint responde siempre determinista con todo en `false`/vacío, sin depender de lo que haya realmente instalado el servidor:

```json
{
  "local_mode": false,
  "claude_cli": {"installed": false, "path": null, "version": null},
  "codex_cli": {"installed": false, "path": null, "version": null},
  "ollama": {"running": false, "base_url": "", "models": []}
}
```

La UI cae directo al flujo de API key / CLI remoto, sin ofrecer nunca el atajo de un clic. `PUT /v1/credentials/llm` aplica exactamente la misma regla (mismo flag) del lado de la validación, aunque hoy la comprueba directamente sin pasar por este endpoint.

### `/v1/skills` — marketplace abierto de Agent Skills

Integración con el mismo estándar abierto de "Agent Skills" que indexa skills.sh (`docs/roadmap.md`): en vez de un catálogo propietario cerrado, el toolkit de Edecán instala y usa skills de ese marketplace compartido. Router delgado (`apps/api/edecan_api/routers/skills.py`, fase v3): reutiliza `edecan_skills.installer`/`edecan_skills.store`/`edecan_skills.client` sin duplicar lógica — ver `packages/skills/README.md`. Sin flag de plan (disponible en todos los planes, igual que `/v1/reminders`); `POST /v1/skills/install` no exige el gate `confirmation_required` de una tool `dangerous` porque el clic en el botón "Instalar" de la UI autenticada YA es la confirmación humana.

- `GET /v1/skills` — skills **instaladas** por el tenant (no el catálogo completo del marketplace; para descubrir lo que todavía no está instalado usa `POST /v1/skills/search`). Nunca incluye `contenido`/`recursos` (deliberadamente liviano, ver `edecan_skills.store._LIST_COLUMNS`) — eso solo viaja en `GET /v1/skills/{id}` o al instalar.

  ```json
  {
    "skills": [
      {
        "id": "8f1a2b3c-0000-4000-8000-000000000001",
        "nombre": "PDF Analyzer",
        "slug": "owner-pdf-analyzer",
        "source": "owner/pdf-analyzer",
        "descripcion": "Extrae y resume PDFs largos.",
        "version": "1.2.0",
        "enabled": true,
        "created_at": "2026-07-08T10:00:00Z"
      }
    ]
  }
  ```

- `GET /v1/skills/{id}` — detalle de una skill **instalada** de este tenant; `{id}` es el UUID interno (columna `id` de `skills`, no el `"owner/repo"` — ese vive en `source`). `404` si no está instalada (este endpoint no consulta el marketplace remoto, solo `skills` local). Misma forma que arriba más `contenido` (el `SKILL.md` completo), `recursos` y `updated_at`.
- `POST /v1/skills/search {"q": "generar reportes de excel"}` — busca en el marketplace remoto (`SkillsIndexClient` contra `SKILLS_INDEX_URL`, default `https://skills.sh`), no en lo instalado. `q` vacío devuelve `{"resultados": []}` sin llamar a la red. Forma de respuesta **distinta** a `GET /v1/skills` (son hits del índice, no filas locales — todavía no tienen `id` interno ni `enabled` porque no están instaladas):

  ```json
  {
    "resultados": [
      {"nombre": "PDF Analyzer", "source": "owner/pdf-analyzer", "descripcion": "Extrae y resume PDFs largos.", "installs": 45231}
    ]
  }
  ```

- `POST /v1/skills/install {"source": "owner/pdf-analyzer"}` → `201`. `source` seco (formato `npx skills add <owner/repo>`), no `id`. Corre el pipeline `parse_source -> fetch_skill -> parse_skill_md -> insert_skill` y devuelve la skill instalada con la misma forma completa de `GET /v1/skills/{id}` (incluye `contenido`). Errores del pipeline mapeados a HTTP: `400` (`FuenteInvalidaError` — incluye los casos anti path-traversal/SSRF de `parse_source`, y `source` vacío), `404` (`SkillNoEncontradaError`), `413` (`SkillDemasiadoGrandeError` — cap de 200 000 bytes).
- `PUT /v1/skills/{id} {"enabled": false}` → `204 No Content`. Solo habilita/deshabilita la skill instalada sin desinstalarla — **no** actualiza versión ni ningún otro campo (para eso, reinstalar). `404` si no está instalada.
- `DELETE /v1/skills/{id}` → `204`. Desinstala. `404` si no está instalada.

### `/v1/smarthome` — casa inteligente (Home Assistant)

Un solo conector — la propia instancia de Home Assistant del tenant (self-host friendly, API oficial) — para luces, A/C, cámaras, cerraduras y sensores (`docs/roadmap.md`, movido de P2 a construcción real en v3 por `docs/roadmap.md`). `connector_key="homeassistant"` es singleton por tenant, mismo patrón "pegar y validar" que `/v1/credentials` (`apps/api/edecan_api/routers/smarthome.py`, fase v3).

Body de `PUT /v1/smarthome/credentials` — el campo del token se llama **`token`**, no `access_token`:

```json
{"base_url": "http://homeassistant.local:8123", "token": "eyJhbGciOiJIUzI1NiIs...", "validate": true}
```

`validate` (default `true`, igual convención que `/v1/credentials`): si es `true`, antes de guardar hace un `GET {base_url}/api/` real con el token (fail-closed igual que Twilio) y responde `400` con el detalle exacto si Home Assistant lo rechaza (401, host inalcanzable, cualquier status distinto de 200) o si `base_url` no es una URL http/https válida o trae credenciales embebidas; `204 No Content` si valida (o si `validate: false`). `DELETE /v1/smarthome/credentials` → `204`, idempotente.

Respuesta de `GET /v1/smarthome/status` — campos `configured`/`base_url`/`reachable` (no `connected`/`entities_count`, que no existen en este endpoint):

```json
{"configured": true, "base_url": "http://homeassistant.local:8123", "reachable": true}
```

`reachable` es una sonda liviana en vivo (`GET {base_url}/api/` con timeout corto) que nunca lanza: `true` si respondió 200, `false` si respondió cualquier otro status (p. ej. token vencido), `null` si la red falló del todo (timeout, host caído). `{"configured": false, "base_url": null, "reachable": null}` si no hay credencial conectada.

### `PUT /v1/connectors/whatsapp/credentials` — WhatsApp Business Platform

Extiende `edecan_connectors`/`PUT /v1/connectors/{key}/credentials` (§10.12, §10.8) con WhatsApp Business Platform (API oficial de Meta) — ruta fija (como `/twilio/credentials`, no la genérica de solo-`bot_token`) porque necesita dos campos, no uno, y singleton por tenant (`connector_key="whatsapp"`, `ARCHITECTURE.md` §12.b):

```json
{"access_token": "EAAG...", "phone_number_id": "109876543210987", "validate": true}
```

`validate` es opcional (default `true`). Con `validate=true` (el caso normal), se hace un ping real a la Graph API de Meta antes de guardar (`GET {phone_number_id}?fields=display_phone_number,verified_name`, mismo espíritu fail-closed que Twilio) y el `display_name` guardado es el `display_phone_number` que devuelve Meta; con `validate=false` solo se valida el FORMATO de las dos credenciales (sin llamada de red) y `display_name` cae al propio `phone_number_id`. Respuestas: `204` en éxito; `400` si el formato es inválido o Meta rechaza el `access_token`/`phone_number_id` (401/403/404 de Meta se traducen todos a `400`); `502` si la Graph API de Meta no respondió (timeout/host caído). La cuenta es singleton por tenant: conectar de nuevo reemplaza (no duplica) la cuenta de WhatsApp existente del tenant.

Tiene su propio motor de cumplimiento (plantillas pre-aprobadas, ventanas de mensajería de 24 horas, opt-in explícito) — no es un simple copy-paste del checklist de telefonía de [`voz-telefonia.md`](./voz-telefonia.md); guía completa (prerrequisitos en Meta, ejemplo con `curl`, ventana de 24h/plantillas, limitación de solo-lectura de mensajes entrantes) en la sección ["WhatsApp (Cloud API oficial)"](./mensajeria.md#whatsapp-cloud-api-oficial) de [`mensajeria.md`](./mensajeria.md).

Implementado y funcionando (fase v3): `apps/api/edecan_api/routers/connectors.py` trae la ruta real (`connect_whatsapp`) más sus dos helpers, `_verify_whatsapp_phone_ownership` y `_upsert_whatsapp_account`; `list_connectors` incluye una entrada `whatsapp` y `disconnect` acepta esa clave. `packages/messaging/edecan_messaging/_creds.py` lee exactamente lo que este endpoint escribe. Cobertura de tests dedicada en `apps/api/tests/test_connectors_whatsapp.py` (24 tests). El test de v2 que antes usaba `"whatsapp"` como ejemplo de "conector desconocido" (`test_connectors_credentials_v2.py::test_connect_bot_token_rejects_unknown_key`) ya se actualizó para usar `"signal"` en su lugar, ahora que `"whatsapp"` es una clave real.

## Rutas v4 (montaje defensivo)

`ARCHITECTURE.md` §13.g decidió que ningún WP tocara `docs/api.md` durante
v4 ("con 5 routers nuevos aterrizando en paralelo... mantenerlo
sincronizado a mano por WP dejaría de ser confiable") y lo dejó como deuda
aceptada para "una pasada de documentación dedicada posterior, fuera de
esta ola". Esta sección es esa pasada (fase v6): los 5 routers de
`edecan_api.main.V4_ROUTER_NAMES = ("devices", "erp", "ads", "vehiculos",
"mensajes")` (§13.a), a alto nivel — un prefijo + una línea por endpoint,
verificado contra el código real de cada router a la fecha de este work
package. El detalle fino (payloads exactos, códigos de error, ejemplos)
vive en el `docs/<feature>.md` de cada uno, mismo patrón que "Rutas v5" más
abajo.

### `/v1/devices` — dispositivos emparejados (companion + móvil)

El CRUD base no exige flag de plan (`companion`, §10.13, ya es `True` en
los 4 planes); los 5 endpoints de push nativo sí exigen
`notifications.push` (§14.c, `True` en los 4 planes hoy).

| Ruta | Para qué |
|---|---|
| `GET /v1/devices` | Lista los dispositivos del tenant (todos los usuarios). |
| `POST /v1/devices {nombre, plataforma, kind, fingerprint?}` | `201`; si `fingerprint` ya existe en un dispositivo `active` del mismo usuario, responde `200` y actualiza ese en vez de duplicar. |
| `POST /v1/devices/{id}/heartbeat` | `204`, refresca `last_seen_at`. `404` si no existe. |
| `POST /v1/devices/{id}/revoke` | Pasa el dispositivo a `status="revoked"` + audita. |
| `POST /v1/devices/pairing` | Auth requerida. Genera `pairing_uri` para un QR de un solo uso (10 min). |
| `POST /v1/devices/pairing/claim` | Sin JWT. Consume el token del QR atómicamente y entrega JWTs + identidad durable del móvil. |
| `POST /v1/devices/pairing/refresh` | Sin JWT. Restaura una sesión con `device_id` + secreto durable; falla si el dispositivo fue revocado. |
| `POST` / `DELETE /v1/devices/{id}/push-token` | Registra/limpia `push_token`+`push_platform` de un dispositivo tuyo. `204`; `404` si no es tuyo. |
| `PUT` / `DELETE /v1/devices/push/credentials` | Pegar-y-validar (sin llamada de red) tus credenciales APNs/FCM. `204`. |
| `GET /v1/devices/push/status` | Qué proveedores tienes conectados + cuántos de tus dispositivos ya registraron token. |

Ver [`notificaciones-push.md`](./notificaciones-push.md) para el contrato completo de la parte de push.

### `/v1/erp` — inventario (ERP básico, flag `erp.inventory`)

| Ruta | Para qué |
|---|---|
| `GET /v1/erp/productos?activo=&q=` | Lista productos, con filtros opcionales. |
| `POST /v1/erp/productos` | Crea un producto. `201`; `409` si el `sku` ya existe en el tenant. |
| `PATCH /v1/erp/productos/{id}` | Edición parcial — `{"activo": false}` desactiva (no hay ruta dedicada de "desactivar"). |
| `POST /v1/erp/productos/{id}/movimientos` | Registra un movimiento de stock. `201`; `400` si dejaría stock negativo con un motivo que no sea `'ajuste'`. |
| `GET /v1/erp/resumen` | SKUs activos, valor a costo/precio, alertas de stock bajo. |

Ver [`negocios.md`](./negocios.md#inventario-erp-básico) para el contrato completo (modelo de datos, atomicidad del movimiento de stock).

### `/v1/ads` — borradores publicitarios (flag `tools.ads`)

| Ruta | Para qué |
|---|---|
| `PUT` / `DELETE /v1/ads/credentials` | Pegar-y-validar tu cuenta de Meta Ads. `204`. |
| `GET /v1/ads/status` | Estado de la conexión + sonda en vivo. |
| `GET /v1/ads/resumen?periodo=` | Campañas + métricas reales de Meta (o datos de ejemplo sin cuenta conectada). |
| `GET /v1/ads/borradores` | Lista tus `ad_drafts`, más nuevos primero. |
| `POST /v1/ads/borradores/{id}/confirmar` | `draft → confirmed → pushed` (o `error`). Empuja la campaña a Meta **siempre en pausa** — nunca activa gasto por su cuenta. |
| `POST /v1/ads/borradores/{id}/cancelar` | Cancela un borrador no `pushed`. |

Ver [`ads.md`](./ads.md) para el contrato completo (los dos gates de confirmación, disclaimers de gasto real).

### `/v1/mensajes` — bandeja unificada (flag `connectors.messaging`)

| Ruta | Para qué |
|---|---|
| `GET /v1/mensajes/canales` | Estado de las 4 plataformas conectadas del tenant (`puede_leer` es `false` solo para WhatsApp). |
| `GET /v1/mensajes?canal=&origen=&limite=` | Últimos mensajes de un canal ya conectado, normalizados. `400` si el canal no existe, es `whatsapp`, o no está conectado. |
| `POST /v1/mensajes/enviar {canal, destinatario, texto}` | Envía un mensaje real vía el cliente oficial del canal y audita (`mensajes.enviado`). |

Ver [`mensajeria.md`](./mensajeria.md) para el contrato completo (por qué WhatsApp es solo-lectura desde acá, formato de `fecha`, plantillas).

### `/v1/vehiculos` — fuera de alcance para el agente; el router HTTP sigue activo

Flag de plan `tools.vehicles` (verificado contra el código real: los 6
endpoints exigen `require_vehicles_flag`), además de la sesión autenticada.
El router HTTP (`PUT`/`DELETE /credentials`, `GET /status`, `GET ""`, `GET
/{id}/estado`, `POST /{id}/puertas`) funciona igual que cualquier otro
conector bring-your-own (Smartcar) — pero las tools de agente
(`vehiculo_estado`/`vehiculo_controlar`) están deliberadamente excluidas de
todo build real del producto (`docs/roadmap.md`, "Vehículos (Smartcar)
eliminado del alcance"; §13.e/§13.h). Detalle completo en
[`vehiculos.md`](./vehiculos.md) — no se invierte más esfuerzo aquí.

## Rutas v5 (montaje defensivo)

A diferencia de v4 (`ARCHITECTURE.md` §13.g, "nadie lo toca en v4" — deuda
aceptada por el riesgo de conflictos de merge con 5 routers en paralelo), v5
retoma la convención de v1-v3: esta sección lista los 3 routers nuevos de
`ARCHITECTURE.md` §14.a a alto nivel — un prefijo + una línea por endpoint
conocido a la fecha de este work package (fase v5, el linchpin de
contratos compartidos). Los detalles finos (payloads exactos, códigos de
error, ejemplos) los documenta cada WP dueño en su propio `docs/<feature>.md`
según vaya aterrizando, mismo patrón que `docs/vehiculos.md`/`docs/ads.md`.
Montaje defensivo (`edecan_api.main.V5_ROUTER_NAMES`, §14.a): si el módulo
de un router todavía no existe en disco, la API sigue arrancando completa
sin él — un enlace roto temporal a alguno de los endpoints de abajo es
esperado mientras su WP dueño no haya aterrizado.

### `/v1/rrhh` — RRHH ligero (flag `erp.hr`)

Dueño real: un WP de seguimiento que extiende `packages/business/edecan_business` (§14.f) — mismo paquete que ya sirve `/v1/negocios`/`/v1/inventario`.

- `GET|POST /v1/rrhh/empleados` · `PATCH /v1/rrhh/empleados/{id}` — CRUD de `employees` (§14.b).
- `GET|POST /v1/rrhh/ausencias` · `PATCH /v1/rrhh/ausencias/{id}` — CRUD de `time_off`; aprobar/rechazar se hace vía el `PATCH` (cambio de `status`).
- `GET|POST /v1/rrhh/nominas` · `GET /v1/rrhh/nominas/{id}` — crear/listar/ver una corrida de `payroll_runs` (nace `status="draft"`, §14.b).
- `POST /v1/rrhh/nominas/{id}/aprobar` · `POST /v1/rrhh/nominas/{id}/cancelar` — únicos caminos que mueven `payroll_runs.status` fuera de `"draft"`; la tool de agente equivalente (`preparar_nomina`, §14.e) es `dangerous=True` y solo deja el borrador, nunca aprueba por sí sola.

### `/v1/viajes` — vuelos/hoteles/paquetes (flag `tools.travel`)

Dueño real: fase v5 (`packages/travel`, `edecan_travel`, §14.f). Implementado y funcionando: el router HTTP (`apps/api/edecan_api/routers/viajes.py`) ya aterrizó con 8 endpoints reales y testeados (`PUT`/`DELETE /credentials`, `PUT`/`DELETE /rastreo/credentials`, `GET /status`, `GET /buscar/vuelos`, `GET /buscar/hoteles`, `GET /rastreo/{numero}`) — detalle completo (payloads, códigos de error, ejemplos) en [`docs/viajes.md`](./viajes.md). El paquete Python (`buscar_vuelos`/`buscar_hoteles`/`estado_vuelo`/`preparar_reserva`/`rastrear_paquete`, §14.e) también se ofrece al agente vía el entry point `edecan.tools`, disponible en el chat normal. `preparar_reserva` (`dangerous=True`) nunca llama a un proveedor de viajes real — solo deja un borrador en `orders` (tabla ya existente desde v2).

### `/v1/voz` — voz avanzada: clonación (flag `voice.cloning`)

Dueño real: fase v5 (`packages/voice`, `edecan_voice`, §14.f). Prefijo real `/v1/voz` — única excepción del repo a la convención "nombre de módulo = último segmento del prefix" (el nombre de módulo sigue siendo `voz_avanzada`, ver §14.a).

- `GET /v1/voz/voces` — voces disponibles del tenant (equivalente HTTP de la tool `listar_voces`). Gatea `voice.web`, no `voice.cloning` (mismo criterio que la tool, `ARCHITECTURE.md` §14.e: listar/sintetizar no clona nada) — única excepción al flag `voice.cloning` del título de esta sección, que sí aplica a los 3 endpoints de `/clones` de abajo.
- `POST /v1/voz/clones` — registra un consentimiento de clonación (`voice_consents`, §14.b) y solicita la clonación al proveedor de voz bring-your-own del tenant.
- `GET /v1/voz/clones` — lista los consentimientos/voces clonadas del tenant.
- `DELETE /v1/voz/clones/{clon_id}` — revoca un consentimiento (`voice_consents.status` → `"revoked"`).

`crear_podcast`/`generar_efecto_sonido` (flag `tools.podcast`, §14.e) y el
job `generate_podcast` (§14.d) no tenían endpoint HTTP propio en v5 —
desde v6 (fase v6) sí lo tienen: ver `/v1/voz/podcasts` en la sección
"Rutas v6 (montaje defensivo)" más abajo.

## Rutas v6 (montaje defensivo)

Contratos nuevos de la ola v6, pinned en `ARCHITECTURE.md` §15.
`edecan_api.main.V6_ROUTER_NAMES = ("reuniones", "analista", "mcp")` — mismo
montaje defensivo que v2-v5 (§11 `docs/roadmap.md`, §12.a, §13.a, §14.a):
la API sigue arrancando completa sin importar cuántos de estos 3 módulos
existan todavía en disco. Al momento de escribir esta sección, los 3 módulos
(`reuniones`, `analista` y `mcp`) ya existen en `apps/api/edecan_api/routers/`
y están montados (documentados abajo, verificados contra el código real). Los
endpoints de podcasts (`/v1/voz/podcasts*`) **no** son parte de `V6_ROUTER_NAMES`:
fase v6 los agregó DENTRO del router `voz_avanzada`, ya montado desde v5
(§14.a) — se documentan en esta sección de todas formas por ser contenido
nuevo de v6.

### `/v1/reuniones` — resumen y minutas de reuniones (flag `tools.meetings`)

Dueño real: fase v6 (`packages/meetings`, `edecan_meetings`). Ya aterrizado
y verificado contra el código real:

- `POST /v1/reuniones {file_id, titulo?}` → `202`. Valida que el archivo
  exista, sea del tenant y sea audio/video (`400` si el mime no empieza por
  `audio/`\|`video/`), inserta la fila `meetings` en `status="pending"` (único
  valor inicial que acepta el CHECK real de la tabla — `pending|running|done|
  error`, `ARCHITECTURE.md` §15.b; el worker la pasa a `"running"` al
  tomarla) y encola el job `process_meeting` con `{"meeting_id": ...}` — todo
  dentro de la MISMA transacción HTTP corta. `404` si el archivo no existe o
  no es tuyo.
- `GET /v1/reuniones` — lista del tenant, más recientes primero.
- `GET /v1/reuniones/{id}` — detalle: `resumen`, `decisiones`, `acciones`
  (`{tarea, responsable?}`), `temas`, `transcript_file_id`,
  `duracion_segundos`, `status`, `error`. `404` si no es tuya. `decisiones`/
  `acciones`/`temas` NO son columnas propias de la tabla — el router las lee
  y las aplana desde el único blob `minutos JSONB` que escribe el worker
  (`{"decisiones": [...], "acciones": [...], "temas": [...]}`); solo cambia
  el mapeo interno, la forma de la respuesta HTTP no.
- `DELETE /v1/reuniones/{id}` → `204`. Borra SOLO la fila `meetings` — el
  archivo de origen y la transcripción (ambos filas `files` normales) se
  quedan intactos a propósito. `404` si no existe.

El trabajo pesado real (extraer audio, transcribir con el STT del tenant,
generar minutas con el LLM del tenant) ocurre async en
`apps/worker/edecan_worker/handlers/process_meeting.py`, nunca en esta
request. Tanto este endpoint como la tool de chat equivalente
(`resumir_reunion`, §15.f) recuerdan el disclaimer de consentimiento
**obligatorio**, string idéntico en los dos lugares (y en el banner de la UI
web): *"Recuerda: asegúrate de contar con el consentimiento de todos los
participantes para grabar y transcribir esta reunión."* Detalle completo en
[`reuniones.md`](./reuniones.md).

### `/v1/analista` — estadística, pronóstico y gráficos sobre tus archivos (sin flag de plan)

Dueño real: fase v6/fase v6 — expone la superficie PÚBLICA y PURA (sin
LLM) de `edecan_docanalysis` por REST. Sin flag de plan a propósito:
paridad con las tools de `edecan_docanalysis` (`predecir_serie`/
`detectar_anomalias`/etc., §14.e), que tampoco declaran `requires_flags`.
100% determinista y offline — ninguno de los 4 endpoints llama al LLM (la
vía en lenguaje natural, `analizar_tabla(pregunta=...)`, sigue existiendo
solo por chat). Ya aterrizado y verificado contra el código real:

- `GET /v1/analista/archivos` — archivos del tenant filtrados a mimes
  tabulares (CSV/XLSX).
- `POST /v1/analista/{file_id}/resumen {hoja?}` →
  `{columnas, stats, outliers, filas_leidas, total_filas_archivo}`.
- `POST /v1/analista/{file_id}/forecast {columna_fecha?, columna_valor?,
  horizonte<=24}` → `{forecast, anomalias}` sobre la misma serie (columna
  numérica autodetectada si falta `columna_valor`).
- `POST /v1/analista/{file_id}/grafico {tipo: barras|lineas|dona,
  columna_x?, columna_y?}` → `{"svg": "<svg ...>...</svg>"}`.

`404` si el archivo no existe o es de otro tenant; `400` con el mensaje
EXACTO de `edecan_docanalysis` si el archivo no parsea o una columna pedida
no existe; límite propio de este router de 10 MB por archivo (independiente
de los topes de filas/columnas de `edecan_docanalysis`). Detalle completo en
[`analista.md`](./analista.md#pantalla-analista).

### `/v1/mcp` — conector MCP por tenant (flag `tools.mcp`)

Dueño real: fase v6 (`packages/mcp`, `edecan_mcp`). Ya aterrizado y
verificado contra el código real (`apps/api/edecan_api/routers/mcp.py`):

- `GET /v1/mcp/servers` — lista los servidores MCP guardados del tenant
  (nombre, transporte, URL/comando redactado, estado y si tiene autenticación) — **nunca**
  incluye `headers`, `env` ni ningún valor secreto.
- `PUT /v1/mcp/servers {nombre, transporte, url?, comando?, headers?, env?,
  validate?}` → `204`. Con `validate` en `true` (default), antes de guardar
  nada hace el *handshake* MCP real (`initialize` + `tools/list`) contra el
  servidor — `400` con el detalle exacto si falla y nada se persiste;
  `validate: false` es la escotilla de escape (tests, migraciones). Emula
  upsert por `nombre`.
  `env` solo se admite en `stdio`, se cifra con la configuración y permite
  entregar secretos a un proceso local sin incrustarlos en `comando`.
- `DELETE /v1/mcp/servers/{nombre}` → `204`. Idempotente (nada que borrar ya
  es un estado válido de "desconectado").
- `GET /v1/mcp/servers/{nombre}/tools` — conecta en vivo y devuelve las
  tools que expone ese servidor ahora mismo. `404` si `{nombre}` no está
  conectado.

Contrato de datos pinned en `ARCHITECTURE.md` §15.g:

- Bring-your-own "múltiple" (patrón OAuth, a diferencia del patrón
  "singular" de `/v1/credentials`): un tenant conecta VARIOS servidores MCP,
  cada uno su propia fila `connector_accounts` con `connector_key="mcp"` y
  `external_account_id` = el slug que el tenant elige (p. ej. `"notion"`).
- Config + secretos del transporte viajan juntos, cifrados, en
  `TokenBundle.access_token` (JSON `{nombre, transporte: "http"|"stdio",
  url?, comando?, headers?}`, `token_type="config"`).
- Tools dinámicas `mcp_{slug}_{tool}`, resueltas por tenant en tiempo de
  turno de chat — **siempre** `dangerous=True`, sin excepción (código de un
  servidor de terceros que Edecán no audita ni controla).
- Transporte `"stdio"` (subproceso local) solo con `EDECAN_LOCAL_MODE=True`
  — mismo gate que `claude_cli`/`codex_cli`/`ollama`/Polly.

Detalle completo en [`mcp.md`](./mcp.md).

### `/v1/voz/podcasts` — vertical completo de podcasts (flag `tools.podcast`)

Dueño real: fase v6. **No** es un router nuevo — vive DENTRO de
`voz_avanzada`, ya montado desde v5 (§14.a), por eso no aparece en
`V6_ROUTER_NAMES`. Antes de este WP, `crear_podcast` (tool de chat) era la
única forma de generar un podcast, sin ninguna fila que el usuario pudiera
consultar desde la UI. Ya aterrizado y verificado contra el código real:

- `POST /v1/voz/podcasts {titulo, guion: [{texto, voz?}]}` → **`202`** (no
  `201`: es un job asíncrono, mismo criterio que `POST /v1/reuniones`
  arriba). Valida el guion con `edecan_creative.podcast.validar_guion`
  (mismas reglas que la tool de chat), inserta la fila `podcasts` en
  `status="pending"` y encola `generate_podcast` con `{"podcast_id": ...}`
  — el job ya existente desde v5 (§14.d) ahora acepta este SEGUNDO shape de
  payload además del original `{"titulo", "segmentos", "formato",
  "user_id"}` de la tool de chat. `400` si `titulo` viene vacío o el guion
  no valida.
- `GET /v1/voz/podcasts` — lista del tenant, más recientes primero.
- `GET /v1/voz/podcasts/{id}` — una fila (incluye `file_id` cuando
  `status="done"`). `404` si no existe o no es tuya.
