# App Android nativa

Cliente Android nativo de Edecán: **Kotlin 2.4.0 + Compose Multiplatform**,
nunca React Native ni Flutter — ver "Por qué no React Native" más abajo.
Habla contra la misma API HTTP
que el panel web y la app de escritorio (`docs/api.md`) — self-host propio
o la app de escritorio Tauri de tu tenant, nunca un servidor "de fábrica"
compartido.

Este documento es la guía del **cliente final** (requisitos, build, firma,
instalación, emparejamiento). Para tocar el código fuente, ver también
[`../apps/mobile/android/README.md`](../apps/mobile/android/README.md)
(arranque rápido para desarrolladores, incluye la matriz de versiones
verificada).

## Requisitos

- Un servidor Edecán propio ya funcionando (self-host, o la app de
  escritorio Tauri de tu tenant) — la URL la pides en el primer paso del
  onboarding de la app.
- Para compilar: JDK 17+ y el Android SDK (`compileSdk`/`targetSdk` 37,
  `minSdk` 26 — Android 8.0 en adelante). Android Studio es lo más cómodo,
  pero no hace falta: las `cmdline-tools` de Google alcanzan (ver detalle
  en el README del código fuente).
- Un teléfono/tablet Android con "Orígenes desconocidos" habilitable — no
  hace falta Play Store ni una cuenta de desarrollador de Google (a
  diferencia de iOS, Android no exige una cuenta de pago para instalar tu
  propio APK).

## Build

```bash
cd apps/mobile/android
./gradlew :androidApp:assembleDebug
```

Genera `androidApp/build/outputs/apk/debug/androidApp-debug.apk` —
instalable directo con `adb install` o copiando el archivo al teléfono. El
`build.gradle.kts` de `androidApp` **no firma `debug` con nada especial**
(usa el keystore de debug genérico que genera el propio Android SDK,
válido solo para desarrollo local, nunca para distribuir).

## Instalación: USB / orígenes desconocidos — nunca Play Store

Esta app **nunca se publica en Google Play**. Se instala:

1. **Por USB con `adb`** (lo más simple mientras desarrollas):
   ```bash
   adb install apps/mobile/android/androidApp/build/outputs/apk/debug/androidApp-debug.apk
   ```
   Requiere haber activado "Depuración USB" en Opciones de desarrollador
   del teléfono (Ajustes → Acerca del teléfono → toca 7 veces "Número de
   compilación" para revelar el menú de desarrollador).
2. **Copiando el `.apk` al teléfono** (AirDrop-equivalente, cable, correo,
   tu propio servidor) y abriéndolo desde el explorador de archivos del
   teléfono — Android pide permiso puntual de "instalar apps
   desconocidas" para la app que uses para abrirlo (el navegador, Archivos,
   etc.), no un ajuste global inseguro para todo el sistema.

Ninguna de las dos vías depende de una cuenta de desarrollador de pago (a
diferencia de iOS/Apple Developer Program) — el único requisito es que el
propio dueño del teléfono habilite la instalación, de forma explícita, la
primera vez.

## Firma release con TU keystore

`androidApp/build.gradle.kts` **no declara ningún `signingConfig` para
`release`** — a propósito, regla dura del repo: nunca se firma un release
con una key compartida. `./gradlew :androidApp:assembleRelease` hoy genera
un `.apk` sin firmar (instalable solo tras firmarlo a mano). Para
distribuir un release real:

**1. Genera tu propio keystore** (una sola vez; guárdalo en un lugar
seguro, si lo pierdes no puedes volver a firmar actualizaciones de la
misma app):

```bash
keytool -genkeypair -v \
  -keystore TU_KEYSTORE_AQUI.jks \
  -alias TU_ALIAS_AQUI \
  -keyalg RSA -keysize 2048 -validity 10000
```

**2. Agrega un `signingConfig` propio** en
`apps/mobile/android/androidApp/build.gradle.kts` (dentro del bloque
`android { }`, dato real, no placeholder — nunca commitees esto con tus
contraseñas reales; usa variables de entorno o `local.properties`, ambos
ya gitignored):

```kotlin
android {
    signingConfigs {
        create("release") {
            storeFile = file("TU_KEYSTORE_AQUI.jks")
            storePassword = System.getenv("EDECAN_KEYSTORE_PASSWORD")
            keyAlias = "TU_ALIAS_AQUI"
            keyPassword = System.getenv("EDECAN_KEY_PASSWORD")
        }
    }
    buildTypes {
        release {
            signingConfig = signingConfigs.getByName("release")
            // ... resto de la config de `release` que ya existe.
        }
    }
}
```

**3. Cambia el `applicationId`** (`cc.edecan.app` es un placeholder de
desarrollo — un `applicationId` no puede repetirse entre instalaciones que
convivan en el mismo dispositivo, y dos clientes distintos firmando con
keystores distintos bajo el mismo id se pisarían al actualizar).

**4. Compila y firma:**

```bash
./gradlew :androidApp:assembleRelease
adb install androidApp/build/outputs/apk/release/androidApp-release.apk
```

## Emparejamiento al primer arranque

La app pide, en un onboarding de 2 pasos (mismo principio de "pocos
clicks" que la app de escritorio, `docs/roadmap.md`):

1. **La URL de tu servidor** — self-host propio o tu app de escritorio
   Tauri. Sin servidor "de fábrica": cada cliente trae el suyo.
2. **Iniciar sesión** (correo + contraseña, + código TOTP si tienes 2FA
   activado, `POST /v1/auth/login`) **o crear una cuenta nueva** (correo +
   contraseña + nombre de tu negocio/tenant, `POST /v1/auth/register` —
   botón "¿No tienes cuenta? Regístrate" en el mismo paso; crea tenant +
   usuario owner + persona por defecto y deja la sesión iniciada de una).

**El "emparejamiento" sigue siendo, ante todo, tener sesión iniciada** —
mientras el refresh token (30 días de vida, `ARCHITECTURE.md` §10.12) siga
guardado y cifrado en el teléfono (`EncryptedSharedPreferences`, respaldado
por el Android Keystore), el dispositivo cuenta como emparejado. Cerrar
sesión desde Ajustes borra los tokens y vuelve a mostrar el
onboarding — la URL del servidor SÍ se conserva, para no tener que volver a
escribirla.

**Además, desde fase v4, un login/registro exitoso registra el
dispositivo de verdad**: `POST /v1/devices {nombre: "<fabricante> <modelo>",
plataforma: "android", kind: "mobile", fingerprint: ANDROID_ID}` (contrato
de fase v4, tabla `devices`, `docs/roadmap.md`) — el `id` que
devuelve el servidor se guarda local (no cifrado — un id de dispositivo no
autentica nada por sí solo) y se usa para `DELETE /v1/devices/{id}` al
cerrar sesión, revocando el emparejamiento en el servidor. Ambas llamadas
son **mejor esfuerzo**: `EdecanApi.emparejarDispositivo`/`.revocarDispositivo`
nunca lanzan — si `/v1/devices` todavía no existe del lado del servidor
(`404`, fase v4 corriendo en paralelo) o falla por cualquier otro motivo,
el login/logout de todas formas sigue su curso normal. Emparejamiento real
por **QR + verificación de *fingerprint*** (en vez de simplemente confiar
en la sesión) sigue diseñado en [`control-remoto.md`](./control-remoto.md)
§4/§9 como el siguiente paso — ver "Roadmap" más abajo.

## Arquitectura

- **`shared/`** (Kotlin Multiplatform): toda la capa de red/dominio contra
  `/v1/*` vive aquí — modelos serializables (`Models.kt`), cliente HTTP con
  Ktor (`ApiClient.kt`, clase `EdecanApi`), cliente SSE para el chat
  (`SseClient.kt`) y el contrato de persistencia de tokens (`TokenStore.kt`
  + `TokenStore.android.kt`). Declara además targets `iosArm64`/
  `iosSimulatorArm64` para consumo **futuro** — hoy el cliente iOS real
  (`apps/mobile/ios/EdecanKit`) es un Swift Package independiente, sin
  relación de compilación con este módulo: mismo contrato de API HTTP, dos
  implementaciones nativas por ahora, con la puerta abierta a converger en
  este módulo compartido el día que se decida.
- **`androidApp/`**: el APK — Compose Multiplatform puro (sin
  `androidx.navigation`, sin Material Icons Extended): tres destinos
  assistant-first — **Edecan**, **Actividad** y **Ajustes** — conmutados con
  estado local simple e iconos emoji. Encima de esos tres destinos,
  `nav/RootNav.kt` agrega un segundo nivel de navegación de un solo paso
  (mismo `remember { mutableStateOf(...) }` local, sin `androidx.navigation`
  tampoco) para "Misiones"/"Automatizaciones"/"Recordatorios" (`Pantallas
  v5`, fase v5) y "Remoto" (fase v6, ver ["Pantalla Remoto"](#pantalla-remoto)
  más abajo), Voz desde el micrófono de Chat e IDE/Negocios desde el modo
  avanzado de Ajustes; no son pestañas nuevas. `ViewModel`s con `StateFlow`, uno por
  pantalla con lógica real: `SessionViewModel` (emparejamiento/sesión,
  registro, `POST`/`DELETE /v1/devices`), `ChatViewModel` (chat SSE +
  confirmaciones), `NegociosViewModel` (KPIs + facturas), `VozViewModel`
  (push-to-talk), `PerfilViewModel` (credenciales LLM), `IdeViewModel`
  (árbol/archivo de solo lectura), `MisionesViewModel` (misiones + *polling*),
  `AutomatizacionesViewModel` (Switch optimista + corridas),
  `RecordatoriosViewModel` (pendientes/completados) y `RemotoViewModel`
  (visor + input remoto, *polling*). `ui/components/
  DonutChart.kt` dibuja la dona de "ventas por canal" a mano con `Canvas` de
  Compose — cero dependencias de charting; `ui/components/
  FechaHoraPickers.kt` envuelve `DatePicker`/`TimePicker` de Material3 para
  el alta de un recordatorio.

### Por qué NO React Native (ni Flutter)

Mandato permanente del producto, no una preferencia de este work package.
Dos capacidades que Edecán ya tiene en su roadmap para el móvil exigen
acceso nativo de bajo nivel que un framework de UI multiplataforma basado
en JavaScript/Dart no da con la misma calidad ni el mismo control:

- **Control remoto del Mac/PC desde el teléfono** (el móvil como *visor*,
  diseño completo en [`control-remoto.md`](./control-remoto.md) §9):
  video de baja latencia vía WebRTC real (`libwebrtc`, no un wrapper), con
  la app móvil decodificando y renderizando frames a la máxima tasa que dé
  el dispositivo — el tipo de trabajo donde el puente JS↔nativo de React
  Native introduce *jank* y complejidad de mantenimiento reales.
- **Input injection / grabación de audio nativa / notificaciones del
  sistema** (`NotificationListenerService` para "control del teléfono",
  `docs/roadmap.md`): APIs de plataforma que penden de servicios/
  permisos de Android específicos, más simples y más confiables de
  mantener en Kotlin directo que a través de un puente de plugins.

Kotlin Multiplatform (el módulo `shared/` de este mismo proyecto) resuelve
además el problema que React Native "resolvía" del otro lado — compartir
la capa de red/dominio entre plataformas — sin pagar el costo del puente en
UI ni renunciar a APIs nativas cuando hagan falta.

### Qué es real hoy (fase v4, además de auth/chat de la iteración anterior)

- **Negocios real**: `NegociosScreen` — KPIs del mes (`GET
  /v1/negocios/kpis`: ingresos/gastos/beneficio/nuevos clientes/facturado/
  cobrado en tarjetas), dona de "ventas por canal" dibujada a mano con
  `Canvas` de Compose (`ui/components/DonutChart.kt`, cero dependencias de
  charting), actividad reciente y últimas facturas (`GET
  /v1/negocios/facturas`) con chip de estado por color. Solo lectura —
  crear/cambiar el estado de una factura sigue siendo del panel web.
- **Confirmación de herramientas peligrosas in-app**: si el agente se
  detiene en `confirmation_required` a mitad de un turno de chat,
  `ChatScreen` ahora pinta una tarjeta Aprobar/Rechazar (nombre de la
  herramienta + argumentos) en vez de solo un aviso de texto — al tocar un
  botón, `ChatViewModel.confirmar` llama `POST
  /v1/conversations/{id}/confirm` y sigue el mismo stream SSE que un turno
  normal. Ya no hace falta ir al panel web para desatascar una conversación.
- **Voz push-to-talk**: botón de micrófono dentro de Chat. Al abrirlo,
  mantén presionado → graba con `MediaRecorder` (permiso
  `RECORD_AUDIO` en tiempo de ejecución) → `POST /v1/voice/transcribe`
  (multipart) → el texto transcrito entra al turno normal del agente (`POST
  /v1/conversations/{id}/messages`, en una conversación dedicada) → la
  respuesta completa se sintetiza (`POST /v1/voice/speak`) y
  se reproduce con `MediaPlayer`. Si el tenant no conectó su propio
  proveedor de voz (`GET /v1/credentials`, `voice_stt`/`voice_tts` ambos
  `null`), la pantalla avisa que va a usar la transcripción/voz de prueba
  del `StubSTT`/`StubTTS` offline (texto fijo, silencio) en vez de dejar
  que parezca un reconocimiento real fallando.
- **Conectar LLM desde el teléfono**: Ajustes — "pegar y validar"
  (`docs/roadmap.md`) contra `PUT /v1/credentials/llm`: selector de
  `kind` (`anthropic`/`openai_compat`/`vertex` siempre;
  `claude_cli`/`codex_cli`/`ollama` solo si `GET /v1/setup/status` dice
  `local_mode: true`), campos según el `kind` elegido
  (`vm/PerfilViewModel.kt::LlmKind`, testeado en
  `androidApp/src/test/.../LlmKindTest.kt`), error EXACTO del proveedor en
  pantalla si la validación falla, estado "Conectado ahora: …" si ya hay
  algo conectado.
- **Registro in-app + emparejamiento de dispositivo real**: `POST
  /v1/auth/register` desde el propio onboarding (ver arriba) y `POST`/
  `DELETE /v1/devices` en login/logout (contrato de fase v4, "mejor
  esfuerzo" — nunca bloquea el flujo si el endpoint no está listo del lado
  del servidor todavía).
- **IDE de solo lectura**: Modo avanzado en Ajustes — árbol del sandbox del companion de
  escritorio emparejado (`GET /v1/ide/tree`, aplanado con sangría por
  profundidad) y contenido de un archivo (`GET /v1/ide/file`, con aviso si
  es binario). `EmptyState` si no hay companion conectado (`GET
  /v1/ide/status`).

### Pantallas v5 (fase v5): Misiones, Automatizaciones, Recordatorios

Tres pantallas conectadas a la API real. Las tres se llegan desde
**Actividad** (`InicioScreen`, tarjetas "Trabajo delegado"/"Rutinas"/
"Recordatorios") — no son pestañas nuevas de la barra
inferior: `RootNav.kt` las cubre como una pantalla propia (con su propio
botón "atrás" que vuelve a Actividad) encima de los 3 destinos existentes, sin
tocarlas. Mismo criterio de "push" de un solo nivel que usaría
`NavigationStack` desde `InicioView.swift` en iOS (que todavía no las
construye).

- **Misiones** (`edecan_api.routers.missions`, `ARCHITECTURE.md` §11,
  `docs/roadmap.md`) — `ui/MisionesScreen.kt` +
  `vm/MisionesViewModel.kt`: lista con badge por `status`
  (`planning|running|waiting_confirmation|done|error|cancelled`), alta de
  misión (`POST /v1/missions {objetivo}`), detalle con sus pasos y resultado
  (`GET /v1/missions/{id}`), tarjeta Aprobar/Rechazar cuando la misión queda
  `waiting_confirmation` (`POST /v1/missions/{id}/confirm {approved}` —
  `approve = false` cancela la misión entera, mismo endpoint para ambos
  botones). La planificación/ejecución real corre asíncrona en el worker
  (`edecan_worker.handlers.run_mission`) — el router NO expone SSE (a
  diferencia del chat), así que esta pantalla refresca por *polling* (cada
  2s, mismo intervalo que `apps/web/src/app/(app)/app/misiones/page.tsx`)
  mientras la misión seleccionada o alguna de la lista siga
  `planning`/`running`. `403` si el plan del tenant no trae el flag
  `agents.missions` (p. ej. `hosted_basic`) — el error del servidor se
  muestra tal cual, sin pantalla especial.
- **Automatizaciones** (`edecan_api.routers.automations`, `docs/roadmap.md`) — `ui/AutomatizacionesScreen.kt` +
  `vm/AutomatizacionesViewModel.kt`: lista con `Switch` **optimista**
  (cambia en pantalla al toque, dispara `PATCH /v1/automations/{id}
  {enabled}` de verdad, y si falla revierte la fila a mano a su valor
  anterior — nunca deja el Switch "mintiendo" sobre lo que el servidor
  guardó), alta simple (`POST /v1/automations`: siempre un disparador por
  agenda con los mismos 3 presets diario/semanal/mensual que
  `AutomationForm.tsx` en el panel web, más una instrucción para el agente),
  y detalle de corridas (`GET /v1/automations/{id}/runs`). El móvil NO
  ofrece crear automatizaciones `kind = "webhook"` — esas se siguen creando
  solo desde el panel web (que muestra el secreto generado una sola vez);
  el móvil sí las **lista** y puede activarlas/desactivarlas igual que
  cualquier otra.
- **Recordatorios** (`edecan_api.routers.reminders`, `ARCHITECTURE.md`
  §10.3/§10.12/§10.11) — `ui/RecordatoriosScreen.kt` +
  `vm/RecordatoriosViewModel.kt`: pestañas "Pendientes"/"Completados"
  (`status == "pending"` vs. cualquier otro), alta con texto + fecha/hora
  eligiendo con `DatePicker`/`TimePicker` de Material3
  (`ui/components/FechaHoraPickers.kt`, `POST /v1/reminders`), "Completar" a
  mano sobre un pendiente (`PUT /v1/reminders/{id} {status: "sent"}` — el
  vocabulario real de `ReminderPatch.status` no tiene un valor `"done"`
  propio; `"sent"` es el mismo que usa `send_reminder_scan`/
  `repo.mark_reminder_sent` cuando el job automático lo entrega solo). Sin
  flag de plan — disponible en los 4 planes, igual que en el panel web.

**Límites de esta ola:**

- **Sin notificaciones push nativas todavía**: las tres pantallas se
  refrescan por *polling*/al volver a entrar, no hay `FCM` empujando un
  aviso al teléfono cuando una misión cambia de estado o un recordatorio
  vence (sigue en el roadmap, ver el próximo bullet más abajo).
  `EdecanApi.createReminder` manda SIEMPRE `channel = "web"`, **nunca**
  `"mobile"` — no existe todavía una ruta de entrega dedicada del lado del
  servidor (`send_reminder.py` solo sabe degradar `voice`/`phone` a mensaje
  de chat, con una advertencia), así que inventar un valor de canal nuevo
  acá no cambiaría nada real hoy y sí confundiría a un futuro lector de
  `reminders`.
- **Solo *polling*, sin SSE**: a diferencia del chat, ninguno de estos tres
  routers expone un stream — es una decisión del propio backend (§7.9/§7.6
  de `docs/roadmap.md`), no una limitación de este work package.
- Misiones no expone editar el `plan` ni reintentar un paso puntual — solo
  crear, ver el avance y aprobar/rechazar el paso pendiente, igual que hace
  el panel web hoy.

### Pantalla Remoto

Control remoto del Mac/PC del tenant desde el teléfono — aplica el guardrail
de [`control-remoto.md`](./control-remoto.md): consentimiento explícito en
el teléfono MÁS una segunda aprobación
LOCAL en el companion antes de que salga un solo *frame* o se mueva un solo
píxel. Se llega **solo** desde el acceso directo "Remoto" de Actividad
(`InicioScreen.kt`), mismo criterio de pantalla secundaria de `RootNav.kt`
(`PantallaSecundaria.REMOTO`) que Misiones/Automatizaciones/Recordatorios de
arriba — espejo Android de fase v6 en iOS.

- `ui/RemotoScreen.kt` + `vm/RemotoViewModel.kt` (`shared/RemoteModels.kt`/
  `RemoteCoords.kt`) contra `/v1/remote` (`ARCHITECTURE.md` §13.c/§14,
  [`control-remoto.md`](./control-remoto.md) §7bis/§10).
- Dos flags de plan independientes, cada uno gateando su propia acción (no
  el flag general del grupo): `companion.remote_view` para poder abrir la
  pantalla, `companion.remote_input` ADEMÁS para que la app ofrezca la
  opción "Controlar" (sin él, solo se puede pedir "Ver").
- El indicador "Sesión remota activa" y el botón "Terminar" están SIEMPRE
  visibles mientras haya una sesión pendiente o activa — nunca ocultables.
- Transporte: ***polling* HTTP** (`GET /v1/remote/sessions/{id}/frame`, PNG
  en base64) — decisión deliberada del prototipo P1
  ([`control-remoto.md`](./control-remoto.md) §1.1), no un *stub* a medio
  terminar. El transporte WebRTC de baja latencia sigue siendo el objetivo a
  futuro, ver "Roadmap" justo abajo.

### Roadmap (siguiente iteración, no en esta app todavía)

- **Editar/correr/buscar en el IDE**: el resto de `/v1/ide` (`PUT
  /v1/ide/file`, `POST /v1/ide/edit`, `POST /v1/ide/run`, `POST
  /v1/ide/search`) — hoy `IdeScreen` es solo lectura (árbol + ver un
  archivo).
- **Telefonía real / voz avanzada**: el micrófono de Chat hoy es push-to-talk
  simple (una grabación, una respuesta) contra el mismo asistente — la
  telefonía Twilio bring-your-own (`docs/voz-telefonia.md`) y un modo
  conversación continua con barge-in (`docs/roadmap.md`) siguen
  pendientes.
- **Negocios de escritura**: crear facturas y cambiar su estado
  (`POST /v1/negocios/facturas`, `POST /v1/negocios/facturas/{id}/estado`)
  — hoy `NegociosScreen` es solo lectura.
- **Perfil, credenciales de voz/imágenes/búsqueda**: el formulario de
  Perfil solo cubre `PUT /v1/credentials/llm`; conectar STT/TTS/imágenes/
  búsqueda desde el teléfono (`PUT /v1/credentials/voice/{stt,tts}`,
  `/images`, `/search`) sigue siendo cosa del panel web. Editar persona
  (tono, formalidad, instrucciones) y tema de la app también.
- **Push**: Firebase Cloud Messaging (FCM) — la tabla `devices` ya tiene el
  lugar donde registrar el token por dispositivo una vez exista
  `POST /v1/devices` del lado del servidor (fase v4). Primer consumidor
  natural una vez exista: avisar cuando una misión queda
  `waiting_confirmation`/termina (`Pantallas v5` arriba) o cuando vence un
  recordatorio, en vez de depender del *polling*/de volver a entrar a la
  pantalla.
- **Visor de control remoto — transporte WebRTC**: la pantalla "Remoto" ya
  es real hoy con *polling* HTTP (ver ["Pantalla Remoto"](#pantalla-remoto)
  arriba) — lo que sigue pendiente es reemplazar ese *polling* por WebRTC
  (`libwebrtc` para Android) de baja latencia, diseño completo en
  [`control-remoto.md`](./control-remoto.md) §5/§9.
- **Emparejamiento real por dispositivo**: hoy `POST /v1/devices` ya se
  llama en login/registro (ver "Emparejamiento al primer arranque" arriba),
  pero sigue siendo un registro silencioso, no un flujo de **QR +
  verificación de *fingerprint*** con aprobación humana explícita —
  ver [`control-remoto.md`](./control-remoto.md) §4/§9.
