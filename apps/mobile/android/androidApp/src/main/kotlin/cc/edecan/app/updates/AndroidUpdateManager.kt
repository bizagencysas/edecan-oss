package cc.edecan.app.updates

import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.provider.Settings
import androidx.core.content.FileProvider
import cc.edecan.app.BuildConfig
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.FileOutputStream
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.security.MessageDigest
import java.util.Locale

private const val UPDATE_CHECK_INTERVAL_MS = 4L * 60L * 60L * 1_000L
private const val HTTP_CONNECT_TIMEOUT_MS = 15_000
private const val HTTP_READ_TIMEOUT_MS = 30_000
private const val MAX_REDIRECTS = 5
private const val PREFS_NAME = "edecan_android_updates"
private const val PREF_LAST_CHECK = "last_check_ms"
private const val PREF_LAST_MANIFEST = "last_manifest"
private const val PREF_LAST_ETAG = "last_etag"

sealed interface AndroidUpdateState {
    data object Idle : AndroidUpdateState
    data object Checking : AndroidUpdateState
    data class UpToDate(val checkedVersion: String) : AndroidUpdateState
    data class Available(val manifest: AndroidUpdateManifest) : AndroidUpdateState
    data class Downloading(val manifest: AndroidUpdateManifest, val percent: Int) : AndroidUpdateState
    data class Ready(val manifest: AndroidUpdateManifest, val apk: File) : AndroidUpdateState
    data class PermissionRequired(val manifest: AndroidUpdateManifest, val apk: File) : AndroidUpdateState
    data class Error(val message: String, val canRetry: Boolean = true) : AndroidUpdateState
}

/**
 * Actualizador para la distribución APK OSS.
 *
 * - Busca un manifiesto público por HTTPS.
 * - Verifica hash, tamaño, package id, versionCode y certificado.
 * - Delega la instalación al Package Installer de Android, que siempre pide
 *   confirmación a la persona.
 *
 * No toca conversaciones, credenciales ni la conexión con el Edecán de
 * escritorio.
 */
object AndroidUpdateManager {
    private val mutex = Mutex()
    private val _state = MutableStateFlow<AndroidUpdateState>(AndroidUpdateState.Idle)
    val state: StateFlow<AndroidUpdateState> = _state.asStateFlow()

    suspend fun check(context: Context, force: Boolean = false) = mutex.withLock {
        val appContext = context.applicationContext
        val manifestUrl = BuildConfig.UPDATE_MANIFEST_URL.trim()
        if (manifestUrl.isBlank()) {
            _state.value = AndroidUpdateState.Error(
                "Este build no tiene un canal de actualizaciones configurado.",
                canRetry = false,
            )
            return@withLock
        }

        val prefs = appContext.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val now = System.currentTimeMillis()
        val lastCheck = prefs.getLong(PREF_LAST_CHECK, 0L)
        if (!force && now - lastCheck in 0 until UPDATE_CHECK_INTERVAL_MS) {
            restoreCachedState(prefs.getString(PREF_LAST_MANIFEST, null))
            return@withLock
        }

        _state.value = AndroidUpdateState.Checking
        try {
            AndroidUpdateManifestParser.requireHttps(manifestUrl, "El canal de actualización")
            val response = withContext(Dispatchers.IO) {
                fetchManifest(manifestUrl, prefs.getString(PREF_LAST_ETAG, null))
            }
            val raw = when {
                response.notModified ->
                    prefs.getString(PREF_LAST_MANIFEST, null)
                        ?: throw UpdateFailure("El canal respondió sin contenido utilizable.")
                else -> response.body
            }
            val manifest = AndroidUpdateManifestParser.parse(raw)
            prefs.edit()
                .putLong(PREF_LAST_CHECK, now)
                .putString(PREF_LAST_MANIFEST, raw)
                .apply {
                    response.etag?.let { putString(PREF_LAST_ETAG, it) }
                }
                .apply()
            showManifest(manifest)
        } catch (failure: Throwable) {
            _state.value = AndroidUpdateState.Error(failure.toUserMessage())
        }
    }

    suspend fun download(context: Context) = mutex.withLock {
        val appContext = context.applicationContext
        val manifest = when (val current = _state.value) {
            is AndroidUpdateState.Available -> current.manifest
            is AndroidUpdateState.Error -> cachedManifest(appContext)
            else -> null
        } ?: return@withLock

        if (manifest.versionCode <= BuildConfig.VERSION_CODE) {
            _state.value = AndroidUpdateState.UpToDate(BuildConfig.VERSION_NAME)
            return@withLock
        }

        try {
            val verified = withContext(Dispatchers.IO) {
                downloadAndVerify(appContext, manifest) { percent ->
                    _state.value = AndroidUpdateState.Downloading(manifest, percent)
                }
            }
            _state.value = AndroidUpdateState.Ready(manifest, verified)
        } catch (failure: Throwable) {
            _state.value = AndroidUpdateState.Error(failure.toUserMessage())
        }
    }

    fun install(context: Context) {
        val (manifest, apk) = when (val current = _state.value) {
            is AndroidUpdateState.Ready -> current.manifest to current.apk
            is AndroidUpdateState.PermissionRequired -> current.manifest to current.apk
            else -> return
        }
        if (!apk.isFile) {
            _state.value = AndroidUpdateState.Error("La descarga ya no está disponible. Descárgala otra vez.")
            return
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O &&
            !context.packageManager.canRequestPackageInstalls()
        ) {
            _state.value = AndroidUpdateState.PermissionRequired(manifest, apk)
            val settingsIntent = Intent(
                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                Uri.parse("package:${context.packageName}"),
            ).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            runCatching { context.startActivity(settingsIntent) }.onFailure {
                _state.value = AndroidUpdateState.Error(
                    "No pude abrir el permiso de instalación. Ábrelo en Ajustes de Android.",
                )
            }
            return
        }

        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.files",
            apk,
        )
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri, "application/vnd.android.package-archive")
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_ACTIVITY_NEW_TASK)
        runCatching { context.startActivity(intent) }.onFailure {
            _state.value = AndroidUpdateState.Error(
                "Android no encontró un instalador de APK compatible.",
            )
        }
    }

    private fun restoreCachedState(raw: String?) {
        val manifest = raw?.let { runCatching { AndroidUpdateManifestParser.parse(it) }.getOrNull() }
        if (manifest == null) {
            _state.value = AndroidUpdateState.Idle
        } else {
            showManifest(manifest)
        }
    }

    private fun showManifest(manifest: AndroidUpdateManifest) {
        _state.value = if (manifest.versionCode > BuildConfig.VERSION_CODE) {
            AndroidUpdateState.Available(manifest)
        } else {
            AndroidUpdateState.UpToDate(BuildConfig.VERSION_NAME)
        }
    }

    private fun cachedManifest(context: Context): AndroidUpdateManifest? =
        context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            .getString(PREF_LAST_MANIFEST, null)
            ?.let { runCatching { AndroidUpdateManifestParser.parse(it) }.getOrNull() }
            ?.takeIf { it.versionCode > BuildConfig.VERSION_CODE }
}

private data class ManifestResponse(
    val body: String,
    val etag: String?,
    val notModified: Boolean,
)

private fun fetchManifest(url: String, etag: String?): ManifestResponse {
    val connection = openHttps(url) {
        requestMethod = "GET"
        setRequestProperty("Accept", "application/json")
        setRequestProperty("User-Agent", "Edecan-Android/${BuildConfig.VERSION_NAME}")
        etag?.let { setRequestProperty("If-None-Match", it) }
    }
    return connection.useConnection {
        when (responseCode) {
            HttpURLConnection.HTTP_NOT_MODIFIED -> ManifestResponse("", null, true)
            HttpURLConnection.HTTP_OK -> {
                val bytes = inputStream.use { it.readAtMost(256 * 1024) }
                ManifestResponse(
                    body = bytes.toString(Charsets.UTF_8),
                    etag = getHeaderField("ETag"),
                    notModified = false,
                )
            }
            else -> throw UpdateFailure("El canal de actualización respondió HTTP $responseCode.")
        }
    }
}

private fun downloadAndVerify(
    context: Context,
    manifest: AndroidUpdateManifest,
    onProgress: (Int) -> Unit,
): File {
    AndroidUpdateManifestParser.requireHttps(manifest.apkUrl, "La descarga")
    val updatesDir = File(context.cacheDir, "updates").apply { mkdirs() }
    require(updatesDir.isDirectory) { "No se pudo preparar la carpeta de actualización." }
    updatesDir.listFiles()?.forEach { existing ->
        if (existing.name != ".nomedia") existing.delete()
    }
    runCatching { File(updatesDir, ".nomedia").createNewFile() }

    val partial = File(updatesDir, "edecan-${manifest.versionCode}.apk.part")
    val verified = File(updatesDir, "edecan-${manifest.versionCode}.apk")
    val digest = MessageDigest.getInstance("SHA-256")
    var downloaded = 0L
    var lastPercent = -1

    try {
        val connection = openHttps(manifest.apkUrl) {
            requestMethod = "GET"
            setRequestProperty("Accept", "application/vnd.android.package-archive")
            setRequestProperty("User-Agent", "Edecan-Android/${BuildConfig.VERSION_NAME}")
        }
        connection.useConnection {
            if (responseCode != HttpURLConnection.HTTP_OK) {
                throw UpdateFailure("La descarga respondió HTTP $responseCode.")
            }
            val contentLength = getHeaderFieldLong("Content-Length", -1)
            if (contentLength > 0 && contentLength != manifest.apkSizeBytes) {
                throw UpdateFailure("El tamaño publicado no coincide con la descarga.")
            }
            inputStream.use { input ->
                FileOutputStream(partial).use { output ->
                    val buffer = ByteArray(DEFAULT_BUFFER_SIZE * 8)
                    while (true) {
                        val read = input.read(buffer)
                        if (read < 0) break
                        downloaded += read
                        if (downloaded > manifest.apkSizeBytes ||
                            downloaded > MAX_ANDROID_UPDATE_BYTES
                        ) {
                            throw UpdateFailure("La descarga superó el tamaño publicado.")
                        }
                        digest.update(buffer, 0, read)
                        output.write(buffer, 0, read)
                        val percent = ((downloaded * 100L) / manifest.apkSizeBytes)
                            .toInt()
                            .coerceIn(0, 100)
                        if (percent != lastPercent) {
                            lastPercent = percent
                            onProgress(percent)
                        }
                    }
                    output.fd.sync()
                }
            }
        }

        if (downloaded != manifest.apkSizeBytes) {
            throw UpdateFailure("La descarga quedó incompleta.")
        }
        val actualHash = digest.digest().toHex()
        if (!MessageDigest.isEqual(
                actualHash.toByteArray(Charsets.US_ASCII),
                manifest.apkSha256.toByteArray(Charsets.US_ASCII),
            )
        ) {
            throw UpdateFailure("El APK descargado no coincide con el publicado.")
        }
        verifyApkIdentity(context, partial, manifest)
        if (!partial.renameTo(verified)) {
            partial.copyTo(verified, overwrite = true)
            partial.delete()
        }
        return verified
    } catch (failure: Throwable) {
        partial.delete()
        verified.delete()
        throw failure
    }
}

private fun verifyApkIdentity(
    context: Context,
    apk: File,
    manifest: AndroidUpdateManifest,
) {
    val packageManager = context.packageManager
    val archive = packageManager.packageArchiveInfo(apk)
        ?: throw UpdateFailure("Android no reconoce el archivo como un APK válido.")
    if (archive.packageName != context.packageName) {
        throw UpdateFailure("El APK pertenece a otra aplicación.")
    }
    if (archive.longVersionCodeCompat() != manifest.versionCode.toLong()) {
        throw UpdateFailure("El versionCode del APK no coincide con el manifiesto.")
    }
    if (archive.versionName != manifest.versionName) {
        throw UpdateFailure("La versión visible del APK no coincide con el manifiesto.")
    }
    if (archive.longVersionCodeCompat() <= BuildConfig.VERSION_CODE.toLong()) {
        throw UpdateFailure("Android no permite reemplazar la app por una versión anterior.")
    }

    val installed = packageManager.installedPackageInfo(context.packageName)
    val installedCertificates = installed.signingCertificateDigests()
    val archiveCertificates = archive.signingCertificateDigests()
    if (installedCertificates.isEmpty() ||
        archiveCertificates.isEmpty() ||
        installedCertificates.intersect(archiveCertificates).isEmpty()
    ) {
        throw UpdateFailure(
            "La actualización no está firmada por el mismo distribuidor que esta instalación.",
        )
    }
}

@Suppress("DEPRECATION")
private fun PackageManager.packageArchiveInfo(apk: File): PackageInfo? =
    if (Build.VERSION.SDK_INT >= 33) {
        getPackageArchiveInfo(
            apk.absolutePath,
            PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong()),
        )
    } else if (Build.VERSION.SDK_INT >= 28) {
        getPackageArchiveInfo(apk.absolutePath, PackageManager.GET_SIGNING_CERTIFICATES)
    } else {
        getPackageArchiveInfo(apk.absolutePath, PackageManager.GET_SIGNATURES)
    }

@Suppress("DEPRECATION")
private fun PackageManager.installedPackageInfo(packageName: String): PackageInfo =
    if (Build.VERSION.SDK_INT >= 33) {
        getPackageInfo(
            packageName,
            PackageManager.PackageInfoFlags.of(PackageManager.GET_SIGNING_CERTIFICATES.toLong()),
        )
    } else if (Build.VERSION.SDK_INT >= 28) {
        getPackageInfo(packageName, PackageManager.GET_SIGNING_CERTIFICATES)
    } else {
        getPackageInfo(packageName, PackageManager.GET_SIGNATURES)
    }

@Suppress("DEPRECATION")
private fun PackageInfo.longVersionCodeCompat(): Long =
    if (Build.VERSION.SDK_INT >= 28) longVersionCode else versionCode.toLong()

private fun PackageInfo.signingCertificateDigests(): Set<String> {
    @Suppress("DEPRECATION")
    val signatures = if (Build.VERSION.SDK_INT >= 28) {
        val info = signingInfo ?: return emptySet()
        buildList {
            addAll(info.apkContentsSigners.orEmpty())
            addAll(info.signingCertificateHistory.orEmpty())
        }
    } else {
        signatures.orEmpty().toList()
    }
    return signatures.mapTo(mutableSetOf()) { signature ->
        MessageDigest.getInstance("SHA-256")
            .digest(signature.toByteArray())
            .toHex()
    }
}

private fun openHttps(
    rawUrl: String,
    configure: HttpURLConnection.() -> Unit,
): HttpURLConnection {
    var current = AndroidUpdateManifestParser.requireHttps(rawUrl).toURL()
    repeat(MAX_REDIRECTS + 1) { redirectCount ->
        val connection = (current.openConnection() as HttpURLConnection).apply {
            instanceFollowRedirects = false
            connectTimeout = HTTP_CONNECT_TIMEOUT_MS
            readTimeout = HTTP_READ_TIMEOUT_MS
            useCaches = false
            configure()
        }
        val status = connection.responseCode
        if (status !in 300..399) return connection
        val location = connection.getHeaderField("Location")
            ?: throw UpdateFailure("La descarga redirigió sin destino.")
        connection.disconnect()
        if (redirectCount == MAX_REDIRECTS) {
            throw UpdateFailure("La descarga tiene demasiadas redirecciones.")
        }
        val resolved = URI(current.toString()).resolve(location).toString()
        current = AndroidUpdateManifestParser.requireHttps(resolved, "La redirección").toURL()
    }
    throw UpdateFailure("No se pudo resolver la descarga.")
}

private inline fun <T> HttpURLConnection.useConnection(block: HttpURLConnection.() -> T): T =
    try {
        block()
    } finally {
        disconnect()
    }

private fun java.io.InputStream.readAtMost(maxBytes: Int): ByteArray {
    val output = ByteArrayOutputStream()
    val buffer = ByteArray(8 * 1024)
    var total = 0
    while (true) {
        val read = read(buffer)
        if (read < 0) break
        total += read
        if (total > maxBytes) throw UpdateFailure("El manifiesto es demasiado grande.")
        output.write(buffer, 0, read)
    }
    return output.toByteArray()
}

private fun ByteArray.toHex(): String =
    joinToString(separator = "") { "%02x".format(Locale.ROOT, it.toInt() and 0xff) }

private class UpdateFailure(message: String) : IllegalStateException(message)

private fun Throwable.toUserMessage(): String = when (this) {
    is UpdateFailure, is IllegalArgumentException -> message ?: "La actualización no es válida."
    else -> "No pude comprobar la actualización. Revisa Internet e inténtalo de nuevo."
}
