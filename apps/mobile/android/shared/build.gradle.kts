// `shared` — capa de red/dominio en Kotlin Multiplatform (ROADMAP_V2.md
// §6.1, ARCHITECTURE.md §12): todo lo que habla con `/v1/*` (docs/api.md)
// vive aquí y no en `androidApp` — modelos serializables, cliente HTTP
// (Ktor), cliente SSE y el contrato de persistencia de tokens. `androidApp`
// consume este módulo como dependencia de proyecto; el equivalente Swift ya
// existe por separado en `apps/mobile/ios/EdecanKit` (Swift Package, sin
// relación de compilación con este módulo — mismo contrato de API, dos
// implementaciones nativas, ver docs/movil-android.md).
//
// Plugin Android: `com.android.kotlin.multiplatform.library`, NO el
// clásico `com.android.library` — desde AGP 9.0, `com.android.library`
// deja de ser compatible con `org.jetbrains.kotlin.multiplatform` en el
// mismo módulo (falla al aplicar el plugin con un mensaje que apunta
// exactamente a este reemplazo; verificado corriendo
// `./gradlew :shared:compileAndroidMain` contra AGP 9.2.1 real — ese es el
// nombre REAL de la tarea de compilación Android de este plugin nuevo
// (`compileDebugKotlinAndroid` era el nombre del `com.android.library`
// clásico y ya no existe; confirmado con `./gradlew :shared:tasks` real,
// WP-V4-04). Cambia también la DSL: la configuración Android ya no es un
// bloque `android { }` a nivel de módulo + `androidTarget()` suelto dentro
// de `kotlin { }` — ahora es `kotlin { android { ... } }`, ver
// https://developer.android.com/kotlin/multiplatform/plugin.
plugins {
    alias(libs.plugins.kotlinMultiplatform)
    alias(libs.plugins.androidKotlinMultiplatformLibrary)
    alias(libs.plugins.kotlinSerialization)
}

kotlin {
    jvmToolchain(17)

    android {
        namespace = "cc.edecan.shared"
        // 37, no 36: `androidx.core`/`androidx.lifecycle` en las versiones
        // pinned de gradle/libs.versions.toml ya exigen compilar contra
        // API 37 (`checkDebugAarMetadata` lo revienta si no — verificado
        // corriendo `./gradlew :androidApp:assembleDebug` real). Mismo
        // valor en `androidApp/build.gradle.kts`, deben ir siempre juntos.
        compileSdk = 37
        minSdk = 26

        // Habilita tests JVM ("host tests") para el target `android` de este
        // módulo KMP — sin esto, `commonTest`/`shared/src/commonTest` NUNCA
        // corre para el target Android (solo quedaría alcanzable desde los
        // targets iOS declarados, que este work package no toca). Verificado
        // real: sin esta línea, `./gradlew :shared:tasks` imprime el warning
        // "the 'commonTest' source directory exists, but android host tests
        // are not enabled" y no existe ninguna tarea de test para `android`.
        // Plugin nuevo de AGP 9 (`com.android.kotlin.multiplatform.library`,
        // ver comentario al tope de este archivo) — `withHostTest {}` es el
        // reemplazo de `unitTests.isIncludeAndroidResources`/etc. del
        // `com.android.library` clásico.
        withHostTest {}

        compilerOptions {
            jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17)
        }
    }

    // iOS: targets DECLARADOS para consumo futuro únicamente — hoy nada los
    // compila ni los usa (EdecanKit en Swift, arriba, es el cliente iOS
    // real). Quedan aquí para que el día que se decida reemplazar EdecanKit
    // por este módulo compartido, la estructura ya exista. Xcode está
    // disponible en el Mac que compila este repo, así que declararlos no
    // rompe nada, pero ninguna tarea de este work package (`:androidApp:
    // assembleDebug`) los toca.
    iosArm64()
    iosSimulatorArm64()

    sourceSets {
        commonMain.dependencies {
            // `api`, no `implementation`, para los dos tipos que SÍ se
            // escapan a la superficie pública de `shared` (`EdecanApi`,
            // `SseClient`, `edecanJson`) y que `androidApp` referencia
            // directamente (p. ej. `ChatViewModel` pasa un `HttpClient` y
            // llama `edecanJson.encodeToString(...)`) — con `implementation`
            // el módulo consumidor no ve esas clases en su propio
            // classpath ("Cannot access class... check module classpath",
            // error real visto corriendo `./gradlew :androidApp:assembleDebug`
            // mientras se escribía este esqueleto) aunque `shared` compile
            // bien por sí solo. `ktor-client-content-negotiation` y
            // `ktor-serialization-kotlinx-json` SÍ se quedan en
            // `implementation`: los usa `EdecanApi` puertas adentro (arma
            // su propio `HttpClient`), pero ningún tipo de esos dos
            // artefactos aparece en una firma pública.
            api(libs.ktor.client.core)
            api(libs.kotlinx.serialization.json)
            implementation(libs.ktor.client.content.negotiation)
            implementation(libs.ktor.serialization.kotlinx.json)
            implementation(libs.kotlinx.coroutines.core)
        }
        androidMain.dependencies {
            // Motor HTTP real de Ktor en Android (OkHttp) — el resto de
            // targets (iOS, cuando se activen) usarían el motor Darwin en
            // su propio source set, no este.
            implementation(libs.ktor.client.okhttp)
            implementation(libs.kotlinx.coroutines.android)
            implementation(libs.androidx.datastore.preferences)
            implementation(libs.androidx.security.crypto)
        }
        // `kotlin("test")`: el KMP Gradle plugin lo resuelve al artefacto
        // correcto por target (`kotlin-test` puro en `commonTest`,
        // `kotlin-test-junit` en el target `android`, vía la resolución
        // especial que el plugin aplica a esta coordenada) — sin pinnear
        // ninguna versión propia en `libs.versions.toml` (usa la del
        // propio Kotlin del proyecto, 2.4.0). Cero dependencias nuevas de
        // terceros: solo la librería de test del propio Kotlin.
        commonTest.dependencies {
            implementation(kotlin("test"))
        }
    }
}

// `commonTest` (agregado por WP-V4-04, ver `shared/src/commonTest/`) hace
// que el plugin de Kotlin Multiplatform conecte automáticamente una tarea
// `iosSimulatorArm64Test` a `:shared:check` — COMPILAR ese test para iOS
// funciona bien (mismo `commonTest`, sin cambios), pero EJECUTARLO exige un
// runtime de iOS Simulator ya descargado en Xcode (`xcrun simctl`, Xcode >
// Settings > Platforms), que esta máquina no tiene provisto todavía
// (verificado real: `./gradlew :shared:check` falla con "Xcode does not
// support simulator tests for ios_simulator_arm64. Check that requested SDK
// is installed" — un problema de runtime del SIMULADOR, no del código).
// Instalar ese runtime es una acción de infraestructura de Xcode fuera del
// alcance de este work package (Android, `apps/mobile/android/` únicamente,
// ver `docs/movil-android.md`) — se deshabilita SOLO la ejecución
// (`enabled = false`, la tarea queda "SKIPPED", nunca falla `check`); la
// COMPILACIÓN del test para iOS (`compileTestKotlinIosSimulatorArm64`) sigue
// corriendo siempre, así que un error de compilación real en `commonTest`
// que rompiera el target iOS se sigue detectando igual.
tasks.matching { it.name == "iosSimulatorArm64Test" }.configureEach { enabled = false }
