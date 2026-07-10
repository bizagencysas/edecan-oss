@file:Suppress("UnstableApiUsage")

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "edecan-android"

// `shared`: capa de red/dominio KMP (docs/api.md) reutilizable por Android
// hoy y por iOS más adelante — ver ROADMAP_V2.md §6.1 y shared/build.gradle.kts.
include(":shared")

// `androidApp`: el APK real (login + chat SSE básico + 6 pestañas).
include(":androidApp")
