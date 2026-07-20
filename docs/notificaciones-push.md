# Notificaciones push nativas (APNs/FCM)

Edecán puede enviar recordatorios como **notificación push nativa** a tu teléfono
(iOS vía APNs, Android vía FCM) además de dejarlos siempre como mensaje de chat en
la conversación "Recordatorios". Es **bring-your-own** al pie de la letra
(`ARCHITECTURE.md` §14 y [`credenciales.md`](./credenciales.md)): conectas TU PROPIA `.p8` de Apple Push Notification
service (de TU cuenta de Apple Developer) y/o TU PROPIO service account de Firebase
Cloud Messaging (de TU proyecto de Firebase) — Edecán nunca guarda ni opera una
credencial de plataforma de push compartida entre tenants.

Cubre notificaciones push nativas para recordatorios y respuestas asíncronas. El
registro persistente de `reminders` se complementa con un canal `mobile` nativo,
además de los canales `web`/`voice`/`phone`/`api`.

## El push es SIEMPRE además del mensaje de chat, nunca en su lugar

Un recordatorio con `channel="mobile"` **primero** queda registrado como mensaje
en la conversación "Recordatorios" (igual que cualquier otro canal) y **solo
después** se intenta el push. Si no conectaste APNs/FCM, si tu teléfono no
registró ningún `push_token` todavía, o si el envío falla por cualquier motivo
(red caída, token vencido, credencial mal formada), el recordatorio de todos
modos queda ahí — la entrega push nunca puede hacer que se "pierda" un
recordatorio (`apps/worker/edecan_worker/handlers/send_reminder.py`,
`apps/worker/edecan_worker/push.py`).

## 1. Setup de APNs (iOS) — en TU cuenta de Apple Developer

Coherente con que cada cliente ya compila su propia app iOS con SU PROPIO bundle
id y SU PROPIA cuenta de Apple Developer Program de pago (ver
[`movil-ios.md`](./movil-ios.md), "Antes de compilar para tu iPhone: bundle id y equipo"):

1. Entra a **[developer.apple.com/account](https://developer.apple.com/account)**
   → *Certificates, Identifiers & Profiles* → **Keys** → **+** (crear una key
   nueva).
2. Marca **Apple Push Notifications service (APNs)**, ponle un nombre (p. ej.
   "Edecán push") y crea la key. Apple te deja **descargar el archivo `.p8`
   UNA SOLA VEZ** — guárdalo, no lo puedes volver a descargar.
3. Anota el **Key ID** (10 caracteres, se muestra al crear la key y queda listado
   en *Keys*).
4. Anota tu **Team ID** (arriba a la derecha de la consola de developer, o en
   *Membership*).
5. El **Bundle ID** es el mismo que usaste al compilar tu app iOS
   (`docs/movil-ios.md`) — p. ej. `com.tuempresa.edecan`.
6. Abre el archivo `.p8` con un editor de texto — su contenido completo
   (incluidas las líneas `-----BEGIN PRIVATE KEY-----`/`-----END PRIVATE
   KEY-----`) es lo que pegas en `p8_key` al conectar (ver más abajo). Tu
   cliente HTTP se encarga de escapar los saltos de línea correctamente al
   mandar JSON — no hace falta que hagas nada especial con ellos.

Esta clave es **tuya**: vive en tu cuenta de Apple Developer, la puedes revocar
en cualquier momento desde *Certificates, Identifiers & Profiles* → *Keys*.
Edecán nunca pide ni almacena una credencial de push "de plataforma".

## 2. Setup de FCM (Android) — en TU proyecto de Firebase

1. Entra a la **[consola de Firebase](https://console.firebase.google.com)** con
   la cuenta de Google que administra tu proyecto (el mismo proyecto que usa tu
   app Android, `docs/movil-android.md`) — o crea un proyecto nuevo si todavía
   no tienes uno.
2. **Configuración del proyecto** (ícono de engranaje) → **Cuentas de
   servicio** → **Generar nueva clave privada**. Firebase descarga un archivo
   JSON con `type`, `project_id`, `private_key`, `client_email`, etc.
3. Abre ese archivo y copia su contenido COMPLETO tal cual — es lo que pegas en
   `service_account_json` al conectar (ver más abajo; `project_id` se deriva
   automáticamente del propio JSON, no hace falta que lo repitas a mano, aunque
   puedes pasarlo explícito si prefieres).

Esta clave es **tuya**: vive en tu proyecto de Firebase/GCP, la puedes revocar
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

### Nota honesta: el registro automático desde las apps móviles llega en la
### siguiente ola

Los endpoints de arriba (`POST`/`DELETE /{id}/push-token`) ya están completos y
funcionando — lo que **todavía no existe** es el código Swift/Kotlin dentro de
`apps/mobile/ios`/`apps/mobile/android` que pida el token al sistema operativo
(`UNUserNotificationCenter`/Firebase Messaging SDK) y lo mande automáticamente
a este endpoint al arrancar la app. Mientras esa pieza aterriza, puedes
registrar un `push_token` a mano vía la API (por ejemplo con `curl` o desde
Configuración → un cliente HTTP cualquiera) para probar el flujo de punta a
punta.

## 5. El canal `"mobile"` en recordatorios

`POST /v1/reminders {..., "channel": "mobile"}` (o la tool `crear_recordatorio`
del agente, con la limitación que se explica abajo) hace que, al vencer el
recordatorio, además del mensaje de chat de siempre, `edecan_worker.push.
enviar_push_a_usuario` mande un push nativo a TODOS tus dispositivos `active`
con `push_token` registrado — despachando cada uno por su `push_platform`
(APNs o FCM según corresponda). Si tienes varios dispositivos, todos reciben el
push.

**Nota para quien siga leyendo**: `packages/toolkit/edecan_toolkit/
recordatorios.py::_CANALES_VALIDOS` (la tool que usa el chat) todavía NO incluye
`"mobile"` en su propio allowlist — sigue quedando fuera de esta ola (ese
paquete no forma parte de este work package), así que un recordatorio creado
por el AGENTE con `channel="mobile"` hoy cae en silencio a `"web"` (sin push),
mientras que uno creado directo por `POST /v1/reminders` sí lo respeta.
Sincronizar ambos allowlists queda pendiente para un WP de seguimiento.

## 6. API HTTP completa (`/v1/devices/*`, flag `notifications.push`)

| Ruta | Qué hace |
|---|---|
| `POST /v1/devices/{id}/push-token` | Registra `push_token`/`push_platform` de un dispositivo TUYO y `active`. `204`. `404` si no es tuyo/no existe/no está activo. |
| `DELETE /v1/devices/{id}/push-token` | Limpia el registro de push de un dispositivo TUYO. `204`. `404` si no es tuyo/no existe. |
| `PUT /v1/devices/push/credentials` | Pegar y validar (sin red) tu APNs y/o FCM — ver arriba. `204`. |
| `GET /v1/devices/push/status` | Qué tienes conectado + cuántos dispositivos de tu cuenta ya registraron token. |
| `DELETE /v1/devices/push/credentials` | Desconectar (idempotente). |

## Cómo funciona el envío por dentro (`edecan_worker.push`)

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

## Qué falta / decisiones conscientes

- El registro automático del `push_token` desde las apps nativas (Swift/Kotlin)
  queda para la siguiente ola — ver la nota honesta más arriba.
- `packages/toolkit/edecan_toolkit/recordatorios.py` (la tool del agente) no
  conoce todavía `channel="mobile"` — ver la nota de la sección 5.
- No hay reintentos automáticos de un push fallido (a diferencia de los jobs en
  sí, que sí reintentan con backoff, `ARCHITECTURE.md` §10.11) — un push es un
  intento único, best-effort, por diseño: el recordatorio real (el mensaje de
  chat) ya quedó entregado de forma confiable antes de siquiera intentarlo.
- Ningún job en segundo plano vuelve a sincronizar el `access_token` de FCM
  entre envíos — cada envío canjea uno nuevo. Para el volumen de recordatorios
  personales que maneja Edecán no es un problema de rendimiento real; si algún
  día se necesitara, cachear el `access_token` de FCM (válido ~1 hora) sería
  la optimización natural.
