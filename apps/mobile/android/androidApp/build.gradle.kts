// `androidApp` — el APK real de este esqueleto (login + chat SSE básico +
// 3 destinos assistant-first; capacidades especializadas como secundarias). Módulo Android puro
// (`com.android.application`), NO un target de `shared` — lo consume como
// `project(":shared")`.
//
// SIN `org.jetbrains.kotlin.android`, a propósito: desde AGP 9.0 ese
// plugin quedó incompatible con `com.android.application` (Kotlin
// "built-in" — AGP compila Kotlin directamente, sin plugin aparte;
// aplicarlo revienta con un error que apunta exactamente a este cambio,
// verificado corriendo `./gradlew :androidApp:assembleDebug` contra AGP
// 9.2.1 real mientras se escribía este esqueleto — ver
// https://developer.android.com/build/releases/agp-9-0-0-release-notes#android-gradle-plugin-built-in-kotlin).
// El compilador de Compose (`org.jetbrains.kotlin.plugin.compose`) SÍ
// sigue haciendo falta — es un plugin de compilación de Compose, no de
// Kotlin en general, y no lo reemplaza el soporte "built-in".
//
// Aplica igual el plugin Compose Multiplatform (`org.jetbrains.compose`)
// aunque hoy solo compila para Android: es el framework de UI que fija
// `DIRECCION_ACTUAL.md`/`ROADMAP_V2.md` §6.1 ("Kotlin 2.4.0 + Compose
// Multiplatform"), y con este mismo módulo shell + `compose.*` como
// dependencias (en vez de las coordenadas `androidx.compose.*` clásicas)
// queda listo para compartir composables con otros targets el día que se
// decida — mismo patrón que genera el wizard oficial de Kotlin
// Multiplatform (https://kmp.jetbrains.com) para su módulo `androidApp`.
plugins {
    alias(libs.plugins.androidApplication)
    alias(libs.plugins.composeMultiplatform)
    alias(libs.plugins.composeCompiler)
}

fun String.asBuildConfigString(): String =
    "\"" + replace("\\", "\\\\").replace("\"", "\\\"") + "\""

// El canal es configuración de distribución, no lógica de producto. El
// checkout oficial apunta al manifiesto estable público; un fork puede
// reemplazarlo con `-PedecanAndroidUpdateManifestUrl=https://...` o con la
// variable de entorno homónima en mayúsculas. La URL se compila en el APK,
// pero nunca contiene credenciales.
val androidUpdateManifestUrl = providers
    .gradleProperty("edecanAndroidUpdateManifestUrl")
    .orElse(providers.environmentVariable("EDECAN_ANDROID_UPDATE_MANIFEST_URL"))
    .orElse(
        "https://raw.githubusercontent.com/bizagencysas/edecan-oss/update-channels/android-stable.json",
    )

// Un release solo se firma si el entorno aporta el conjunto completo. Así el
// checkout OSS sigue construyendo APKs release sin firmar, mientras CI y cada
// fork pueden usar su propio keystore sin escribir rutas, alias ni contraseñas
// en Git.
val releaseKeystorePath = providers.environmentVariable("EDECAN_ANDROID_RELEASE_KEYSTORE").orNull
val releaseKeystorePassword = providers.environmentVariable("EDECAN_ANDROID_RELEASE_KEYSTORE_PASSWORD").orNull
val releaseKeyAlias = providers.environmentVariable("EDECAN_ANDROID_RELEASE_KEY_ALIAS").orNull
val releaseKeyPassword = providers.environmentVariable("EDECAN_ANDROID_RELEASE_KEY_PASSWORD").orNull
val hasReleaseSigning = listOf(
    releaseKeystorePath,
    releaseKeystorePassword,
    releaseKeyAlias,
    releaseKeyPassword,
).all { !it.isNullOrBlank() }

// El checkout OSS compila sin Firebase. Quien quiera push remoto aporta su
// propio archivo local; el plugin solo se activa cuando ese archivo existe.
if (file("google-services.json").isFile) {
    apply(plugin = "com.google.gms.google-services")
}

android {
    namespace = "cc.edecan.app"
    // 37, no 36: `androidx.core`/`androidx.lifecycle` en las versiones
    // pinned de gradle/libs.versions.toml ya exigen compilar contra API 37
    // (`checkDebugAarMetadata` lo revienta si no — verificado corriendo
    // `./gradlew :androidApp:assembleDebug` real). Mismo valor en
    // `shared/build.gradle.kts`, deben ir siempre juntos.
    compileSdk = 37

    defaultConfig {
        // Placeholder de desarrollo — cada cliente lo cambia por el suyo
        // antes de firmar con su propia cuenta/keystore (un applicationId
        // no puede repetirse entre instalaciones que convivan en el mismo
        // dispositivo). Ver docs/movil-android.md, sección "Firma release".
        applicationId = "cc.edecan.app"
        minSdk = 26
        targetSdk = 37
        versionCode = 9
        versionName = "0.7.4"
        buildConfigField(
            "String",
            "UPDATE_MANIFEST_URL",
            androidUpdateManifestUrl.get().asBuildConfigString(),
        )
    }

    signingConfigs {
        if (hasReleaseSigning) {
            create("releaseFromEnvironment") {
                storeFile = file(checkNotNull(releaseKeystorePath))
                storePassword = checkNotNull(releaseKeystorePassword)
                keyAlias = checkNotNull(releaseKeyAlias)
                keyPassword = checkNotNull(releaseKeyPassword)
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false // TODO: activar + reglas R8 propias en una siguiente iteración.
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            // SIN signingConfig propio, a propósito: este esqueleto NUNCA
            // firma un release con una key compartida (regla dura del
            // repo). Cada cliente firma con SU KEYSTORE propio — hasta que
            // ese cliente agregue su propio `signingConfig` aquí,
            // `assembleRelease` genera un .apk SIN FIRMAR, instalable solo
            // tras firmarlo a mano (`apksigner`). Ver docs/movil-android.md.
            if (hasReleaseSigning) {
                signingConfig = signingConfigs.getByName("releaseFromEnvironment")
            }
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    packaging {
        resources.excludes.add("/META-INF/{AL2.0,LGPL2.1}")
    }
}

dependencies {
    implementation(project(":shared"))

    implementation(libs.compose.runtime)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material3)
    implementation(libs.compose.ui)
    implementation(libs.compose.ui.tooling.preview)
    debugImplementation(libs.compose.ui.tooling)

    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.androidx.camera.camera2)
    implementation(libs.androidx.camera.lifecycle)
    implementation(libs.androidx.camera.view)
    implementation(libs.mlkit.barcode.scanning)
    implementation(platform(libs.firebase.bom))
    implementation(libs.firebase.messaging)
    implementation(libs.kotlinx.serialization.json)

    // `kotlin("test-junit")` (no `kotlin("test")` a secas): `androidApp` NO
    // aplica el plugin `org.jetbrains.kotlin.android` (a propósito, ver el
    // comentario al tope de este archivo sobre "Kotlin built-in" de AGP 9),
    // así que no hay sustitución automática de `kotlin("test")` al artefacto
    // JVM real — sin esto, `kotlin.test.Test` no resuelve en absoluto
    // (verificado real: `Unresolved reference 'Test'` corriendo
    // `./gradlew :androidApp:testDebugUnitTest`). `kotlin-test-junit` trae
    // JUnit4 (el test runner que ya usa por defecto la tarea `test` de
    // Gradle/AGP) como dependencia transitiva — sin pinnear una versión de
    // JUnit propia en `libs.versions.toml`. Tests JVM puros de `src/test/`
    // (p. ej. `LlmKindTest`) — nada de Compose/Robolectric todavía: la UI en
    // sí no tiene tests unitarios en este esqueleto, solo la lógica de los
    // `ViewModel`/enums que no toca el framework de Android.
    testImplementation(kotlin("test-junit"))
    testImplementation(libs.kotlinx.coroutines.test)
}
