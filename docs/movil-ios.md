# App iOS nativa

Guía completa para compilar, firmar e instalar la app iOS de Edecán —
Swift 6 + SwiftUI puro, diseño Liquid Glass, **nunca React Native**. El código
vive en [`../apps/mobile/ios/`](../apps/mobile/ios/); este documento es la
referencia para el CLIENTE que va a compilar su propia app, y el
`README.md` de ese directorio es el arranque rápido para quien va a tocar
el código.

**Léelo antes de empezar:** esta app **nunca** se distribuye por App Store
ni TestFlight — es una decisión de producto permanente, no una limitación
temporal (ver la sección ["Nunca App Store, nunca TestFlight"](#nunca-app-store-nunca-testflight)
más abajo). Cada cliente compila, firma e instala su propia copia.

## Resumen — el modelo en 4 ideas

1. **Instalación local únicamente.** Se compila con Xcode/fastlane y se
   instala por USB — igual que un desarrollador prueba su propia app
   mientras la construye, pero pensado para que un cliente real la use día
   a día.
2. **Tu propia cuenta Apple Developer Program.** Cada cliente firma con SU
   cuenta de pago ($99/año), nunca con la del dueño de Edecán — ver
   ["Requisitos"](#requisitos) para el porqué.
3. **El servidor lo traes tú.** No hay un servidor Edecán "de fábrica": la
   app pide la URL de tu propia instalación (self-host, o tu app de
   escritorio Tauri) en el primer arranque. Mismo principio bring-your-own
   que el resto del producto (`docs/roadmap.md`).
4. **Iniciar sesión ES el emparejamiento**, en este v1. Ver
   ["Emparejamiento en el primer arranque"](#emparejamiento-en-el-primer-arranque).

## Requisitos

- **Un Mac** con Xcode 26 o más nuevo (este esqueleto se generó y compiló
  con Xcode 26.6 / SDK iOS 26.5 — ver ["Verificado en esta iteración"](#verificado-en-esta-iteración-fase-v3)).
- **[xcodegen](https://github.com/yonaskolb/XcodeGen)**: `brew install xcodegen`.
  `Edecan.xcodeproj` no existe en el repo — se genera desde `project.yml`,
  la única fuente de verdad del proyecto (nunca se edita el `.xcodeproj` a
  mano ni se versiona).
- **[fastlane](https://fastlane.tools)** (opcional para desarrollo, obligatorio
  para instalar en un iPhone real): `brew install fastlane`.
- **Tu propia cuenta Apple Developer Program de pago** ($99/año) — ver el
  porqué justo abajo.
- **Un servidor Edecán** accesible desde tu iPhone (misma red Wi-Fi, VPN, o
  un dominio público si tu self-host/escritorio lo expone así).

### Por qué cada cliente necesita su propia cuenta Apple Developer

Decisión de negocio explícita, documentada en `docs/roadmap.md`
("Decisión de negocio: instalación con cuenta Apple Developer del propio
cliente") y confirmada en `docs/roadmap.md`. El resumen técnico:

- Firmar con un Apple ID gratis (sin cuenta de pago) funciona por USB, pero
  el build **expira a los 7 días** — inviable para uso real.
- Firma **ad-hoc** con una cuenta Developer Program de pago da un build
  válido ~1 año, sin pasar por revisión de Apple ni por la App Store — pero
  exige **registrar el UDID de cada dispositivo** en la cuenta antes de
  firmar, con un **tope de ~100 dispositivos por cuenta por año**.
- La alternativa (Apple Developer *Enterprise* Program, sin tope de
  dispositivos) tiene términos de licencia que **prohíben explícitamente**
  distribuir a clientes externos — solo sirve para apps internas de una
  organización a sus propios empleados. Usarlo así arriesga que Apple
  revoque el certificado de golpe, tumbando la app para todos los clientes
  a la vez. **Edecán no usa este camino.**

Si fuera la cuenta del dueño de Edecán la que registrara el UDID de cada
cliente, el tope de 100 dispositivos/año se agotaría rapidísimo repartido
entre todos los clientes del producto. Exigiendo que **cada cliente use su
propia cuenta**, cada uno tiene su propio tope de 100 — de sobra para sus
propios dispositivos — y el problema desaparece. El perfil de cliente de
este producto (alguien con IDE completo, control de computador,
git/Docker/Kubernetes/AWS/GCP/Azure en la misma app) ya es alguien que
probablemente crea software: pedirle una cuenta Developer no es una
barrera real para ese público (`docs/roadmap.md`).

## Arquitectura del proyecto

```
apps/mobile/ios/
├── project.yml            # fuente de verdad del proyecto Xcode (xcodegen)
├── EdecanKit/              # Swift Package: red/datos, SIN UI (SPM local)
│   ├── Sources/EdecanKit/
│   │   ├── Models.swift            # Codable: TokenPair, Me, Conversation, ChatEvent, JSONValue
│   │   ├── NegociosModels.swift    # NegocioKPIs, CanalKPI, ActividadKPI, Factura, FacturaItem
│   │   ├── CredentialsModels.swift # CredentialsOut, LLMCredentialsIn, SetupStatus, DetectLocalProviders
│   │   ├── IDEModels.swift         # IDEStatusOut, IDEEntry/IDETree, IDEFileOut
│   │   ├── DeviceModels.swift      # DeviceOut (POST /v1/devices, fase v4 en paralelo)
│   │   ├── VoiceModels.swift       # TranscribeOut, HablarResultado
│   │   ├── MissionsModels.swift    # MissionOut, MissionStepOut, MissionDetailOut (v5, fase v5)
│   │   ├── AutomationsModels.swift # AutomationOut/Trigger/Accion, AutomationRunOut (v5, fase v5)
│   │   ├── RemindersModels.swift   # ReminderOut (v5, fase v5)
│   │   ├── RemoteModels.swift      # RemoteSession/RemoteFrame/RemoteInput + mapeo de coordenadas (fase v6)
│   │   ├── APIClient.swift         # actor: auth/register/me/conversaciones/negocios/credenciales/
│   │   │                           # setup/ide/voz/dispositivos/misiones/automatizaciones/
│   │   │                           # recordatorios/remoto, auto-refresh en 401
│   │   ├── MultipartFormData.swift # helpers `Data.append*` para multipart a mano (voz)
│   │   ├── SSEClient.swift         # URLSession.bytes(for:) → AsyncThrowingStream<ChatEvent>
│   │   ├── Keychain.swift          # kSecClassGenericPassword, sin App Group
│   │   └── PairingStore.swift      # URL del servidor + "emparejado" + deviceId (fase v4)
│   └── Tests/EdecanKitTests/       # 89 tests, offline, corren con `swift test`
├── EdecanApp/              # target de la app (SwiftUI)
│   ├── EdecanApp.swift, RootTabView.swift, Theme.swift, SessionStore.swift
│   ├── Onboarding/OnboardingView.swift    # login + registro (POST /v1/auth/register)
│   ├── Screens/{Inicio,Chat,IDE,Negocios,Voz,Perfil}View.swift
│   ├── Screens/{Misiones,Automatizaciones,Recordatorios}View.swift  # v5, alcanzables desde Inicio
│   ├── Screens/RemotoView.swift             # fase v6, alcanzable desde Inicio (*polling*, no WebRTC)
│   ├── Componentes/
│   │   ├── ChatViewModel.swift         # turno SSE + confirmación de herramientas peligrosas
│   │   ├── TarjetaConfirmacion.swift   # tarjeta Aprobar/Rechazar, compartida Chat ↔ Voz ↔ Misiones
│   │   ├── NegociosViewModel.swift     # GET /v1/negocios/kpis + /facturas en paralelo
│   │   ├── VozRecorder.swift           # AVAudioEngine — graba a WAV, @unchecked Sendable
│   │   ├── VozViewModel.swift          # push-to-talk: reutiliza ChatViewModel completo
│   │   ├── CredencialesViewModel.swift # GET /v1/credentials + /v1/setup/* + PUT LLM
│   │   ├── ConectarLLMSheet.swift      # formulario "pegar y validar" de Conectar LLM
│   │   ├── IDEViewModel.swift          # GET /v1/ide/status|tree|file (solo lectura)
│   │   ├── MisionesViewModel.swift         # lista+detalle con *polling* de /v1/missions (v5)
│   │   ├── AutomatizacionesViewModel.swift # toggle optimista + runs de /v1/automations (v5)
│   │   ├── RecordatoriosViewModel.swift    # CRUD simple de /v1/reminders (v5)
│   │   ├── RemotoViewModel.swift           # visor + input de /v1/remote, *polling* (fase v6)
│   │   ├── BurbujaMensaje.swift, EmptyStateView.swift
│   └── Resources/Assets.xcassets/
├── EdecanWidgets/           # extensión de widgets (placeholder mínimo)
└── fastlane/                # lanes generate/bump/adhoc
```

### `EdecanKit` — la capa de red, sin UI

Todo lo que habla con `/v1/*` (ver [`api.md`](./api.md) y
`ARCHITECTURE.md` §10.12/§12) vive en este Swift Package local, para que
`EdecanApp` y `EdecanWidgets` compartan un solo cliente en vez de
reimplementarlo cada uno:

- **`APIClient`** es un `actor` (estado mutable — los tokens en memoria —
  tocado desde varias tareas a la vez). Cubre auth (`login`/`registrar`/
  `refrescar`), `me`/`listarConversaciones`/`crearConversacion`, Negocios
  (`negociosKPIs`/`listarFacturas`), credenciales bring-your-own
  (`credenciales`/`conectarLLM`/`desconectarLLM`), el wizard de arranque
  (`setupStatus`/`setupDetect`), IDE de solo lectura
  (`ideStatus`/`ideTree`/`ideFile`), voz (`transcribir`/`hablar`, con
  `multipart/form-data` armado a mano) y dispositivos
  (`registrarDispositivo`/`revocarDispositivo`, degradando con gracia a
  `nil`/silencio ante un `404` — ver ``DeviceOut``). Reintento automático de
  **una vez** por endpoint si el access token expiró (401 → `refrescar()` →
  reintenta). Errores tipados en español (`APIError`), listos para mostrar
  tal cual en la UI.
- **`SSEClient`** abre el stream de
  `POST /v1/conversations/{id}/messages` **y** `POST .../confirm` con
  `URLSession.bytes(for:)` y lo parsea línea por línea (`event:`/`data:`/
  línea en blanco = fin de bloque), emitiendo un `ChatEvent` por cada bloque
  completo vía `AsyncThrowingStream`. Solo entiende el framing SSE — la
  autenticación y la URL las arma quien llama, con
  `APIClient.tokenDeAccesoValido()` / `APIClient.urlCompleta(_:)`.
- **`Keychain`** es un envoltorio mínimo sobre `Security.framework`:
  `kSecClassGenericPassword` con `kSecAttrAccessibleAfterFirstUnlock`, sin
  `kSecAttrAccessGroup` (no comparte datos con `EdecanWidgets` todavía —
  ver ["Qué es real hoy vs. qué falta"](#qué-es-real-hoy-vs-qué-falta)).
- **`PairingStore`** (`@MainActor @Observable`) guarda la URL del servidor,
  expone `isPaired` y ahora también `deviceId` (el `id` de `devices` que
  devolvió `POST /v1/devices`, si ese endpoint ya existe del lado del
  servidor — fase v4, contrato en paralelo). **Sin ningún valor por
  defecto de servidor** — si nunca se configuró nada, `serverURL` es `nil`
  y el onboarding lo pide.

### `EdecanApp` — la app SwiftUI

`EdecanApp.swift` decide entre `OnboardingView` (sin emparejar) y
`RootTabView` (emparejado). `RootTabView` monta las 6 pestañas del mockup
del panel web — Inicio, Chat, IDE, Negocios, **Voz** (antes "Llamadas" en
el esqueleto original), Perfil — con `.tint(EdecanTheme.morado)`, y expone
`TabRouter` (`@Observable`, vía `.environment(...)`) para que una pantalla
pueda cambiar la pestaña activa de otra (p. ej. el aviso de "voz de prueba"
en Voz llevando directo a Perfil).

**Liquid Glass:** un `TabView` estándar en iOS 26 ya adopta automáticamente
la barra flotante translúcida del sistema, sin modifiers extra. El material
`glassEffect(_:in:)` real (con `if #available(iOS 26, *)` y fallback a
`.ultraThinMaterial`) se aplica a mano en `Theme.swift`
(`TarjetaVidrio`/`.tarjetaVidrio(esquina:)`) — es lo que usan las tarjetas
de `OnboardingView`, las burbujas de `ChatView`/`VozView`, la tarjeta de
confirmación y los estados vacíos.

**Chat es funcional de verdad**, no una maqueta: `ChatViewModel` crea la
conversación la primera vez, arma la petición SSE a mano
(`APIClient.urlCompleta` + `APIClient.tokenDeAccesoValido()` +
`SSEClient.stream(_:)`) y va apendeando cada `text_delta` al mensaje del
asistente en pantalla, con un indicador mientras el agente usa una
herramienta (`tool_start` sin su `tool_end` todavía). Si el agente pide
confirmar una herramienta peligrosa (`confirmation_required`), expone
`confirmacionPendiente` y `ChatView` muestra ``TarjetaConfirmacion`` con
botones Aprobar/Rechazar que llaman `POST .../confirm` — nunca se manda al
usuario al panel web para eso. **``VozViewModel`` reutiliza el mismo
`ChatViewModel` tal cual** (no reimplementa el turno del agente) para que
*push-to-talk* corra exactamente la misma lógica de conversación/SSE/
confirmación que la pestaña Chat. IDE es de solo lectura (árbol +
visor monoespaciado); Negocios trae KPIs + dona + facturas reales.

## Pantallas v5: Misiones, Automatizaciones, Recordatorios

Tres pantallas nuevas, **alcanzables desde los accesos directos de
`InicioView`** (`RootTabView` sigue con las mismas 6 pestañas — este WP no
las toca), conectadas de verdad a la API real, no maquetas:

- **Misiones** (`Screens/MisionesView.swift`, `Componentes/MisionesViewModel.swift`)
  — el Orchestrator multi-agente. Consume `GET/POST /v1/missions`,
  `GET /v1/missions/{id}`, `POST /v1/missions/{id}/confirm`,
  `POST /v1/missions/{id}/cancel` (`ARCHITECTURE.md` §11 `docs/roadmap.md`). Lista con badge de estado (`planning|running|
  waiting_confirmation|done|error|cancelled`) y alta con un campo
  `objetivo`; detalle con la línea de tiempo de `agent_steps` (agente, status,
  resultado) y, si un paso queda `waiting_confirmation`, la MISMA
  `TarjetaConfirmacion` que usan Chat/Voz con sus botones Aprobar/Rechazar,
  cableados al endpoint real. **Sin SSE**: `missions.py` es deliberadamente
  delgado (solo inserta/lee filas y encola `run_mission` — la
  planificación/ejecución corre asíncrona en el worker), así que la lista y
  el detalle hacen *polling* cada 2s (`Task` + `Task.sleep`, el mismo
  intervalo que ya usa `apps/web/.../misiones/page.tsx`) mientras algo siga
  activo, y se detienen solos al salir de la pantalla.
- **Automatizaciones** (`Screens/AutomatizacionesView.swift`,
  `Componentes/AutomatizacionesViewModel.swift`) — reglas de agenda o
  webhook (`ARCHITECTURE.md` §11 `docs/roadmap.md`). Consume
  `GET /v1/automations`, `PATCH /v1/automations/{id}` (toggle),
  `POST /v1/automations` (alta) y `GET /v1/automations/{id}/runs`. Lista con
  `Toggle` de activar/desactivar **optimista** (refleja el cambio al instante
  y revierte solo si el servidor lo rechaza, p. ej. por el tope de
  automatizaciones activas del plan); alta con presets de `rrule` (diario/
  semanal/mensual, mismo criterio que
  `apps/web/src/components/automatizaciones/AutomationForm.tsx`) + una
  opción personalizada — **esta app móvil solo crea disparadores de tipo
  agenda (`kind="schedule"`)**, dar de alta un webhook sigue siendo terreno
  del panel web (aunque sí puede listar/ver el detalle de automatizaciones
  webhook ya existentes); detalle con sus últimas `automation_runs`.
- **Recordatorios** (`Screens/RecordatoriosView.swift`,
  `Componentes/RecordatoriosViewModel.swift`) — `ARCHITECTURE.md` §10.3/
  §10.12. Consume `GET/POST /v1/reminders` y `PUT /v1/reminders/{id}`. Lista
  separada en Pendientes/Completados, alta con texto + `DatePicker`, y
  completar con *swipe* sobre una fila pendiente.

### Límites conocidos de estas 3 pantallas

- **Sin push todavía, todo por *polling* o carga manual.** Ninguna de las
  tres recibe notificaciones en tiempo real — Misiones hace *polling* activo
  mientras algo esté en curso (ver arriba); Automatizaciones/Recordatorios
  se cargan al entrar a la pantalla y con *pull-to-refresh*, sin *polling*
  continuo (no hay nada "en curso" que valga la pena repetir automáticamente
  ahí). Notificaciones push (APNs) siguen pendientes del emparejamiento por
  dispositivo completo, igual que el resto de la app (ver la tabla de abajo).
- **`APIClient.createReminder` nunca manda `canal: "mobile"`.** El valor
  existe como concepto desde v5, pero la entrega push a este teléfono no
  está conectada (`send_reminder.py` solo sabe entregar por chat hoy) — un
  recordatorio creado desde la app usa `channel: "web"` por defecto y se
  entrega exactamente igual que uno creado desde el panel web, hasta que esa
  ola aterrice.
- **`APIClient.completeReminder` reutiliza el status `"sent"`.** El backend
  no tiene un status "completado" propio (solo `pending|sent|cancelled`,
  `ARCHITECTURE.md` §10.3) — completar a mano desde el *swipe* pone el mismo
  status que ya usa `send_reminder_scan` cuando el recordatorio vence solo;
  ambos casos se muestran igual (tachado, en "Completados").
- **Crear una automatización desde el teléfono siempre es tipo agenda.**
  Ver arriba — sin formulario de webhook en esta app.
- **Sin `getAutomation` propio**: el detalle de una automatización reutiliza
  la fila que ya está en memoria de la lista (`AutomatizacionesViewModel.automatizaciones`)
  en vez de pedirla de nuevo — si esa fila cambiara del lado del servidor
  mientras el detalle está abierto (p. ej. otra sesión la desactivó), esta
  pantalla no se entera hasta volver a la lista.

## Compilar por primera vez (desarrollo, sin firmar)

Para trabajar en el código o simplemente confirmar que compila en tu
máquina, sin necesidad todavía de cuenta Developer ni de un iPhone físico:

```bash
brew install xcodegen
cd apps/mobile/ios

# 1. Capa de red/datos, aislada — 38 tests, corre en segundos
cd EdecanKit && swift build && swift test
cd ..

# 2. Proyecto completo, contra el simulador (no necesita firma)
xcodegen generate
xcodebuild -project Edecan.xcodeproj -scheme EdecanApp \
  -destination 'generic/platform=iOS Simulator' build
```

O simplemente `open Edecan.xcodeproj` tras el `xcodegen generate` y correr
con ▶︎ en Xcode contra un simulador.

## Antes de compilar para tu iPhone: bundle id y equipo

`project.yml` trae dos valores que **cada cliente debe cambiar antes de
firmar**, no antes de compilar para el simulador:

- **`PRODUCT_BUNDLE_IDENTIFIER: cc.edecan.app`** es un placeholder de
  desarrollo. Un bundle id no se puede repetir entre cuentas Apple
  Developer distintas, así que cámbialo por uno propio (p. ej.
  `com.tuempresa.edecan`) en `project.yml` (busca `cc.edecan.app`, aparece
  en el target `EdecanApp` y en `EdecanWidgetsExtension` como
  `cc.edecan.app.widgets`) antes de firmar con tu cuenta.
- **`DEVELOPMENT_TEAM: ""`** queda deliberadamente vacío — este repo
  **nunca** trae un Team ID real (`ARCHITECTURE.md` §0, cero secretos).
  Dos formas de completarlo con el tuyo (reemplaza el valor vacío por
  `TU_TEAM_ID_AQUI`, tu Team ID de 10 caracteres de
  `developer.apple.com/account`):
  1. En Xcode: target `EdecanApp` → *Signing & Capabilities* → elige tu
     equipo del desplegable (tras iniciar sesión con tu Apple ID en
     Xcode → *Settings* → *Accounts*). `CODE_SIGN_STYLE: Automatic` deja
     que Xcode resuelva perfil y certificado solo a partir de ahí — esta
     es la vía recomendada.
  2. O escribiéndolo directamente en `project.yml` y corriendo
     `xcodegen generate` de nuevo.

## Compilar e instalar en tu iPhone (build ad-hoc)

Con bundle id y equipo ya configurados:

### 1. Registra el UDID de cada dispositivo

En tu cuenta de [developer.apple.com](https://developer.apple.com/account/resources/devices) →
*Devices* → *+*. Para obtener el UDID de un iPhone: conéctalo por USB,
ábrelo en Xcode → *Window* → *Devices and Simulators*, y copia el
*Identifier* que aparece ahí (o en el iPhone: *Ajustes* → *General* →
*Información* → desplázate hasta *UDID identifica el dispositivo*... en
iOS moderno se obtiene más fácil desde la ventana de Xcode). Tope: ~100
dispositivos por cuenta por año — es tuyo, no compartido con otros
clientes de Edecán.

### 2. El perfil de aprovisionamiento ad-hoc

Con `CODE_SIGN_STYLE: Automatic` (ya configurado en `project.yml`), Xcode
genera y renueva el perfil "Ad Hoc" solo, en cuanto tu dispositivo está
registrado y conectado. Si prefieres el camino manual: *Certificates,
Identifiers & Profiles* → *Profiles* → *+* → *Ad Hoc* → tu bundle id → los
dispositivos que quieras incluir.

### 3. `fastlane adhoc`

```bash
cd apps/mobile/ios
brew install fastlane   # si no lo tienes
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 fastlane bump    # opcional: sube el build number
LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8 fastlane adhoc
```

`adhoc` corre `xcodegen generate` y compila un Release firmado `ad-hoc`
(`gym`), y deja el resultado en `apps/mobile/ios/build/Edecan-adhoc.ipa`.

**El prefijo `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8` es obligatorio en
algunas máquinas**: sin un locale UTF-8 explícito, `fastlane`/`gym` pueden
fallar con un error de codificación de Ruby al leer el proyecto — mismo
pipeline que el dueño de Edecán ya usa en sus otros proyectos iOS (bump de
versión → `xcodegen generate` → fastlane con `LC_ALL=en_US.UTF-8`).

### 4. Instala el `.ipa` por USB

Dos caminos, sin pasar por ninguna tienda:

- **Xcode**: *Window* → *Devices and Simulators* → selecciona tu iPhone
  conectado por USB → arrastra `Edecan-adhoc.ipa` a la lista de apps.
- **[Apple Configurator](https://apps.apple.com/app/apple-configurator/id1037126344)**
  (gratis, Mac App Store): arrastra el `.ipa` sobre el dispositivo
  conectado.

La primera vez que la abras, iOS puede pedir confiar en el desarrollador:
*Ajustes* → *General* → *VPN y gestión de dispositivos* → tu perfil →
*Confiar*.

## Emparejamiento en el primer arranque

Al abrir la app por primera vez ves `OnboardingView`, 2 pasos (más un
desvío opcional):

1. **URL del servidor** — pega la URL de tu propia instalación de Edecán
   (self-host, o tu app de escritorio). Sin default: cada cliente trae el
   suyo.
2. **Iniciar sesión** — correo, contraseña, y un código de 6 dígitos solo
   si tu cuenta tiene 2FA activado (`POST /v1/auth/login`, ver
   [`api.md`](./api.md)). Si todavía no tienes cuenta, "¿No tienes cuenta?
   Crear una" lleva a un formulario corto (nombre de empresa/equipo, correo,
   contraseña) que llama `POST /v1/auth/register` — crea el tenant, el
   usuario `owner` y una persona por defecto, y deja la sesión iniciada
   igual que un login.

**En este v1, el par de tokens JWT que deja el login/registro ES el
emparejamiento** de este teléfono con tu tenant — no hay todavía un
protocolo de emparejamiento propio para móvil completo (como el código
corto de 6 dígitos que ya usa el companion de escritorio, `POST
/v1/companion/pair-code`). Mientras el refresh token siga en el Keychain de
este iPhone, el dispositivo cuenta como emparejado; "Cerrar sesión" en la
pestaña Perfil lo borra y vuelve a mostrar el onboarding.

**Registro real por dispositivo, best-effort (fase v4):** justo después de
un login/registro exitoso, `SessionStore.emparejarDispositivo(pairingStore:)`
llama `POST /v1/devices {nombre, plataforma: "ios", kind: "mobile",
fingerprint}` (`nombre` = `UIDevice.current.name`, `fingerprint` =
`UIDevice.current.identifierForVendor`) y, si responde con éxito, guarda el
`id` devuelto en `PairingStore.deviceId` para poder revocarlo luego
(`POST /v1/devices/{id}/revoke`, disparado al cerrar sesión) sin tener que
cambiar la contraseña de la cuenta. **Este es un contrato en paralelo
(fase v4) que todavía no tiene router en `apps/api/edecan_api/routers/`**
al escribir esto — `APIClient.registrarDispositivo`/`.revocarDispositivo`
tratan un `404` como "todavía no existe" y degradan en silencio, sin
bloquear ni mostrar error: el emparejamiento v1 de arriba (sesión =
emparejamiento) sigue siendo el mecanismo real hasta que ese WP aterrice.
Ver `EdecanKit/Sources/EdecanKit/PairingStore.swift`/`DeviceModels.swift` y
`SessionStore.swift` para el punto de extensión exacto.

## Nunca App Store, nunca TestFlight

Decisión de producto **permanente**, no una limitación de esta iteración.
Ninguna lane de `fastlane/Fastfile`
sube nada a App Store Connect. TestFlight también pasa por App Store
Connect (aunque con revisión más ligera que la tienda completa) — se
descarta por el mismo motivo. La única vía de distribución es la que
describe este documento: build ad-hoc firmado, instalado por USB con la
cuenta Developer del propio cliente.

## Qué es real hoy vs. qué falta

| Área | Estado |
|---|---|
| Autenticación (login, **registro**, refresh automático, Keychain) | **Real** |
| Chat con streaming SSE (`text_delta`, indicador de herramienta) | **Real** |
| **Confirmar/rechazar una herramienta peligrosa desde el chat** (`POST .../confirm`, tarjeta inline Aprobar/Rechazar) | **Real** — `ChatViewModel.confirmacionPendiente` + `TarjetaConfirmacion`, compartida con Voz |
| **Voz nativa** (*push-to-talk* con `AVAudioEngine` → `POST /v1/voice/transcribe` → turno de chat completo → `POST /v1/voice/speak` → `AVAudioPlayer`) | **Real** — pestaña "Voz" (antes "Llamadas"); avisa con un enlace a Perfil si la respuesta usó el `StubTTS` (sin credencial de voz conectada) |
| **Negocios** (KPIs del mes con tarjetas + dona de canales con Swift Charts + lista de facturas) | **Real** — `GET /v1/negocios/kpis` + `/facturas` |
| **Perfil: estado de conexión de LLM/voz/imágenes/búsqueda** (`GET /v1/credentials`) + selector **"Conectar LLM"** ("pegar y validar", `PUT /v1/credentials/llm`) | **Real** — incluye atajos de un clic para CLI/Ollama detectados (`GET /v1/setup/detect`) cuando el servidor corre en modo local |
| **IDE embebido de solo lectura** (árbol navegable con `OutlineGroup` + visor monoespaciado) | **Real** — `GET /v1/ide/status\|tree\|file`; escribir/editar/correr comandos queda fuera de esta app (sigue siendo terreno del panel web) |
| **Registro por dispositivo** (`POST /v1/devices`, revocar al cerrar sesión) | Best-effort, degrada con gracia si el servidor todavía no tiene ese router (fase v4 en paralelo) — ver ["Emparejamiento en el primer arranque"](#emparejamiento-en-el-primer-arranque) |
| **Misiones** (crear por objetivo, lista con *polling*, detalle con pasos y confirmación) | **Real** — ver ["Pantallas v5"](#pantallas-v5-misiones-automatizaciones-recordatorios) |
| **Automatizaciones** (lista con toggle optimista, alta de agenda, detalle con corridas) | **Real** — ver ["Pantallas v5"](#pantallas-v5-misiones-automatizaciones-recordatorios) |
| **Recordatorios** (lista pendientes/completados, alta, completar con *swipe*) | **Real** — ver ["Pantallas v5"](#pantallas-v5-misiones-automatizaciones-recordatorios) |
| `EdecanKit` completo con 89 tests offline | **Real** |
| Liquid Glass (`glassEffect` + fallback) en tarjetas/burbujas/onboarding/confirmación | **Real** |
| Inicio (saludo + `GET /v1/me`), Perfil (datos + cerrar sesión) | **Real** |
| Proyecto Xcode compilable (xcodegen) + lanes de fastlane (`generate`/`bump`/`adhoc`) | **Real** |
| Emparejamiento real por dispositivo COMPLETO (tabla `devices` con listado/gestión en la app, QR) | Pendiente — hoy solo se registra/revoca el propio dispositivo al iniciar/cerrar sesión (ver arriba), sin pantalla para ver/gestionar otros dispositivos emparejados |
| Notificaciones push (APNs) | Pendiente — requiere el emparejamiento por dispositivo completo de arriba |
| Historial de llamadas de telefonía premium (Twilio, por tenant) | Pendiente — la pestaña Voz solo cubre voz web (*push-to-talk*), no telefonía |
| IDE con escritura/edición/terminal (`PUT /v1/ide/file`, `POST /v1/ide/edit\|run\|search`) | Pendiente — la app móvil solo lee/navega, el panel web sigue siendo donde se edita |
| **Visor de control remoto** (`RemotoView`/`RemotoViewModel`, *polling* HTTP) | **Real** — ver ["Verificado en esta iteración (fase v6)"](#verificado-en-esta-iteración-fase-v6); transporte WebRTC de baja latencia sigue pendiente (§5 de [`control-remoto.md`](./control-remoto.md)) |
| Widget con datos reales (próxima conversación, recordatorio) | Pendiente — hoy es un placeholder estático, sin App Group con `EdecanApp` |
| Editar persona (tono, formalidad, instrucciones), tema de la app | Pendiente — placeholder en Perfil |

## Verificado en esta iteración (fase v3)

Con Xcode 26.6 (SDK iPhoneSimulator 26.5) y Swift 6.3.3 instalados:

- `xcodegen generate` → genera `Edecan.xcodeproj` sin errores.
- `cd EdecanKit && swift build` (limpio, sin caché) → compila sin
  advertencias.
- `swift test` → **20/20 tests pasan**.
- `xcodebuild -project Edecan.xcodeproj -scheme EdecanApp -destination
  'generic/platform=iOS Simulator' build` → **`BUILD SUCCEEDED`**, cero
  advertencias del compilador Swift (con `SWIFT_STRICT_CONCURRENCY:
  complete`, Swift 6 en modo estricto), incluyendo la compilación y el
  empaquetado de `EdecanWidgets.appex` dentro de `Edecán.app/PlugIns/`.

## Verificado en esta iteración (fase v4)

Mismo entorno (Xcode 26.6, SDK iPhoneSimulator 26.5, Swift 6.3.3, xcodegen
2.45.4), re-verificado tras CADA feature de este paquete de trabajo
(Negocios → confirmación → Voz → IDE → Perfil/Onboarding), no solo al
final:

- `cd EdecanKit && swift build && swift test` → compila limpio, **38/38
  tests pasan** (18 nuevos: `NegociosModelsTests`, `CredentialsModelsTests`,
  `IDEModelsTests`, `DeviceModelsTests`, `VoiceModelsTests`).
- `xcodegen generate && xcodebuild -project Edecan.xcodeproj -scheme
  EdecanApp -configuration Debug -destination 'generic/platform=iOS
  Simulator' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO clean build`
  → **`BUILD SUCCEEDED`**, cero advertencias del compilador Swift (mismo
  `SWIFT_STRICT_CONCURRENCY: complete` estricto de siempre — incluyendo
  `VozRecorder`, que captura `self` dentro del tap block de tiempo real de
  `AVAudioEngine` con una conformidad `@unchecked Sendable` deliberada y
  documentada, ver su docstring).
- `IPHONEOS_DEPLOYMENT_TARGET`/SDK: sin fricción — el proyecto ya fijaba
  `26.0` contra el SDK `26.5` instalado, ningún ajuste hizo falta en
  `project.yml` para este paquete.
- Sin dependencias externas nuevas: todo lo agregado (voz, gráfico de dona,
  multipart a mano) usa únicamente frameworks del SDK de Apple (`AVFoundation`,
  `Charts`, `UIKit` para `UIDevice`) — cero paquetes SPM de terceros.

## Verificado en esta iteración (fase v5)

Mismo entorno (Xcode 26.6, SDK iPhoneSimulator 26.5, Swift 6.3.3, xcodegen
2.45.4):

- `cd EdecanKit && swift build && swift test` → compila limpio, **58/58
  tests pasan** (20 nuevos: `MissionsModelsTests`, `AutomationsModelsTests`,
  `RemindersModelsTests`).
- `xcodegen generate && xcodebuild -project Edecan.xcodeproj -scheme
  EdecanApp -configuration Debug -destination 'generic/platform=iOS
  Simulator' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO clean build`
  → **`BUILD SUCCEEDED`**, cero advertencias del compilador Swift (mismo
  `SWIFT_STRICT_CONCURRENCY: complete` estricto de siempre — incluyendo el
  *polling* con `Task`/`Task.sleep` de `MisionesViewModel`, que captura
  `self` dentro del closure del `Task` almacenado sin necesitar ninguna
  conformidad `@unchecked Sendable`: un `Task {}` creado dentro de un método
  de una clase `@MainActor` hereda esa misma isolación para su closure).
  Único ajuste real durante el desarrollo: un `Binding(get:set:)` construido
  a partir de un closure GUARDADO como propiedad (`let onToggle: (Bool) ->
  Void`) de `AutomatizacionesView` sí generaba una advertencia real de
  Sendable (`Binding` exige `@Sendable` en sus dos closures) — se resolvió
  armando el `Binding` con un closure LITERAL directo en su sitio de uso
  (`AutomatizacionesView.lista`, dentro del `ForEach`) en vez de reenviar un
  valor de closure ya construido; ver el comentario en el código.
- Sin dependencias externas nuevas ni cambios en `project.yml`/`Package.swift`:
  los targets ya usan globs por carpeta, así que los 3 archivos nuevos de
  `EdecanKit/Sources/EdecanKit/`, los 3 de `EdecanKit/Tests/EdecanKitTests/`,
  las 3 pantallas de `EdecanApp/Screens/` y los 3 ViewModels de
  `EdecanApp/Componentes/` entraron solos al compilar.

## Verificado en esta iteración (fase v6)

Mismo entorno (Xcode 26.6, SDK iPhoneSimulator 26.5, Swift 6.3.3, xcodegen
2.45.4):

- `cd EdecanKit && swift build && swift test` → compila limpio, **85/85
  tests pasan** en 16 suites (suites nuevas de esta iteración:
  `RemoteCoordinateMapperTests`, `RemoteFrameModelsTests`,
  `RemoteInputModelsTests`, `RemoteSessionModelsTests`, todas sobre
  `EdecanKit/Sources/EdecanKit/RemoteModels.swift`).
- `xcodegen generate && xcodebuild -project Edecan.xcodeproj -scheme
  EdecanApp -configuration Debug -destination 'generic/platform=iOS
  Simulator' CODE_SIGNING_ALLOWED=NO CODE_SIGNING_REQUIRED=NO clean build`
  → **`BUILD SUCCEEDED`**, cero advertencias del compilador Swift (mismo
  `SWIFT_STRICT_CONCURRENCY: complete` estricto de siempre).
- Pantalla nueva "Remoto" (`EdecanApp/Screens/RemotoView.swift` +
  `Componentes/RemotoViewModel.swift`), alcanzable **solo** desde el acceso
  directo "Remoto" de `InicioView` — mismo criterio de pantalla secundaria
  que Misiones/Automatizaciones/Recordatorios (`RootTabView` sin cambios).
  Visor con *polling* HTTP, no WebRTC — decisión deliberada del prototipo P1
  (`docs/control-remoto.md` §1.1), no un *stub* a medio terminar — más input
  de teclado/mouse cuando el plan trae `companion.remote_input`.
- Sin dependencias externas nuevas: todo lo agregado usa `URLSession` (ya en
  uso por el resto de `EdecanKit`) y frameworks del SDK de Apple — cero
  paquetes SPM de terceros, mismo criterio de siempre.

## Roadmap corto

Por orden aproximado de lo que más desbloquea al resto:

1. **Emparejamiento real por dispositivo, completo** — fase v4 (contrato
   en paralelo) debe aterrizar `POST /v1/devices`/`GET /v1/devices`/`POST
   /v1/devices/{id}/revoke` del lado del servidor; esta app ya llama
   `registrarDispositivo`/`revocarDispositivo` y degrada con gracia
   mientras tanto (ver ["Emparejamiento en el primer
   arranque"](#emparejamiento-en-el-primer-arranque)). Una vez aterrice,
   sumar una pantalla en Perfil para ver/revocar OTROS dispositivos
   emparejados (hoy solo se gestiona el propio, al cerrar sesión).
2. **Push APNs** — depende directamente del punto anterior, para poder
   dirigir una notificación a un iPhone concreto.
3. **Voz de telefonía (Twilio, premium)** — la pestaña Voz de hoy es *push-to-talk*
   web (`voice.web`); el historial de llamadas entrantes/salientes de
   telefonía por tenant (`voice.telephony`, ver
   [`voz-telefonia.md`](./voz-telefonia.md)) sigue sin vivir en la app.
4. **Visor de control remoto — transporte WebRTC** — la pantalla "Remoto" ya
   es real hoy con *polling* HTTP (fase v6, ver
   ["Verificado en esta iteración (fase v6)"](#verificado-en-esta-iteración-fase-v6)
   arriba), nunca controlando sin sesión aprobada en el Mac. Lo que sigue
   pendiente es reemplazar ese *polling* por WebRTC de baja latencia —
   diseño completo (transporte, niveles de permiso, modelo de amenazas) ya
   escrito en [`control-remoto.md`](./control-remoto.md) §5.
5. **IDE con escritura** — sumar `PUT /v1/ide/file`/`POST
   /v1/ide/edit\|run\|search` sobre el árbol de solo lectura que ya existe
   (ver [`ide.md`](./ide.md)).
6. **Editar persona y tema** — tono/formalidad/instrucciones y claro/oscuro
   manual (hoy sigue el `ColorScheme` del sistema) desde Perfil, sin pasar
   por el panel web.
7. **Widget con datos reales** — próxima conversación o recordatorio más
   cercano, lo que exige compartir un App Group entre `EdecanApp` y
   `EdecanWidgets` para que el widget pueda leer el Keychain compartido.

Ver también [`movil-android.md`](./movil-android.md) para el equivalente en
Android (Kotlin + Compose Multiplatform, mismo criterio de nunca subir a
tienda).

## Solución de problemas

- **`fastlane`/`gym` falla con un error de codificación (`invalid byte
  sequence`, `ArgumentError`, etc.)** — te faltó el prefijo
  `LC_ALL=en_US.UTF-8 LANG=en_US.UTF-8` antes del comando.
- **Xcode no encuentra un perfil de aprovisionamiento válido** — el UDID
  del dispositivo que conectaste no está registrado todavía en tu cuenta
  (paso 1 de ["Compilar e instalar en tu iPhone"](#compilar-e-instalar-en-tu-iphone-build-ad-hoc)),
  o iniciaste sesión con un Apple ID sin cuenta Developer Program de pago
  activa en Xcode → *Settings* → *Accounts*.
- **"Bundle identifier is not available" al firmar** — el bundle id
  `cc.edecan.app` (o el que hayas puesto) ya lo usa otra app en tu cuenta,
  o lo intentó firmar otro cliente con la misma cuenta. Cámbialo por uno
  único (`com.tuempresa.edecan`) en `project.yml` y corre
  `xcodegen generate` de nuevo.
- **La app no puede conectar con el servidor** — confirma que el iPhone y
  el servidor están en la misma red (o que la URL es accesible desde
  Internet si tu servidor está expuesto así), y que la URL en el onboarding
  incluye `http://` o `https://`.
- **"No se pudo conectar" o sesión que expira todo el tiempo** — revisa que
  el reloj del iPhone esté en hora automática; un JWT con `exp` mal
  comparado por *skew* de reloj se ve igual que una sesión expirada de
  verdad.
