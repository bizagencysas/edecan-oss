package cc.edecan.shared

import android.content.Context
import android.content.SharedPreferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.withContext

private val Context.pairingDataStore by preferencesDataStore(name = "cc.edecan.app.pairing")

/**
 * Implementación Android de [TokenStore] (ver su docstring en `commonMain`
 * para qué significa "emparejado" en este v1) — el equivalente Kotlin de
 * `Keychain.swift`/`PairingStore.swift` en `EdecanKit` (iOS). Split
 * deliberado en DOS mecanismos de persistencia distintos, no uno solo:
 *
 * - **DataStore Preferences** para la URL del servidor: NO es un dato
 *   sensible (ni siquiera secreto dentro del propio dispositivo) —
 *   DataStore es el reemplazo moderno recomendado de `SharedPreferences`
 *   para esto, con una API 100% basada en coroutines.
 * - **`EncryptedSharedPreferences`** (`androidx.security.crypto`) SOLO
 *   para el par de tokens JWT: son credenciales de sesión reales y deben
 *   quedar SIEMPRE cifradas en reposo (claves AES-256-SIV, valores
 *   AES-256-GCM, con la clave maestra respaldada por el Android Keystore
 *   — nunca texto plano en el filesystem de la app).
 *
 * Las llamadas que tocan `EncryptedSharedPreferences` corren en
 * [Dispatchers.IO] a propósito: la primera lectura por proceso (creación
 * de la clave maestra en el Keystore + apertura del archivo cifrado) hace
 * I/O de disco síncrono real, y esta clase no debe asumir en qué
 * dispatcher la llama quien la usa ([EdecanApi], `SessionViewModel`).
 *
 * Nota de versión: `MasterKey`/`EncryptedSharedPreferences` están
 * marcadas `@Deprecated` desde `androidx.security.crypto` 1.1.0 (el
 * reemplazo sugerido por Google es integrar Tink directamente,
 * `com.google.crypto.tink:tink-android`, sin una envoltura equivalente de
 * alto nivel todavía). Se usan aquí de todas formas, a propósito: siguen
 * siendo la API cifrada estándar, totalmente funcional, sin fecha de
 * remoción anunciada — migrar a Tink crudo es bastante más código/superficie
 * de errores criptográficos para un esqueleto v1, y no cambia el contrato
 * de [TokenStore] si se hace más adelante (el reemplazo quedaría 100%
 * contenido en este archivo).
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
    private val prefsCifradas: SharedPreferences by lazy {
        val masterKey = MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build()
        EncryptedSharedPreferences.create(
            context,
            "cc.edecan.app.tokens",
            masterKey,
            EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
            EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
        )
    }

    override suspend fun getServerUrl(): String? =
        context.pairingDataStore.data.first()[Keys.serverUrl]

    override suspend fun saveServerUrl(url: String) {
        context.pairingDataStore.edit { it[Keys.serverUrl] = url }
    }

    override suspend fun getDeviceId(): String? =
        context.pairingDataStore.data.first()[Keys.deviceId]

    override suspend fun saveDeviceId(deviceId: String) {
        context.pairingDataStore.edit { it[Keys.deviceId] = deviceId }
    }

    override suspend fun clearDeviceId() {
        context.pairingDataStore.edit { it.remove(Keys.deviceId) }
    }

    override suspend fun getAccessToken(): String? = withContext(Dispatchers.IO) {
        prefsCifradas.getString(KEY_ACCESS_TOKEN, null)
    }

    override suspend fun getRefreshToken(): String? = withContext(Dispatchers.IO) {
        prefsCifradas.getString(KEY_REFRESH_TOKEN, null)
    }

    override suspend fun saveTokens(accessToken: String, refreshToken: String) {
        withContext(Dispatchers.IO) {
            prefsCifradas.edit()
                .putString(KEY_ACCESS_TOKEN, accessToken)
                .putString(KEY_REFRESH_TOKEN, refreshToken)
                .apply()
        }
    }

    override suspend fun clearTokens() {
        withContext(Dispatchers.IO) {
            prefsCifradas.edit()
                .remove(KEY_ACCESS_TOKEN)
                .remove(KEY_REFRESH_TOKEN)
                .apply()
        }
    }

    private companion object {
        const val KEY_ACCESS_TOKEN = "access_token"
        const val KEY_REFRESH_TOKEN = "refresh_token"
    }
}
