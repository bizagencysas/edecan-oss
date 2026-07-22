# Notificaciones push nativas (APNs/FCM)

Edecán puede enviar recordatorios como **notificación push nativa** a tu teléfono
(iOS vía APNs, Android vía FCM) además de dejarlos siempre como mensaje de chat en
la conversación "Recordatorios". Es **bring-your-own** al pie de la letra
(`ARCHITECTURE.md` §14 y [`credenciales.md`](./credenciales.md)): conectas TU PROPIA `.p8` de Apple Push Notification
service (de TU cuenta de Apple Developer) y/o TU PROPIO service account de Firebase
Cloud Messaging (de TU proyecto de Firebase) — Edecán nunca guarda ni opera una
credencial de plataforma de push compartida entre tenants.

Cubre notificaciones push nativas para recordatorios, respuestas asíncronas,
resúmenes de llamadas y eventos importantes: trabajo o misión terminada/fallida,
contenido creado/publicado, diseño/exportación lista, archivo/PDF listo y
autorreparación terminada. El registro persistente de `reminders` se complementa
con un canal `mobile` nativo, además de los canales `web`/`voice`/`phone`/`api`.

## El push es SIEMPRE además del mensaje de chat, nunca en su lugar

Un recordatorio con `channel="mobile"` **primero** queda registrado como mensaje
en la conversación "Recordatorios" (igual que cualquier otro canal) y **solo
después** se intenta el push. Si no conectaste APNs/FCM, si tu teléfono no
registró ningún `push_token` todavía, o si el envío falla por cualquier motivo
(red caída, token vencido, credencial mal formada), el recordatorio de todos
modos queda ahí — la entrega push nunca puede hacer que se "pierda" un
recordatorio (`apps/worker/edecan_worker/handlers/send_reminder.py`,
`apps/worker/edecan_worker/push.py`).

Una llamada terminal sigue el mismo orden: primero guarda su resumen
estructurado y un evento de actividad; después el job
`notify_phone_call_summary` intenta el push. El aviso solo dice que hay un
resumen disponible en Actividad. Nunca muestra teléfono, participantes,
objetivo, transcripción, puntos clave ni compromisos en la pantalla bloqueada.

El emisor universal aplica el mismo contrato: guarda una actividad mínima en
`audit_log`, confirma esa transacción y solo después intenta APNs/FCM. Un
`event_key` opaco hace idempotente la transición: si el mismo job llega dos
veces, no duplica ni la actividad ni el push. Nunca persiste o envía prompts,
resultados, nombres de archivo o mensajes de error; el texto visible es una
frase genérica y el payload solo contiene rutas e identificadores opacos.

## 1. Setup de APNs (iOS) — en TU cuenta de Apple Developer

Coherente con que cada cliente ya compila su propia app iOS con SU PROPIO bundle
id y SU PROPIA cuenta de Apple Developer Program de pago (ver
[`movil-ios.md`](./movil-ios.md), "Antes de compilar para tu iPhone: bundle id y equipo"):

1. Necesitas membresía activa de Apple Developer Program y un **Bundle ID
   explícito y único** (por ejemplo `com.tuempresa.edecan`). En *Certificates,
   Identifiers & Profiles → Identifiers*, abre ese App ID y activa **Push
   Notifications**. En Xcode, conserva la capability *Push Notifications*;
   este repo ya declara `aps-environment` en `EdecanApp.entitlements`.
2. Regenera los perfiles de desarrollo/distribución después de activar la
   capability y firma la app con tu Team. El simulador sirve para compilar y
   probar avisos locales; el token APNs real exige una app correctamente
   firmada e instalada.
3. En *Certificates, Identifiers & Profiles → Keys → +*, crea una key con
   **Apple Push Notifications service (APNs)**. Apple entrega el `.p8` una
   sola vez. Una key de autenticación sirve para sandbox y producción y se
   puede revocar desde la cuenta.
4. Anota **Key ID**, **Team ID** y el Bundle ID exacto de la app.
5. Abre el archivo `.p8` con un editor de texto — su contenido completo
   (incluidas las líneas `-----BEGIN PRIVATE KEY-----`/`-----END PRIVATE
   KEY-----`) es lo que pegas en `p8_key` al conectar (ver más abajo). Tu
   cliente HTTP se encarga de escapar los saltos de línea correctamente al
   mandar JSON — no hace falta que hagas nada especial con ellos.

La guía oficial de Apple para registrar la app y tratar tokens como valores
rotables está en [Registering your app with APNs](https://developer.apple.com/documentation/usernotifications/registering-your-app-with-apns);
la creación de la key está en [Communicate with APNs using authentication tokens](https://developer.apple.com/help/account/capabilities/communicate-with-apns-using-authentication-tokens/).

Esta clave es **tuya**: vive en tu cuenta de Apple Developer, la puedes revocar
en cualquier momento desde *Certificates, Identifiers & Profiles* → *Keys*.
Edecán nunca pide ni almacena una credencial de push "de plataforma".

## 2. Setup de FCM (Android) — en TU proyecto de Firebase

1. Entra a la **[consola de Firebase](https://console.firebase.google.com)** y
   crea/elige tu proyecto. Agrega una app Android cuyo package name coincida
   **exactamente** con `applicationId` (por defecto, el placeholder
   `cc.edecan.app`; para distribución debes usar el tuyo).
2. Descarga `google-services.json` y colócalo localmente en
   `apps/mobile/android/androidApp/google-services.json`. Está ignorado por
   Git. No se copia al backend ni se comparte entre instalaciones. Si el
   archivo no existe, Gradle no aplica el plugin Google Services y el checkout
   OSS sigue compilando con avisos locales.
3. Habilita Cloud Messaging API en el proyecto. La app usa el módulo principal
   `firebase-messaging` (no el módulo KTX, retirado del BoM moderno) y recibe
   rotaciones en `onNewToken`.
4. **Configuración del proyecto** (ícono de engranaje) → **Cuentas de
   servicio** → **Generar nueva clave privada**. Firebase descarga un archivo
   JSON con `type`, `project_id`, `private_key`, `client_email`, etc.
5. Abre ese archivo y copia su contenido COMPLETO tal cual — es lo que pegas en
   `service_account_json` al conectar (ver más abajo; `project_id` se deriva
   automáticamente del propio JSON, no hace falta que lo repitas a mano, aunque
   puedes pasarlo explícito si prefieres).

La configuración cliente y las versiones recomendadas están en la
[guía oficial de Firebase para Android](https://firebase.google.com/docs/android/setup);
el permiso de Android 13+ y la rotación del token están en
[FCM para Android](https://firebase.google.com/docs/cloud-messaging/android/get-started).

`google-services.json` contiene identificadores cliente, pero el JSON de la
**cuenta de servicio sí es un secreto de servidor**: nunca se coloca dentro del
APK. Esta clave es **tuya**: vive en tu proyecto de Firebase/GCP, la puedes revocar
en cualquier momento desde *Cuentas de servicio* de la consola. Edecán nunca
pide ni almacena una credencial de push "de plataforma".

## 3. Conectarlo en Edecán

`PUT /v1/devices/push/credentials` (flag de plan `notifications.push`, `True`
en los 4 planes hoy):

```json
{
  "apns": {
    "team_id": "TU_TEAM_ID_AQUI",
    "key_id": "TU_KEY_ID_AQUI",
    "bundle_id": "com.tuempresa.edecan",
    "p8_key": "-----BEGIN PRIVATE KEY-----\nTU_CLAVE_P8_AQUI\n-----END PRIVATE KEY-----",
    "environment": "production"
  },
  "fcm": {
    "service_account_json": "{\"type\":\"service_account\",\"project_id\":\"TU_PROYECTO_AQUI\", ...}"
  }
}
```

Puedes mandar **solo `apns`, solo `fcm`, o ambos** — al menos uno es obligatorio.
Un `PUT` con solo uno de los dos **nunca borra el otro** si ya lo habías
conectado antes (se lee la config existente y se sobreescribe solo la clave que
mandaste esta vez) — así puedes conectar iOS hoy y Android la semana que viene
sin perder lo primero.

**Validación de FORMA, sin red** (nunca llama a Apple/Google al guardar):

- `p8_key` debe parsear como una clave privada de curva elíptica (EC) válida.
- `service_account_json` debe ser JSON válido con `"type": "service_account"` y
  traer `client_email`/`private_key`.
- Si algo no tiene forma válida, `PUT` responde `400` con el detalle exacto —
  nada se guarda hasta que TODO lo que mandaste pasa su validación.

Se guarda cifrado en tu `TokenVault` (`ARCHITECTURE.md` §10.4, connector_key
`"push"`) — nunca en texto plano, nunca en logs.

- `GET /v1/devices/push/status` → `{apns: bool, fcm: bool, devices_con_token:
  int}` — qué tienes conectado y cuántos dispositivos de tu cuenta ya
  registraron un `push_token`.
- `DELETE /v1/devices/push/credentials` → desconecta ambos (idempotente). Los
  `push_token` que ya hubieran registrado tus dispositivos NO se borran — un
  push posterior sin credencial conectada simplemente no se envía, se loguea
  como advertencia y se cuenta como "0 enviados".

## 4. Registrar el `push_token` de un dispositivo

Cuando tu app nativa (iOS/Android) recibe su token de push del sistema
operativo, lo registra contra Edecán:

```
POST /v1/devices/{device_id}/push-token
{"push_token": "<el token que te dio APNs/FCM>", "push_platform": "apns"}
```

`push_platform` es `"apns"` o `"fcm"`. `device_id` es el id que devolvió
`POST /v1/devices` al emparejar el dispositivo (`ARCHITECTURE.md` §13.f) — a
diferencia de otras operaciones de ese router (`heartbeat`/`revoke`, que
cualquier miembro del tenant puede tocar sobre cualquier dispositivo), esta
SOLO funciona sobre un dispositivo que sea tuyo (tu propio `user_id`) y esté
`active` — `404` si no.

`DELETE /v1/devices/{device_id}/push-token` limpia el registro (p. ej. al
cerrar sesión en la app o desinstalarla).

Las apps ya hacen este ciclo completo:

- El permiso se pide desde **Tú → Avisos**, con contexto; nunca bloquea el
  onboarding.
- En cada arranque autorizado se solicita el token vigente y se hace `POST`.
  `onNewToken`/el callback APNs reemplaza el anterior sin registrarlo en logs.
- Al desvincular, la app intenta `DELETE` antes de revocar la sesión.
- El tap acepta solo las rutas cerradas `assistant`, `activity`, `settings`,
  `create` y `remote`; cualquier valor desconocido abre el asistente.

## 5. Preferencias por categoría

Cada persona controla sus avisos con `GET|PUT
/v1/devices/push/preferences`. El `PUT` es parcial: `{"content": false}`
conserva intactas las demás categorías. Los defaults razonables son:

```json
{"work":true,"content":true,"design":true,"files":true,"self_repair":true}
```

Apagar una categoría evita únicamente el push; la actividad durable permanece
disponible para que el resultado no se pierda.

## 6. El canal `"mobile"` en recordatorios

`POST /v1/reminders {..., "channel": "mobile"}` (o la tool `crear_recordatorio`
del agente, con la limitación que se explica abajo) hace que, al vencer el
recordatorio, además del mensaje de chat de siempre, `edecan_worker.push.
enviar_push_a_usuario` mande un push nativo a TODOS tus dispositivos `active`
con `push_token` registrado — despachando cada uno por su `push_platform`
(APNs o FCM según corresponda). Si tienes varios dispositivos, todos reciben el
push.

La tool `crear_recordatorio` y ambos clientes móviles usan `mobile` por defecto.
Las apps también programan una notificación local con id estable. Así un build
sin APNs/FCM propio sigue avisando en el dispositivo y el mensaje de chat sigue
siendo la fuente durable.

## 7. API HTTP completa (`/v1/devices/*`, flag `notifications.push`)

| Ruta | Qué hace |
|---|---|
| `POST /v1/devices/{id}/push-token` | Registra `push_token`/`push_platform` de un dispositivo TUYO y `active`. `204`. `404` si no es tuyo/no existe/no está activo. |
| `DELETE /v1/devices/{id}/push-token` | Limpia el registro de push de un dispositivo TUYO. `204`. `404` si no es tuyo/no existe. |
| `GET /v1/devices/push/preferences` | Preferencias efectivas del usuario actual por categoría. |
| `PUT /v1/devices/push/preferences` | Actualización parcial de preferencias por categoría. |
| `PUT /v1/devices/push/credentials` | Pegar y validar (sin red) tu APNs y/o FCM — ver arriba. `204`. |
| `GET /v1/devices/push/status` | Qué tienes conectado + cuántos dispositivos de tu cuenta ya registraron token. |
| `DELETE /v1/devices/push/credentials` | Desconectar (idempotente). |

## Cómo funciona el envío por dentro (`edecan_worker.push`)

Las llamadas entrantes usan el mismo contrato universal. El webhook firmado
confirma primero `phone_calls` y un evento `incoming`; después encola solo el
`call_id`. El worker vuelve a comprobar ambos, registra una actividad
idempotente, consulta la preferencia `work` y recién entonces intenta un push
con título/cuerpo genéricos y `edecan://activity/<call_id>`. Número, nombre,
objetivo y transcripción nunca aparecen en la pantalla bloqueada. El resumen de
cierre usa su job terminal independiente y no se genera ni se duplica aquí.

- **APNs**: se construye un JWT de proveedor firmado **ES256** (`iss=team_id`,
  `iat`, header `kid=key_id`) con TU `.p8`, y se hace `POST
  https://api.push.apple.com/3/device/{token}` (o `api.sandbox.push.apple.com`
  si conectaste `"environment": "sandbox"`) con `apns-topic=bundle_id`,
  `apns-push-type=alert` y el body `{"aps":{"alert":{"title","body"},
  "sound":"default"}}`.
- **FCM**: se construye un JWT-bearer OAuth2 firmado **RS256** con la
  `private_key` de tu service account, se canjea por un access_token en
  `oauth2.googleapis.com/token`, y se hace `POST https://fcm.googleapis.com/v1/
  projects/{project_id}/messages:send`.
- **Navegación segura**: APNs recibe la metadata opaca fuera de `aps`; FCM en
  `message.data`. El allowlist admite `route`, `kind`, `event`, `event_key`,
  `chat_id`, `artifact_id`, `resource_id` y `deeplink`; rechaza cualquier
  clave arbitraria.
- **Multi-dispositivo**: si tienes varios dispositivos con token registrado, se
  intenta el envío a TODOS — un fallo en uno (red caída, token vencido) no
  frena el envío a los demás.
- **Limpieza automática de tokens muertos**: si el proveedor confirma que un
  token ya no sirve (APNs `410 Unregistered`/`400 BadDeviceToken`; FCM `404
  UNREGISTERED`), Edecán limpia `push_token`/`push_platform` de ese dispositivo
  en `devices` — la próxima vez que tu app se abra y vuelva a registrar un
  token fresco, todo sigue funcionando normal.
- **Ausencia de credencial o de dispositivo con token**: se loguea como
  advertencia y se cuenta como "0 enviados" — nunca revienta el job del
  recordatorio.

### HTTP/2 en APNs — nota técnica

El endpoint moderno de APNs está pensado para HTTP/2, pero este repo no trae el
paquete opcional `h2` de `httpx` — los requests salen por HTTP/1.1 puro, que en
la práctica Apple acepta igual en la inmensa mayoría de los casos (ver el
docstring completo de `apps/worker/edecan_worker/push.py` para el detalle). Si
alguna vez ves pushes de APNs que no llegan y todo lo demás (credenciales,
token, topic) está bien, esa es la primera pista a revisar.

## Fallback OSS y decisiones conscientes

- Sin `.p8`/service account en el servidor no hay push remoto, pero la app no
  falla: avisos locales cubren recordatorios y resultados observados por la app
  (contenido, trabajos y llamadas). Los payloads remotos usan los mismos canales
  y rutas.
- Desinstalar una app no permite ejecutar el `DELETE`; APNs/FCM limpiará tokens
  muertos cuando el proveedor los rechace. Al reinstalar, el token nuevo se
  vuelve a registrar.
- Los productores asíncronos `run_mission`, `generate_content` e `ingest_file`
  ya usan el emisor universal. Otros productores backend pueden construir
  `edecan_core.notifications.ImportantNotificationEvent`; deben entregar solo
  después de confirmar el resultado principal.
- Los flujos síncronos de Design Studio, publicación y autorreparación ya
  tienen tipos de evento reservados (`design_ready`, `design_export_ready`,
  `content_published`, `self_repair_completed`). Su punto de integración debe
  vivir en el orquestador que confirma el turno, no dentro de la tool mientras
  su transacción sigue abierta. El hook post-commit de esos productores queda
  pendiente: debe publicar el contrato portable
  `edecan_core.notifications.ImportantNotificationEvent` hacia el worker, sin
  importar `apps/worker` desde `apps/api`. Esta frontera evita avisar «listo»
  antes de que el artefacto o reparación sea durable.
- No hay reintentos automáticos de un push fallido (a diferencia de los jobs en
  sí, que sí reintentan con backoff, `ARCHITECTURE.md` §10.11) — un push es un
  intento único, best-effort, por diseño: el recordatorio real (el mensaje de
  chat) ya quedó entregado de forma confiable antes de siquiera intentarlo.
- Ningún job en segundo plano vuelve a sincronizar el `access_token` de FCM
  entre envíos — cada envío canjea uno nuevo. Para el volumen de recordatorios
  personales que maneja Edecán no es un problema de rendimiento real; si algún
  día se necesitara, cachear el `access_token` de FCM (válido ~1 hora) sería
  la optimización natural.
