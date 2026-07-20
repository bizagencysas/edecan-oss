# Mensajería (Telegram, Discord, Slack, WhatsApp)

Edecán envía y lee mensajes en **Telegram, Discord y Slack**, y además ENVÍA (solo envío — ver «Limitación de lectura en v3» más abajo) por **WhatsApp Business Platform**, exclusivamente por sus APIs oficiales (`ARCHITECTURE.md` §10.8, §12.b; `docs/roadmap.md`, fase v2; fase v3 agrega WhatsApp). Como con el resto de conectores (ver [`conectores.md`](./conectores.md)), **cada tenant conecta sus propias credenciales** — nunca hay un bot, una app ni un número de teléfono compartido de la plataforma que hable en nombre de todos los tenants.

Las cuatro plataformas no comparten un único mecanismo de conexión:

| Plataforma | Mecanismo | Cómo se conecta | Clave de conector |
|---|---|---|---|
| **Telegram** | Token de bot (no OAuth) | `PUT /v1/connectors/telegram/credentials {bot_token}` | `telegram` |
| **Discord** | Token de bot (no OAuth) | `PUT /v1/connectors/discord/credentials {bot_token}` | `discord` |
| **Slack** | OAuth v2 | `GET /v1/connectors/slack/authorize` → `GET /v1/connectors/slack/callback` (igual que Google/Microsoft/Meta/X/YouTube) | `slack` |
| **WhatsApp** | Access token + phone_number_id (no OAuth) | `PUT /v1/connectors/whatsapp/credentials {access_token, phone_number_id}` | `whatsapp` |

Telegram y Discord no tienen un flujo de autorización delegada tipo OAuth para bots: el propio tenant crea su bot y pega el token directo, con el mismo patrón no-OAuth que ya usa Twilio (`ARCHITECTURE.md` §10.10, `docs/voz-telefonia.md`) — el token se cifra en el `TokenVault` exactamente igual que cualquier otra credencial de conector. Slack sí es una app OAuth normal (registra scopes, redirige al usuario a instalar la app en su workspace) y por eso reutiliza el flujo genérico `authorize`/`callback` que ya usan el resto de conectores — no necesita un endpoint especial. WhatsApp tampoco es OAuth, pero a diferencia de Telegram/Discord necesita DOS campos (no un `bot_token` único), así que tiene su propia ruta fija — ver [«WhatsApp (Cloud API oficial)»](#whatsapp-cloud-api-oficial) más abajo.

---

## Telegram

**Clave del conector**: `telegram`. **Cómo se guarda**: `PUT /v1/connectors/telegram/credentials {"bot_token": "..."}`, autenticado con tu `Authorization: Bearer` normal del panel.

### Crear el bot con BotFather

1. Abre una conversación con [@BotFather](https://t.me/BotFather) en Telegram.
2. Envía `/newbot` y sigue las instrucciones: nombre visible del bot y un `username` único que debe terminar en `bot` (p. ej. `MiEmpresaAsistente_bot`).
3. BotFather responde con el **token del bot**, con la forma `123456789:AAHtsOm5POK_bl2ZzP1zN1Y1YRXhSHwWMTk` (un id numérico, dos puntos, y una cadena alfanumérica). Guárdalo — es la única credencial que necesitas.
4. (Opcional, recomendado) `/setprivacy` → *Disable* si quieres que el bot pueda leer todos los mensajes de un grupo al que lo agregues, no solo los que lo mencionan directamente (`leer_mensajes` usa `getUpdates`, que solo ve lo que el modo de privacidad del bot le permite ver).
5. Agrega el bot a los chats/grupos desde los que quieras enviar o leer mensajes (o simplemente inicia una conversación 1:1 con él).
6. Pega el token en el panel de Edecán (**Conectores → Telegram**), que internamente llama a `PUT /v1/connectors/telegram/credentials`.

### `destino`/`origen` que espera la tool

Un `chat_id` (número, puede ser negativo para grupos/canales) o el `username` del chat si es público. La forma más simple de obtener el `chat_id` de un chat nuevo es enviarle cualquier mensaje y llamar a `leer_mensajes` (que usa `getUpdates`): el `chat_id` aparece en el resultado.

### Límites de uso conocidos

Telegram no publica un cupo diario como Google/YouTube, pero sí límites de **tasa**: aproximadamente 1 mensaje/segundo a un mismo chat, y del orden de 30 mensajes/segundo en total repartidos entre chats distintos (los grupos grandes tienen límites más estrictos, ~20 mensajes/minuto). `edecan_messaging` no reintenta automáticamente si Telegram responde `429` — la tool reporta el error tal cual. Confirma los valores vigentes en la [documentación oficial de límites de Telegram](https://core.telegram.org/bots/faq#my-bot-is-hitting-limits-how-do-i-avoid-this).

---

## Discord

**Clave del conector**: `discord`. **Cómo se guarda**: `PUT /v1/connectors/discord/credentials {"bot_token": "..."}`.

### Crear la app y el bot en el Discord Developer Portal

1. Entra al [Discord Developer Portal](https://discord.com/developers/applications) → **New Application**, ponle un nombre.
2. Ve a la pestaña **Bot** → **Add Bot** (o **Reset Token** si ya existe uno).
3. En **Privileged Gateway Intents** no necesitas activar ninguno: `edecan_messaging` habla solo por **REST** (`POST/GET /channels/{id}/messages`), no abre conexión de Gateway/WebSocket, así que no necesita `MESSAGE CONTENT INTENT` ni los demás intents privilegiados que sí exigiría un bot en tiempo real.
4. Click **Reset Token** → **Copy** para obtener el token del bot (empieza distinto según la app, formato tipo `MTIzNDU2Nzg5MDEyMzQ1Njc4.GhIJKl.MnOpQrStUvWxYz0123456789`). Solo se muestra una vez — si lo pierdes, tendrás que regenerarlo.
5. Ve a **OAuth2 → URL Generator**: marca el scope `bot` y, en **Bot Permissions**, como mínimo `View Channels`, `Send Messages` y `Read Message History`. Copia la URL generada y ábrela para invitar el bot a tu servidor.
6. Pega el token en el panel de Edecán (**Conectores → Discord**).

### `destino`/`origen` que espera la tool

El `channel_id` numérico del canal de texto (clic derecho sobre el canal en Discord con el modo desarrollador activado → **Copy Channel ID**; **Ajustes de usuario → Avanzado → Modo desarrollador** si no ves esa opción).

### Límites de uso conocidos

Discord aplica *rate limits* por ruta (del orden de 5 solicitudes/5 segundos por canal para enviar mensajes) y un límite global por bot (~50 solicitudes/segundo) — devuelve `429` con un header `Retry-After` cuando se excede. `edecan_messaging` no reintenta automáticamente. Ver la [documentación oficial de rate limits de Discord](https://discord.com/developers/docs/topics/rate-limits).

---

## Slack

**Clave del conector**: `slack`. **Cómo se guarda**: flujo OAuth estándar — `GET /v1/connectors/slack/authorize` → el tenant instala la app en su workspace → `GET /v1/connectors/slack/callback`.

### Crear la app de Slack del tenant

1. Entra a [api.slack.com/apps](https://api.slack.com/apps) → **Create New App → From scratch**. Nombre de la app y workspace de desarrollo (cada tenant real hará esto en SU PROPIO workspace, no en el tuyo).
2. **OAuth & Permissions** → sección **Redirect URLs**: agrega exactamente `{PUBLIC_BASE_URL}/v1/connectors/slack/callback` (mismo patrón que el resto de conectores, ver [`conectores.md`](./conectores.md)).
3. En la misma pantalla, sección **Scopes → Bot Token Scopes**, agrega los tres scopes mínimos que pide Edecán (`edecan_connectors.messaging.slack.SlackConnector`, ver abajo): `chat:write`, `channels:read`, `channels:history`.
4. **Basic Information**: copia el **Client ID** y el **Client Secret** a `SLACK_CLIENT_ID`/`SLACK_CLIENT_SECRET` en tu `.env` — igual que el resto de proveedores OAuth, esta es la app de LA PLATAFORMA (tu instancia de Edecán), no la del tenant; cada tenant solo autoriza contra ella.
5. Desde el panel de Edecán (**Conectores → Slack**), el tenant hace clic en "Conectar" (dispara `GET /v1/connectors/slack/authorize`) y Slack le pide instalar la app en su workspace — al aceptar, vuelve al `callback` y queda conectado.

### Scopes mínimos exactos usados

```
chat:write
channels:read
channels:history
```

Solo cubren canales **públicos**. Si necesitas enviar/leer en canales privados, el tenant deberá invitar explícitamente al bot a ese canal privado y tu app necesitará además el scope `groups:history`/`groups:read` — no están incluidos por defecto (principio de mínimo privilegio: Edecán no pide de entrada acceso a conversaciones privadas).

### `destino`/`origen` que espera la tool

El id del canal (`C0123456789`) o su nombre con `#` (`#general`) — Slack resuelve ambas formas en `chat.postMessage`/`conversations.history`.

### Límites de uso conocidos

Slack limita la Web API por **tier** de método: `chat.postMessage` y `conversations.history` están en tiers que permiten del orden de decenas de solicitudes por minuto por workspace (Slack ha ido introduciendo límites adicionales por app en 2025 para apps nuevas). Confirma el tier vigente de cada método en la [documentación oficial de rate limits de Slack](https://api.slack.com/apis/rate-limits).

---

## WhatsApp (Cloud API oficial)

**Clave del conector**: `whatsapp`. **Cómo se guarda**: `PUT /v1/connectors/whatsapp/credentials {"access_token": "...", "phone_number_id": "..."}` (autenticado con tu `Authorization: Bearer` normal del panel; ver `ARCHITECTURE.md` §12.b y `apps/api/edecan_api/routers/connectors.py`, fase v3). A diferencia de Telegram/Discord/Twilio (que permiten varias cuentas por tenant, hasta la cuota del plan), la cuenta de WhatsApp es **singleton por tenant**: conectar una nueva reemplaza la anterior, nunca las acumula.

WhatsApp Business Platform (Cloud API, propiedad de Meta) tiene API oficial, pero con requisitos de cumplimiento sustancialmente más pesados que Telegram/Discord/Slack — esta sección los cubre en el mismo orden en que hay que resolverlos.

### Prerequisitos del tenant en Meta

Todo esto ocurre en la cuenta de Meta del PROPIO tenant (`developers.facebook.com`/Business Manager) — Edecán nunca provee número, app ni token:

1. **App de Meta**: crea una app tipo "Business" en [developers.facebook.com/apps](https://developers.facebook.com/apps).
2. **Producto WhatsApp**: agrégalo desde el dashboard de la app (**Add Product → WhatsApp → Set up**). Meta asigna un número y un `phone_number_id` de prueba automáticamente — sirve para probar, pero producción necesita un número propio verificado en el Business Manager.
3. **Access token PERMANENTE vía system user**: el token temporal de 24h que muestra el Quickstart de Meta NO sirve para producción — expira y Edecán no lo puede refrescar solo (WhatsApp no tiene un flujo `refresh` como el resto de `edecan_connectors`). En **Meta Business Suite → Configuración del negocio → Usuarios del sistema**, crea un **usuario del sistema**, asígnale la app de WhatsApp con el permiso `whatsapp_business_messaging`, y genera un token con expiración «Nunca». Ese es el `access_token` que se pega en Edecán.
4. **`phone_number_id`**: en el dashboard de la app → **WhatsApp → API Setup**, junto al número ya verificado — es un ID numérico interno de Meta, NO el número de teléfono en sí (no lleva `+` ni formato E.164).
5. **Plantillas de mensaje aprobadas** (message templates): en **Business Manager → WhatsApp Manager → Plantillas de mensajes**, crea y manda a revisión cada plantilla que vayas a usar (nombre, idioma, cuerpo con variables `{{1}}`, `{{2}}`, etc.). Meta tarda típicamente minutos a horas en aprobarlas. Son obligatorias para iniciar conversaciones o responder fuera de la ventana de 24h (ver abajo); Edecán no las crea ni las administra, solo las referencia por nombre al enviar.

### Configuración en Edecán

Con `access_token` y `phone_number_id` ya listos (las plantillas viven en Meta, no se pegan aquí), conéctalos desde el panel (**Conectores → WhatsApp**) o directo por API:

```bash
curl -X PUT https://tu-instancia.edecan.example/v1/connectors/whatsapp/credentials \
  -H "Authorization: Bearer $EDECAN_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "access_token": "EAAG...tu-token-permanente-de-system-user",
        "phone_number_id": "109876543210987"
      }'
```

Por defecto (`"validate": true`, se puede omitir) Edecán verifica las credenciales contra la Graph API real de Meta (`GET /{phone_number_id}?fields=display_phone_number,verified_name`) antes de guardarlas — mismo espíritu "fail closed" que la verificación de propiedad que ya hace con Twilio (`docs/voz-telefonia.md`): `400` si Meta rechaza el `access_token` (no autorizado para ese `phone_number_id`) o no encuentra el id, `502` si Meta no respondió. Éxito → `204`, y el panel muestra el número humano-legible (`display_phone_number`) que devolvió Meta como nombre de la cuenta conectada.

### Ventana de 24 horas y plantillas

La regla de cumplimiento más importante de WhatsApp Business Messaging: **solo se puede mandar texto libre dentro de las 24 horas siguientes al último mensaje que el destinatario escribió**. Fuera de esa ventana (o para iniciar tú la conversación), Meta EXIGE una plantilla pre-aprobada — un mensaje de texto libre fuera de ventana devuelve el error `131047` de la Graph API.

`enviar_mensaje` con `plataforma: "whatsapp"` refleja esto en sus argumentos:

- Sin `plantilla`: envía texto libre (`{destino, texto}`) — solo funciona dentro de la ventana de 24h.
- Con `plantilla` (+ `idioma` opcional, por defecto `es`): envía la plantilla ya aprobada con ese nombre — es la única forma de iniciar conversación o responder fuera de la ventana de 24h.

Si te equivocas (texto libre fuera de ventana), Edecán traduce el `131047` de Meta a un mensaje claro que indica usar `plantilla`, en vez de dejar pasar el error crudo de la Graph API; el código `131026` (destinatario no disponible / sin opt-in) también se traduce a un mensaje accionable.

### `destino` que espera la tool

Un número en formato E.164 (`+525512345678`), con o sin el `+` inicial — `edecan_messaging` lo normaliza antes de llamar a la Graph API, que espera el número SIN `+`.

### Limitación de lectura en v3 (sin webhooks)

`leer_mensajes` con `plataforma: "whatsapp"` responde con un mensaje explicando la limitación en vez de intentar leer. A diferencia de Telegram/Slack (que Edecán lee por *polling* con `getUpdates`/`conversations.history`), WhatsApp Cloud API empuja los mensajes entrantes por **webhook**: hace falta una URL pública verificada (`GET` de verificación con `hub.challenge` + validación de firma `X-Hub-Signature-256`, igual en espíritu a los webhooks de Twilio que ya valida `edecan_premium.twilio_router`). Montar ese webhook queda fuera del alcance de fase v3 — **roadmap para una fase posterior**: un router nuevo tipo `POST /v1/messaging/whatsapp/webhook`, con su propia verificación de firma por tenant/número.

### Cumplimiento y bring-your-own — sin excepciones

- **Nunca un número ni un token compartido de la plataforma**: cada tenant trae SU PROPIA app de Meta, SU PROPIO usuario del sistema y SU PROPIO número verificado — Edecán nunca manda un mensaje de WhatsApp en nombre de la plataforma ni de otro tenant.
- **Opt-in**: responsabilidad del propio tenant (igual que SMS/voz, `docs/voz-telefonia.md`) — a diferencia de Twilio, v3 no tiene una tabla `consents`/connector_key equivalente para WhatsApp; el tenant debe asegurarse de que el destinatario haya optado por recibir sus mensajes antes de escribirle.
- **Cero automatización que viole las políticas de Meta**: nada de mensajes masivos no solicitados, nada de eludir la ventana de 24h con trucos, nada de plantillas engañosas — solo el uso de WhatsApp Business Platform tal como Meta lo documenta. Ver la [política de mensajería de WhatsApp Business](https://business.whatsapp.com/policy) y los [términos de la Cloud API](https://developers.facebook.com/docs/whatsapp/cloud-api).
- `enviar_mensaje` sigue siendo `dangerous=True` para WhatsApp exactamente igual que para las otras tres plataformas: exige confirmación humana explícita antes de enviarse (`ARCHITECTURE.md` §10.7) — WhatsApp no es un caso especial que se salte ese gate.

---

## Herramientas del agente

`edecan_messaging` (`packages/messaging/`) expone dos herramientas, gateadas por el flag de plan `connectors.messaging` (`docs/roadmap.md`):

| Tool | `dangerous` | Argumentos | Qué hace |
|---|---|---|---|
| `enviar_mensaje` | sí (requiere confirmación) | `plataforma` (`telegram`\|`discord`\|`slack`\|`whatsapp`), `destino`, `texto`, `plantilla` (opcional, solo WhatsApp), `idioma` (opcional, solo WhatsApp, default `es`) | Envía un mensaje real y visible — pasa por el mismo gate de confirmación humana que `enviar_correo`/`enviar_sms`/`publicar_social` (`ARCHITECTURE.md` §10.7). Al enviarse, registra `usage_events(kind="messages")` y una fila en `audit_log`. En WhatsApp, `plantilla` envía una plantilla pre-aprobada en vez de texto libre (obligatorio fuera de la ventana de 24h, ver arriba). |
| `leer_mensajes` | no | `plataforma`, `origen` (opcional solo en Telegram; no aplica a WhatsApp), `limite` (máx. 20) | Solo lectura: últimos mensajes del chat/canal. Con `plataforma: "whatsapp"` devuelve un mensaje de limitación en vez de intentar leer (ver «Limitación de lectura en v3» arriba) — nunca llega a resolver credenciales. |

Ambas resuelven la credencial del tenant desde su `TokenVault` (`edecan_messaging._creds.resolver_credenciales`, por `connector_key`) y responden con un mensaje claro si la plataforma todavía no está conectada.

---

## Bandeja unificada (web)

Además de las tools del agente de arriba, fase v4 agrega una superficie HTTP + web para que una persona lea/envíe mensajes directo desde el panel, sin pasar por el chat: `apps/api/edecan_api/routers/mensajes.py` (`/v1/mensajes`) y `/app/mensajes` en `apps/web`. Consume `packages/messaging/` TAL CUAL (`edecan_messaging._creds.resolver_credenciales` para la credencial, `edecan_messaging.clients`/`.whatsapp` para hablar con cada API oficial) — no agrega un mecanismo de conexión nuevo: sigue siendo la MISMA cuenta/bot/número que el tenant ya conectó como se explica arriba en este documento (`PUT /v1/connectors/{key}/credentials`, `GET /v1/connectors/slack/authorize`, etc.). Gateada por el mismo flag de plan que las tools, `connectors.messaging`.

### Endpoints

| Endpoint | Qué hace |
|---|---|
| `GET /v1/mensajes/canales` | Estado de las 4 plataformas para el tenant actual: `[{"canal", "conectado", "puede_leer"}]`. `puede_leer` es `false` únicamente para `whatsapp` (ver «Limitación de lectura en v3» arriba) — así la web no tiene que memorizar esa asimetría por su cuenta, la refleja tal cual la devuelve la API. |
| `GET /v1/mensajes?canal=&origen=&limite=` | Últimos mensajes de UN canal ya conectado, normalizados a `{"canal", "remitente", "texto", "fecha", "chat_id"}`. `origen` es obligatorio salvo en Telegram (mismo criterio que `leer_mensajes`). `400` si el canal no existe, si es `whatsapp`, si falta `origen` donde hace falta, o si el canal no está conectado (mensaje accionable, ver abajo). |
| `POST /v1/mensajes/enviar {"canal", "destinatario", "texto"}` | Envía un mensaje real vía el cliente oficial del canal y deja un rastro en `audit_log` (acción `mensajes.enviado`). Acepta además `plantilla`/`idioma` opcionales (solo WhatsApp, mismo par de argumentos que `enviar_mensaje`) para no dejar la ventana de 24h sin salida desde esta API — el compositor simple de la web (destinatario + texto + botón Enviar) no expone ese campo, pero cualquier otro consumidor de la API sí puede usarlo. |

### Por qué `POST /enviar` no exige un segundo paso de confirmación

`enviar_mensaje` vía el AGENTE es `dangerous=True` (tabla de arriba) porque ahí un LLM decide enviar por su cuenta — `Agent.run_turn` exige `confirmation_required` antes de ejecutarla (`ARCHITECTURE.md` §10.7). `POST /v1/mensajes/enviar` es distinto: lo dispara un humano haciendo click en el botón «Enviar» del compositor, viendo el destinatario y el texto en pantalla — ese click YA ES la confirmación explícita, con el mismo criterio que enviar un mensaje desde cualquier app de mensajería real (Telegram, Slack, WhatsApp mismos) nunca piden una segunda confirmación aparte del propio botón de enviar.

### Asimetrías por canal — honestas, no escondidas

| Canal | Leer (`GET`) | Enviar (`POST /enviar`) |
|---|---|---|
| Telegram | Sí — `origen` opcional (vacío usa los últimos *updates* pendientes del bot) | Sí |
| Discord | Sí — `origen` (id de canal) obligatorio | Sí |
| Slack | Sí — `origen` (id o `#nombre` de canal) obligatorio | Sí |
| WhatsApp | **No** (`puede_leer=false`, `400` si se intenta) — Cloud API entrega mensajes entrantes solo por webhook, fuera de alcance (ver arriba) | Sí, con la misma regla de ventana de 24h/plantilla que la tool del agente |

### Formato de `fecha` — distinto por canal, a propósito sin normalizar

`GET /v1/mensajes` devuelve `fecha` como el valor crudo de cada plataforma, convertido a texto tal cual, SIN reinterpretarlo a una zona horaria o formato común — reinterpretar mal un timestamp es peor que no reinterpretarlo:

- **Telegram**: epoch Unix en segundos (p. ej. `"1735689600"`).
- **Discord**: ISO 8601 con offset (p. ej. `"2026-07-08T14:30:00.000000+00:00"`).
- **Slack**: `"segundos.microsegundos"` (el mismo `ts` que usa la Web API de Slack como identificador de mensaje, p. ej. `"1512085950.000216"`).

Quien consuma la API decide cómo mostrarlo — la web (`components/mensajes/MensajesList.tsx`) hace un *best-effort* de formateo local por canal (con el crudo como respaldo si no puede parsearlo), pero eso vive del lado del cliente, nunca en la respuesta de la API.

### Bring-your-own — sin excepciones, igual que el resto de este documento

Esta bandeja no crea una cuenta/bot/número nuevo ni un mecanismo de conexión propio: lee exactamente lo que el tenant ya conectó en `/app/conectores` (secciones de arriba). Si un canal no está conectado, tanto `GET /v1/mensajes` como `POST /v1/mensajes/enviar` responden `400` con un mensaje accionable que apunta a `/app/conectores` (el mismo texto que ya arma `MessagingNotConnectedError` en `edecan_messaging._creds`, reutilizado tal cual — nunca un mensaje HTTP distinto al que ya usan las tools del agente).

---

## Exclusiones

### Signal — excluida permanentemente

Signal **no tiene una API pública oficial** para bots o integraciones de terceros — su protocolo está diseñado deliberadamente en torno a clientes verificados y cifrado de extremo a extremo entre personas, sin una superficie de "cuenta de aplicación" equivalente a un bot de Telegram/Discord o una app de Slack. Cualquier forma de automatizar Signal hoy implica reimplementar/envolver un cliente no oficial (`signal-cli` u otros proyectos de la comunidad), lo que viola la regla dura de este proyecto de **solo integrar APIs oficiales** (`ARCHITECTURE.md` §0.3). Por eso Signal queda excluida — no es una omisión temporal ni una cuestión de prioridad, es la misma postura de "sin API oficial, sin integración" que aplica al resto del producto.

### WhatsApp — ya NO es una exclusión (implementada desde fase v3)

Hasta v2 (fase v2), WhatsApp Cloud API estaba documentada aquí como plan P1, sin implementar. Desde v3 (fase v3, `ARCHITECTURE.md` §12.b) el ENVÍO ya es real — ver la sección [«WhatsApp (Cloud API oficial)»](#whatsapp-cloud-api-oficial) arriba. Lo único que sigue pendiente (deliberadamente fuera de alcance, no una omisión) es la LECTURA de mensajes entrantes, que exige montar un webhook público — ver «Limitación de lectura en v3» en esa misma sección para el detalle y el roadmap.

---

Ver también: [`conectores.md`](./conectores.md) para el resto de integraciones OAuth (Google, Microsoft, Meta, X, YouTube), [`voz-telefonia.md`](./voz-telefonia.md) para el patrón no-OAuth de Twilio en el que se basa la conexión de Telegram/Discord, y [`api.md`](./api.md) para la referencia completa de rutas HTTP.
