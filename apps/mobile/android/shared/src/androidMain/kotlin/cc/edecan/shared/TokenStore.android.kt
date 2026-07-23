package cc.edecan.shared

import android.content.Context
import android.content.SharedPreferences
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyPermanentlyInvalidatedException
import android.security.keystore.KeyProperties
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.io.IOException
import java.security.KeyStore
import java.util.Base64
import java.util.UUID
import javax.crypto.Cipher
import javax.crypto.AEADBadTagException
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

private val Context.pairingDataStore by preferencesDataStore(name = "cc.edecan.app.pairing")
private const val KEY_ACCESS_TOKEN = "access_token"
private const val KEY_REFRESH_TOKEN = "refresh_token"
private const val KEY_SERVER_URL = "server_url"
private const val KEY_DEVICE_ID = "device_id"
private const val KEY_DEVICE_TOKEN = "device_token"
private const val KEY_INSTALLATION_ID = "installation_id"

/**
 * Implementación Android de [TokenStore] (ver su docstring en `commonMain`
 * para qué significa "emparejado" en este v1) — el equivalente Kotlin de
 * `Keychain.swift`/`PairingStore.swift` en `EdecanKit` (iOS). Split
 * deliberado en DOS mecanismos de persistencia distintos, no uno solo:
 *
 * - **Android Keystore + AES-256-GCM** para URL, JWTs, id y token durable.
 *   SharedPreferences guarda únicamente payloads cifrados versionados; la
 *   clave no exportable vive en el Keystore del dispositivo.
 * - **DataStore Preferences** queda solo para migrar instalaciones antiguas
 *   que guardaron URL/id en claro; el valor se elimina tras cifrarlo.
 *
 * Las llamadas que tocan el almacén cifrado corren en
 * [Dispatchers.IO] a propósito: la primera lectura por proceso (creación
 * de la clave maestra en el Keystore + apertura del archivo cifrado) hace
 * I/O de disco síncrono real, y esta clase no debe asumir en qué
 * dispatcher la llama quien la usa ([EdecanApi], `SessionViewModel`).
 *
 * La implementación sigue la sustitución oficial de `MasterKey` indicada por
 * AndroidX Security Crypto 1.1: `KeyGenerator` contra `AndroidKeyStore`.
 * [LegacyEncryptedTokenMigration] conserva las sesiones creadas por versiones
 * anteriores y elimina el valor antiguo después de migrarlo correctamente.
 */
class DataStoreTokenStore(private val context: Context) : TokenStore {

    private object Keys {
        val serverUrl = stringPreferencesKey("server_url")
        val deviceId = stringPreferencesKey("device_id")
    }

    // Perezoso a propósito: crear/abrir la clave maestra del Keystore es
    // I/O real y no debe pagarse en el constructor de esta clase (que se
    // instancia, entre otros, cada vez que el onboarding actualiza la URL
    // del servidor — ver `SessionViewModel.actualizarBaseUrl`).
    private val tokenVault: AndroidKeystoreTokenVault by lazy {
        AndroidKeystoreTokenVault(context.applicationContext)
    }
    private val plaintextMigrationMutex = Mutex()
    private val installationIdMutex = Mutex()

    override suspend fun getServerUrl(): String? = encryptedOrMigrate(KEY_SERVER_URL, Keys.serverUrl)

    override suspend fun saveServerUrl(url: String) {
        withContext(Dispatchers.IO) { tokenVault.putAll(mapOf(KEY_SERVER_URL to url)) }
        context.pairingDataStore.edit { it.remove(Keys.serverUrl) }
    }

    override suspend fun getDeviceId(): String? = encryptedOrMigrate(KEY_DEVICE_ID, Keys.deviceId)

    override suspend fun saveDeviceId(deviceId: String) {
        withContext(Dispatchers.IO) {
            // Un id proveniente del login clásico no puede conservar el
            // secreto durable de otra identidad/tenant.
            tokenVault.update(mapOf(KEY_DEVICE_ID to deviceId), setOf(KEY_DEVICE_TOKEN))
        }
        context.pairingDataStore.edit { it.remove(Keys.deviceId) }
    }

    override suspend fun getInstallationId(): String = installationIdMutex.withLock {
        withContext(Dispatchers.IO) {
            tokenVault.get(KEY_INSTALLATION_ID)?.let { return@withContext it }
            UUID.randomUUID().toString().also { generated ->
                tokenVault.putAll(mapOf(KEY_INSTALLATION_ID to generated))
            }
        }
    }

    override suspend fun clearDeviceId() {
        clearDevicePairing()
    }

    override suspend fun getDeviceToken(): String? = withContext(Dispatchers.IO) {
        tokenVault.get(KEY_DEVICE_TOKEN)
    }

    override suspend fun saveDevicePairing(deviceId: String, deviceToken: String) {
        withContext(Dispatchers.IO) {
            tokenVault.putAll(
                mapOf(
                    KEY_DEVICE_ID to deviceId,
                    KEY_DEVICE_TOKEN to deviceToken,
                ),
            )
        }
        context.pairingDataStore.edit { it.remove(Keys.deviceId) }
    }

    override suspend fun clearDevicePairing() {
        withContext(Dispatchers.IO) {
            tokenVault.remove(KEY_DEVICE_ID, KEY_DEVICE_TOKEN)
        }
        context.pairingDataStore.edit { it.remove(Keys.deviceId) }
    }

    override suspend fun getAccessToken(): String? = withContext(Dispatchers.IO) {
        tokenVault.get(KEY_ACCESS_TOKEN)
    }

    override suspend fun getRefreshToken(): String? = withContext(Dispatchers.IO) {
        tokenVault.get(KEY_REFRESH_TOKEN)
    }

    override suspend fun saveTokens(accessToken: String, refreshToken: String) {
        withContext(Dispatchers.IO) {
            tokenVault.putAll(
                mapOf(
                    KEY_ACCESS_TOKEN to accessToken,
                    KEY_REFRESH_TOKEN to refreshToken,
                ),
            )
        }
    }

    override suspend fun clearTokens() {
        withContext(Dispatchers.IO) {
            tokenVault.remove(KEY_ACCESS_TOKEN, KEY_REFRESH_TOKEN)
        }
    }

    private suspend fun encryptedOrMigrate(
        encryptedKey: String,
        legacyKey: Preferences.Key<String>,
    ): String? = plaintextMigrationMutex.withLock {
        withContext(Dispatchers.IO) {
            tokenVault.get(encryptedKey)?.let { return@withContext it }
            val legacy = context.pairingDataStore.data.first()[legacyKey] ?: return@withContext null
            tokenVault.putAll(mapOf(encryptedKey to legacy))
            context.pairingDataStore.edit { it.remove(legacyKey) }
            legacy
        }
    }
}

/** Payload autocontenido y versionado: `v1:<iv>:<ciphertext+tag>`. */
internal object EncryptedTokenPayloadCodec {
    private const val VERSION = "v1"
    private const val IV_BYTES = 12
    private const val GCM_TAG_BYTES = 16

    data class Payload(val iv: ByteArray, val ciphertext: ByteArray)

    fun encode(iv: ByteArray, ciphertext: ByteArray): String {
        require(iv.size == IV_BYTES) { "IV AES-GCM inválido" }
        require(ciphertext.size >= GCM_TAG_BYTES) { "Payload AES-GCM inválido" }
        val encoder = Base64.getUrlEncoder().withoutPadding()
        return "$VERSION:${encoder.encodeToString(iv)}:${encoder.encodeToString(ciphertext)}"
    }

    fun decode(value: String): Payload {
        val parts = value.split(':')
        require(parts.size == 3 && parts[0] == VERSION) { "Versión de token cifrado inválida" }
        val decoder = Base64.getUrlDecoder()
        val iv = decoder.decode(parts[1])
        val ciphertext = decoder.decode(parts[2])
        require(iv.size == IV_BYTES) { "IV AES-GCM inválido" }
        require(ciphertext.size >= GCM_TAG_BYTES) { "Payload AES-GCM inválido" }
        return Payload(iv, ciphertext)
    }
}

/** Criptografía pura y testeable; AAD impide intercambiar access/refresh. */
internal object AesGcmTokenCipher {
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val GCM_TAG_BITS = 128

    fun encrypt(plaintext: String, associatedData: String, key: SecretKey): String {
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        cipher.updateAAD(associatedData.toByteArray(Charsets.UTF_8))
        val ciphertext = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        return EncryptedTokenPayloadCodec.encode(cipher.iv, ciphertext)
    }

    fun decrypt(payload: String, associatedData: String, key: SecretKey): String {
        val decoded = EncryptedTokenPayloadCodec.decode(payload)
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.DECRYPT_MODE, key, GCMParameterSpec(GCM_TAG_BITS, decoded.iv))
        cipher.updateAAD(associatedData.toByteArray(Charsets.UTF_8))
        return cipher.doFinal(decoded.ciphertext).toString(Charsets.UTF_8)
    }
}

private class AndroidKeystoreTokenVault(private val context: Context) {
    private val preferences: SharedPreferences =
        context.getSharedPreferences(PREFERENCES_FILE, Context.MODE_PRIVATE)
    private val key: SecretKey by lazy(::loadOrCreateKey)

    init {
        migrateLegacyTokens()
    }

    fun get(name: String): String? {
        val encrypted = preferences.getString(name, null) ?: return null
        return try {
            AesGcmTokenCipher.decrypt(encrypted, name, key)
        } catch (_: IllegalArgumentException) {
            clearCorruptedValue(name)
            null
        } catch (_: AEADBadTagException) {
            clearCorruptedValue(name)
            null
        } catch (_: KeyPermanentlyInvalidatedException) {
            clearCorruptedValue(name)
            null
        }
    }

    fun putAll(values: Map<String, String>) {
        update(values, emptySet())
    }

    fun remove(vararg names: String) {
        update(emptyMap(), names.toSet())
    }

    fun update(values: Map<String, String>, removals: Set<String>) {
        val editor = preferences.edit()
        removals.forEach(editor::remove)
        values.forEach { (name, value) ->
            editor.putString(name, AesGcmTokenCipher.encrypt(value, name, key))
        }
        val committed = editor
            .putBoolean(KEY_LEGACY_MIGRATION_COMPLETE, true)
            .commit()
        requireCommitted(committed)
        LegacyEncryptedTokenMigration.clear(context)
    }

    private fun migrateLegacyTokens() {
        synchronized(MIGRATION_LOCK) {
            if (preferences.getBoolean(KEY_LEGACY_MIGRATION_COMPLETE, false)) return@synchronized
            val legacyTokens = try {
                LegacyEncryptedTokenMigration.read(context)
            } catch (_: Exception) {
                // Conserva el almacén antiguo intacto para reintentar en la
                // próxima instancia; nunca sobreescribe datos que no pudo leer.
                return@synchronized
            }

            val editor = preferences.edit().putBoolean(KEY_LEGACY_MIGRATION_COMPLETE, true)
            legacyTokens?.accessToken?.let {
                editor.putString(
                    KEY_ACCESS_TOKEN,
                    AesGcmTokenCipher.encrypt(it, KEY_ACCESS_TOKEN, key),
                )
            }
            legacyTokens?.refreshToken?.let {
                editor.putString(
                    KEY_REFRESH_TOKEN,
                    AesGcmTokenCipher.encrypt(it, KEY_REFRESH_TOKEN, key),
                )
            }
            requireCommitted(editor.commit())
            LegacyEncryptedTokenMigration.clear(context)
        }
    }

    private fun clearCorruptedValue(name: String) {
        val related = when (name) {
            KEY_ACCESS_TOKEN, KEY_REFRESH_TOKEN -> setOf(KEY_ACCESS_TOKEN, KEY_REFRESH_TOKEN)
            KEY_DEVICE_ID, KEY_DEVICE_TOKEN -> setOf(KEY_DEVICE_ID, KEY_DEVICE_TOKEN)
            else -> setOf(name)
        }
        val editor = preferences.edit()
        related.forEach(editor::remove)
        editor.putBoolean(KEY_LEGACY_MIGRATION_COMPLETE, true).commit()
        LegacyEncryptedTokenMigration.clear(context)
    }

    private fun loadOrCreateKey(): SecretKey = synchronized(KEYSTORE_LOCK) {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey) ?: run {
            val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
            generator.init(
                KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setRandomizedEncryptionRequired(true)
                    .build(),
            )
            generator.generateKey()
        }
    }

    private fun requireCommitted(committed: Boolean) {
        if (!committed) throw IOException("No se pudo persistir el almacén cifrado de tokens")
    }

    private companion object {
        const val PREFERENCES_FILE = "cc.edecan.app.tokens.v2"
        const val KEY_ALIAS = "cc.edecan.app.tokens.aes_gcm.v1"
        const val KEY_LEGACY_MIGRATION_COMPLETE = "legacy_migration_complete"
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        val KEYSTORE_LOCK = Any()
        val MIGRATION_LOCK = Any()
    }
}
