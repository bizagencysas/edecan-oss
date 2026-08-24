@file:Suppress("UnstableApiUsage")

pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

plugins {
    // Gradle 10 elimina la descarga implícita de toolchains sin repositorio.
    // Este resolver oficial del proyecto Gradle mantiene reproducible JDK 17.
    id("org.gradle.toolchains.foojay-resolver-convention") version "1.0.0"
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

// `androidApp`: el APK real (login + chat SSE + 3 destinos assistant-first).
include(":androidApp")
