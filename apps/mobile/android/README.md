# Edecán — app Android nativa

App real y compilable: Kotlin 2.4.0 + Compose Multiplatform (Android hoy;
iOS declarado para el futuro), sin React Native/Flutter (mandato
permanente — ver `docs/roadmap.md` y `ARCHITECTURE.md` §11). Nunca se
distribuye por Play Store: instalación local vía USB/orígenes desconocidos,
cada cliente compila y firma con su propio keystore. Guía completa para el
cliente final en [`../../../docs/movil-android.md`](../../../docs/movil-android.md)
— este README es solo el arranque rápido para quien va a tocar el código.

## Estructura

```
apps/mobile/android/
├── settings.gradle.kts       # incluye :shared y :androidApp
├── build.gradle.kts          # raíz: solo declara plugins (apply false)
├── gradle/libs.versions.toml # catálogo de versiones — única fuente de verdad
├── gradlew, gradlew.bat      # wrapper de Gradle (no editar a mano)
├── shared/                    # módulo KMP: red/dominio, SIN UI
│   ├── build.gradle.kts
│   └── src/
│       ├── commonMain/kotlin/cc/edecan/shared/
│       │   ├── Models.kt        # modelos serializables de /v1/* (docs/api.md)
│       │   ├── ApiClient.kt     # EdecanApi: auth/negocios/credentials/setup/voz/ide/devices/remote
│       │   ├── RemoteModels.kt  # modelos + vocabulario de /v1/remote/* (fase v6)
│       │   ├── RemoteCoords.kt  # mapeo de coordenadas toque->frame, puro (fase v6)
│       │   ├── SseClient.kt     # SseClient (I/O) + SseFrameParser (framing puro, testeado)
│       │   └── TokenStore.kt    # contrato de persistencia de tokens + device id
│       ├── androidMain/kotlin/cc/edecan/shared/
│       │   └── TokenStore.android.kt  # DataStore + EncryptedSharedPreferences
│       └── commonTest/kotlin/cc/edecan/shared/
│           ├── ModelsTest.kt          # serialización de Models.kt
│           ├── RemoteModelsTest.kt    # serialización + intervalo de polling puro (fase v6)
│           ├── RemoteCoordsTest.kt    # mapeo de coordenadas con letterbox (fase v6)
│           └── SseFrameParserTest.kt  # framing SSE línea por línea
└── androidApp/                # el APK real
    ├── build.gradle.kts
    ├── proguard-rules.pro
    └── src/
        ├── main/
        │   ├── AndroidManifest.xml
        │   ├── res/                # iconos, tema Material 3 (claro/oscuro), network security config
        │   └── kotlin/cc/edecan/app/
        │       ├── MainActivity.kt, App.kt
        │       ├── nav/RootNav.kt  # Scaffold + NavigationBar de 6 destinos + Remoto como pantalla secundaria
        │       ├── ui/             # Onboarding, Inicio, Chat, IDE, Negocios, Voz, Perfil, Remoto
        │       │   └── components/ # EmptyState, DonutChart (Canvas puro)
        │       └── vm/             # Session/Chat/Negocios/Voz/Perfil/Ide/Remoto ViewModel (StateFlow)
        └── test/kotlin/cc/edecan/app/vm/
            ├── LlmKindTest.kt      # vocabulario del formulario "Conectar LLM"
            └── RemotoTeclasTest.kt # vocabulario de la barra de teclado remota (fase v6)
```

`shared` también declara `iosArm64()`/`iosSimulatorArm64()` — targets
**declarados para consumo futuro únicamente**; hoy nada los compila ni los
usa (el cliente iOS real es `apps/mobile/ios/EdecanKit`, un Swift Package
sin relación de compilación con este módulo — mismo contrato de API
`/v1/*`, dos implementaciones nativas independientes). Ninguna tarea de
este work package los toca.

## Requisitos

- JDK 17 o superior (probado con Temurin 25).
- Android SDK: `compileSdk`/`targetSdk` 37, `minSdk` 26 — cmdline-tools
  alcanza, no hace falta Android Studio completo (aunque es lo más cómodo
  para editar/depurar). Con Homebrew: `brew install --cask
  android-commandlinetools`, luego acepta licencias e instala los paquetes:
  ```bash
  export ANDROID_HOME=/opt/homebrew/share/android-commandlinetools
  yes | "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" --licenses
  "$ANDROID_HOME/cmdline-tools/latest/bin/sdkmanager" \
    "platform-tools" "platforms;android-37.1" "build-tools;37.0.0"
  ```
- `local.properties` con `sdk.dir=<ruta al SDK>` — Android Studio lo genera
  solo al abrir el proyecto; si compilas por CLI, créalo a mano (ver
  `local.properties` de ejemplo, gitignored).

## Build

```bash
cd apps/mobile/android
./gradlew :androidApp:assembleDebug
adb install androidApp/build/outputs/apk/debug/androidApp-debug.apk
```

Verificado real contra AGP 9.2.1 + Kotlin 2.4.0 + Compose Multiplatform
1.11.1 (`BUILD SUCCESSFUL`, `:androidApp:assembleDebug` y también
`:androidApp:assembleRelease` sin firmar) — ver "Matriz de versiones" abajo
para cómo volver a verificarla si algo cambia más adelante.

## Tests

```bash
./gradlew :shared:check :androidApp:testDebugUnitTest
```

Corre, todo en verde (verificado real, `BUILD SUCCESSFUL`):

- **`:shared:testAndroidHostTest`** (parte de `:shared:check`): 62 tests —
  `ModelsTest` (serialización contra ejemplos reales de `docs/api.md`/
  `ARCHITECTURE.md` §10.5, incluyendo `ChatEvent`/`Invoice`/`NegociosKpis`/
  `CredentialsOut`), `SseFrameParserTest` (framing SSE línea por línea,
  sin abrir ningún canal/conexión real), desde fase v5
  `MissionsModelsTest`/`AutomationsModelsTest`/`RemindersModelsTest`
  (serialización contra ejemplos reales de
  `apps/api/edecan_api/routers/{missions,automations,reminders}.py`, mismo
  criterio que `ModelsTest`) y, desde fase v6, `RemoteModelsTest`
  (serialización de `RemoteModels.kt` contra ejemplos reales de
  `apps/api/edecan_api/routers/remote.py`/`SqlRepo._REMOTE_SESSION_COLUMNS`,
  más `remoteFramePollDelayMillis` — función pura del intervalo de *polling*)
  y `RemoteCoordsTest` (mapeo de coordenadas toque→frame con letterbox,
  puerto de `apps/web/src/components/remoto/coords.ts`, sin Compose/Android).
- **`:androidApp:testDebugUnitTest`**: `LlmKindTest` (vocabulario/campos del
  formulario "Conectar LLM" de `PerfilScreen`) y, desde fase v6,
  `RemotoTeclasTest` (vocabulario de la barra de teclado remota de
  `RemotoScreen`, fijado exacto contra `REMOTE_SPECIAL_KEYS`).
- **`:shared:check`** también intenta compilar (no ejecutar) el mismo
  `commonTest` para los targets iOS declarados (`iosArm64`/
  `iosSimulatorArm64`) — la tarea `iosSimulatorArm64Test` (que SÍ correría
  esos tests en un Simulador) está deshabilitada a propósito
  (`shared/build.gradle.kts`, `enabled = false`) porque esta máquina no
  tiene un runtime de iOS Simulator provisto: fuera del alcance de este
  módulo Android. La *compilación* del test para iOS sigue corriendo
  siempre, así que un error real ahí se sigue detectando.

`kotlin("test")`/`kotlin("test-junit")` (`shared`/`androidApp`
respectivamente — ver el comentario en cada `build.gradle.kts` sobre por
qué son artefactos distintos) son la única librería de test usada: cero
dependencias de terceros nuevas (nada de JUnit5/MockK/Robolectric/
Turbine).

## Verificado en esta iteración (fase v5)

Mismo entorno de la "Matriz de versiones" de abajo (JDK 25 Temurin, AGP
9.2.1, Kotlin 2.4.0, Compose Multiplatform 1.11.1, Gradle 9.5.1):

```bash
cd apps/mobile/android
./gradlew --no-build-cache clean :androidApp:assembleDebug :shared:check :androidApp:testDebugUnitTest
```

→ **`BUILD SUCCESSFUL` en 18s** (68 tareas: 67 ejecutadas desde cero + 1
up-to-date — build limpio, `clean` real y `--no-build-cache`, no una
compilación incremental), cubriendo `RootNav.kt` y las 3 pantallas nuevas
(`ui/MisionesScreen.kt`, `ui/AutomatizacionesScreen.kt`,
`ui/RecordatoriosScreen.kt`) con sus `ViewModel`s y los 3 modelos nuevos de
`shared/` (`MissionsModels.kt`, `AutomationsModels.kt`,
`RemindersModels.kt`). Único warning: las deprecaciones ya existentes de
`MasterKey`/`EncryptedSharedPreferences` en `TokenStore.android.kt` (ver
"Matriz de versiones" abajo) — nada nuevo de v5, sin ningún ajuste de
código hecho durante esta verificación.

- **`:shared:testAndroidHostTest`**: **37/37 tests pasan**, 0 fallos (9
  nuevos: `MissionsModelsTest` 3, `AutomationsModelsTest` 4,
  `RemindersModelsTest` 2 — los 28 previos siguen en verde).
- **`:androidApp:testDebugUnitTest`**: `LlmKindTest`, **8/8 tests pasan**
  (sin cambios — no toca código de v5).
- Sin dependencias externas nuevas ni cambios en
  `gradle/libs.versions.toml` para esta ola: las 3 pantallas y sus
  `ViewModel`s usan solo lo ya declarado (Ktor, kotlinx-serialization/
  coroutines, Material3 `DatePicker`/`TimePicker` para
  `ui/components/FechaHoraPickers.kt`).

## Verificado en esta iteración (fase v6)

Mismo entorno de la "Matriz de versiones" de abajo (JDK 25 Temurin, AGP
9.2.1, Kotlin 2.4.0, Compose Multiplatform 1.11.1, Gradle 9.5.1):

```bash
cd apps/mobile/android
./gradlew --no-build-cache clean :androidApp:assembleDebug :shared:check :androidApp:testDebugUnitTest
```

→ **`BUILD SUCCESSFUL` en 8s** (68 tareas: 67 ejecutadas desde cero + 1
up-to-date — build limpio, `clean` real y `--no-build-cache`, no una
compilación incremental), cubriendo la pantalla nueva `ui/RemotoScreen.kt` +
`vm/RemotoViewModel.kt` (espejo Android de fase v6 en iOS: visor de
`/v1/remote/*` con *polling* real, zoom/pan, tap→`input_pointer`, barra de
teclado→`input_key`), los 2 módulos nuevos de `shared/`
(`RemoteModels.kt`, `RemoteCoords.kt`) más las 7 rutas nuevas agregadas a
`EdecanApi` (`ApiClient.kt`), la advertencia específica de `usar_computadora`
sumada a la tarjeta de confirmación de `ui/ChatScreen.kt`, y el acceso
directo "Remoto" nuevo en `ui/InicioScreen.kt` + `nav/RootNav.kt`. Único
warning nuevo: `rememberTransformableState` (la sobrecarga de 3 parámetros
sin centroide, única disponible en este `foundation-api.jar` real — ver el
comentario junto a su uso en `RemotoScreen.kt`) — se acepta a propósito,
mismo criterio que las deprecaciones ya existentes de
`MasterKey`/`EncryptedSharedPreferences`.

- **`:shared:testAndroidHostTest`**: **62/62 tests pasan**, 0 fallos (25
  nuevos de este WP: `RemoteModelsTest` 11, `RemoteCoordsTest` 14 — los 37
  previos siguen en verde).
- **`:androidApp:testDebugUnitTest`**: **12/12 tests pasan**, 0 fallos (4
  nuevos: `RemotoTeclasTest` — `LlmKindTest` sigue en verde sin cambios).
- Sin dependencias externas nuevas ni cambios en `gradle/libs.versions.toml`:
  el visor usa solo lo ya declarado (`compose.foundation` para
  `Image`/`detectTapGestures`/`transformable`, `android.graphics.BitmapFactory`/
  `android.util.Base64` del SDK de Android para decodificar el frame PNG,
  cero librerías de carga de imágenes de terceros).
- `AndroidManifest.xml` sin cambios: `/v1/remote/*` reutiliza el mismo
  `INTERNET` ya declarado, ninguna acción nueva pide un permiso de Android
  distinto (la captura de pantalla y el permiso de Accesibilidad son del
  lado del companion en macOS, no de esta app móvil).

## Firma release

Este esqueleto **nunca** firma un release con una key compartida (regla
dura del repo). `androidApp/build.gradle.kts` no declara ningún
`signingConfig` para `release` a propósito — cada cliente agrega el suyo
propio antes de distribuir un APK real. Ver
[`../../../docs/movil-android.md`](../../../docs/movil-android.md), sección
"Firma release con tu keystore".

## Matriz de versiones

`gradle/libs.versions.toml` es la única fuente de verdad. Todas las
versiones de abajo se verificaron **reales** (no de memoria) contra Google
Maven/Maven Central el 2026-07-08, mientras se escribía y compilaba este
esqueleto por primera vez:

| Artefacto | Versión | Notas |
|---|---|---|
| Kotlin | 2.4.0 | pinned por decisión de producto (`docs/roadmap.md`) |
| AGP (Android Gradle Plugin) | 9.2.1 | ver "Cambios de AGP 9" abajo |
| Gradle | 9.5.1 | `gradle/wrapper/gradle-wrapper.properties` |
| Compose Multiplatform (plugin `org.jetbrains.compose`) | 1.11.1 | los artefactos `compose.material3`/`compose.ui`/etc. NO se pinnean sueltos — los resuelve este plugin |
| Ktor | 3.5.1 | cliente HTTP/SSE de `shared` |
| kotlinx-serialization | 1.11.0 | |
| kotlinx-coroutines | 1.11.0 | |
| androidx.datastore:datastore-preferences | 1.2.1 | |
| androidx.security:security-crypto | 1.1.0 | ver nota de deprecación en `TokenStore.android.kt` |
| androidx.lifecycle (runtime-ktx, viewmodel-compose) | 2.11.0 | exige `compileSdk` 37 (ver abajo) |
| androidx.activity:activity-compose | 1.13.0 | |
| androidx.core:core-ktx | 1.19.0 | exige `compileSdk` 37 (ver abajo) |

Cómo volver a verificar una versión contra el repositorio real (reemplaza
el `groupId`/`artifactId`):

```bash
curl -s "https://dl.google.com/dl/android/maven2/androidx/core/core-ktx/maven-metadata.xml" | grep -o '<release>[^<]*</release>'
curl -s "https://repo1.maven.org/maven2/org/jetbrains/kotlin/kotlin-gradle-plugin/maven-metadata.xml" | tail -5
```

### Cambios de AGP 9 que este esqueleto ya absorbió (para cuando toque subir de versión otra vez)

Dos cambios reales de AGP 9 rompieron el patrón "clásico" mientras se
escribía este esqueleto — quedan documentados aquí y en el propio
`build.gradle.kts` de cada módulo para que la próxima actualización de
versión no los vuelva a redescubrir desde cero:

1. **`shared` (KMP + Android):** `com.android.library` ya NO es compatible
   con `org.jetbrains.kotlin.multiplatform` en el mismo módulo. Se usa
   `com.android.kotlin.multiplatform.library` en su lugar, con la config
   Android dentro de `kotlin { android { ... } }` (no un bloque `android {
   }` a nivel de módulo). Ver
   https://developer.android.com/kotlin/multiplatform/plugin.
2. **`androidApp` (Android puro):** `org.jetbrains.kotlin.android` ya NO se
   aplica — AGP 9 compila Kotlin de forma "built-in", y aplicar ese plugin
   revienta con un error explícito. El compilador de Compose
   (`org.jetbrains.kotlin.plugin.compose`) sigue haciendo falta igual (es
   otra cosa). Ver
   https://developer.android.com/build/releases/agp-9-0-0-release-notes#android-gradle-plugin-built-in-kotlin.

Además: `androidx.core:core-ktx` 1.19.0 y `androidx.lifecycle:*` 2.11.0 ya
exigen compilar contra API 37 (`compileSdk = 37`, no 36) —
`checkDebugAarMetadata` lo revienta con un mensaje claro si el `compileSdk`
se queda corto; ver comentario en cada `build.gradle.kts`.

## Qué es real hoy vs. qué falta

Ver la sección "Qué es real hoy" / "Roadmap" completa en
[`../../../docs/movil-android.md`](../../../docs/movil-android.md) — ese
documento vive fuera de `apps/mobile/android/`, así que este WP (alcance
pinned a esta carpeta) no lo actualiza; queda como deuda de documentación
para una pasada dedicada, mismo criterio que otros WPs de este repo dejan
"enlace roto temporal" como deuda aceptada. Resumen actualizado a esta
iteración: auth (login/registro, refresh, tokens cifrados con
`EncryptedSharedPreferences`, emparejamiento de dispositivo vía `POST`/
`DELETE /v1/devices`), chat con streaming SSE real + confirmación in-app de
herramientas peligrosas (incluida la advertencia específica de
`usar_computadora`, fase v6), Negocios (KPIs + dona + facturas, solo
lectura), Voz push-to-talk (transcribir → turno del agente → hablar la
respuesta), Perfil (conectar LLM "pegar y validar"), IDE de solo lectura
(árbol + ver archivo) y, desde fase v6, **Remoto** (visor de `/v1/remote/*`
sobre el mismo prototipo *polling* HTTP del panel web — consentimiento +
aprobación local en el companion, indicador permanente + Terminar siempre
visible, `kind="control"` con tap→clic/doble clic/clic derecho + barra de
teclado, *polling* automático una vez aprobada la sesión, cero *polling*
huérfano al salir de la pantalla) SÍ están construidos; editar/correr/buscar
en el IDE, telefonía Twilio/voz avanzada, Negocios de escritura, FCM push,
el transporte WebRTC de extremo a extremo (`docs/control-remoto.md` §5 —
el *polling* HTTP de hoy sigue siendo el mismo prototipo P1 que ya usaba el
panel web, no cambia con este WP) y el emparejamiento con QR + verificación
de *fingerprint* (`docs/control-remoto.md` §4 — Remoto sigue usando el
`pair-code` simple de 8 caracteres del companion, igual que el resto de la
app) quedan documentados como siguiente iteración.
