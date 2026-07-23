package cc.edecan.app.updates

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.longOrNull
import java.net.URI

internal const val ANDROID_UPDATE_SCHEMA_VERSION = 1
internal const val MAX_ANDROID_UPDATE_BYTES = 750L * 1024L * 1024L
private val SHA256_PATTERN = Regex("^[a-fA-F0-9]{64}$")

data class AndroidUpdateManifest(
    val channel: String,
    val versionCode: Int,
    val versionName: String,
    val publishedAt: String,
    val releaseNotes: String,
    val apkUrl: String,
    val apkSha256: String,
    val apkSizeBytes: Long,
)

/**
 * Parser estricto del puntero público. El APK sigue siendo la raíz de
 * confianza (Android exige la misma firma que la app instalada), pero fallar
 * cerrado aquí evita descargas ambiguas, downgrades y archivos gigantes.
 */
object AndroidUpdateManifestParser {
    fun parse(raw: String): AndroidUpdateManifest {
        require(raw.toByteArray(Charsets.UTF_8).size <= 256 * 1024) {
            "El manifiesto de actualización es demasiado grande."
        }
        val root = Json.parseToJsonElement(raw).jsonObject
        require(root.requiredInt("schema_version") == ANDROID_UPDATE_SCHEMA_VERSION) {
            "Versión de manifiesto no compatible."
        }

        val channel = root.requiredString("channel")
        require(channel == "stable" || channel == "preview") {
            "Canal de actualización no compatible."
        }
        val versionCode = root.requiredInt("version_code")
        require(versionCode > 0) { "version_code debe ser positivo." }
        val versionName = root.requiredString("version_name")
        require(versionName.length in 1..80 && !versionName.any(Char::isWhitespace)) {
            "version_name no es válido."
        }

        val apk = root["apk"]?.jsonObject
            ?: throw IllegalArgumentException("Falta el objeto apk.")
        val apkUrl = apk.requiredString("url")
        requireHttps(apkUrl, "La URL del APK")
        val sha256 = apk.requiredString("sha256").lowercase()
        require(SHA256_PATTERN.matches(sha256)) { "El SHA-256 del APK no es válido." }
        val sizeBytes = apk.requiredLong("size_bytes")
        require(sizeBytes in 1..MAX_ANDROID_UPDATE_BYTES) {
            "El tamaño declarado del APK no es válido."
        }

        return AndroidUpdateManifest(
            channel = channel,
            versionCode = versionCode,
            versionName = versionName,
            publishedAt = root.optionalString("published_at"),
            releaseNotes = root.optionalString("release_notes").take(20_000),
            apkUrl = apkUrl,
            apkSha256 = sha256,
            apkSizeBytes = sizeBytes,
        )
    }

    fun requireHttps(rawUrl: String, label: String = "La URL"): URI {
        val uri = runCatching { URI(rawUrl) }.getOrElse {
            throw IllegalArgumentException("$label no es válida.")
        }
        require(uri.scheme.equals("https", ignoreCase = true) && !uri.host.isNullOrBlank()) {
            "$label debe usar HTTPS."
        }
        require(uri.userInfo == null && uri.fragment == null) {
            "$label contiene componentes no permitidos."
        }
        return uri
    }
}

private fun JsonObject.requiredString(name: String): String =
    this[name]?.jsonPrimitive?.contentOrNull?.trim()?.takeIf(String::isNotEmpty)
        ?: throw IllegalArgumentException("Falta $name.")

private fun JsonObject.optionalString(name: String): String =
    this[name]?.jsonPrimitive?.contentOrNull?.trim().orEmpty()

private fun JsonObject.requiredInt(name: String): Int =
    this[name]?.jsonPrimitive?.intOrNull
        ?: throw IllegalArgumentException("Falta $name.")

private fun JsonObject.requiredLong(name: String): Long =
    this[name]?.jsonPrimitive?.longOrNull
        ?: throw IllegalArgumentException("Falta $name.")
