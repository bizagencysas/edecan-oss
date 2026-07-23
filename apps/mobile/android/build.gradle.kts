// Build raíz: solo declara (sin aplicar) los plugins que usan los módulos
// hijos, para que Gradle resuelva sus versiones UNA sola vez desde
// `gradle/libs.versions.toml` en vez de repetirlas en cada módulo.
plugins {
    alias(libs.plugins.androidApplication) apply false
    alias(libs.plugins.androidKotlinMultiplatformLibrary) apply false
    alias(libs.plugins.kotlinMultiplatform) apply false
    alias(libs.plugins.kotlinAndroid) apply false
    alias(libs.plugins.kotlinSerialization) apply false
    alias(libs.plugins.composeMultiplatform) apply false
    alias(libs.plugins.composeCompiler) apply false
    alias(libs.plugins.googleServices) apply false
}
