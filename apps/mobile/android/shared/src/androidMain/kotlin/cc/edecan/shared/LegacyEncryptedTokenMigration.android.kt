@file:Suppress("DEPRECATION")

package cc.edecan.shared

import android.content.Context
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Puente de compatibilidad de lectura única para instalaciones anteriores.
 *
 * La aplicación ya no escribe tokens con estas APIs deprecadas. Se mantienen
 * exclusivamente para migrar el archivo existente sin cerrar sesiones durante
 * una actualización; después de un guardado correcto, sus valores se borran.
 */
internal object LegacyEncryptedTokenMigration {
    private const val PREFERENCES_FILE = "cc.edecan.app.tokens"
    private const val KEY_ACCESS_TOKEN = "access_token"
    private const val KEY_REFRESH_TOKEN = "refresh_token"

    data class Tokens(val accessToken: String?, val refreshToken: String?)

    fun read(context: Context): Tokens? {
        val rawPreferences =
            context.getSharedPreferences(PREFERENCES_FILE, Context.MODE_PRIVATE)
        if (rawPreferences.all.isEmpty()) return null
        val preferences = encryptedPreferences(context)
        return Tokens(
            accessToken = preferences.getString(KEY_ACCESS_TOKEN, null),
            refreshToken = preferences.getString(KEY_REFRESH_TOKEN, null),
        )
    }

    fun clear(context: Context) {
        val rawPreferences =
            context.getSharedPreferences(PREFERENCES_FILE, Context.MODE_PRIVATE)
        if (rawPreferences.all.isEmpty()) return
        runCatching {
            if (encryptedPreferences(context).edit().clear().commit()) {
                context.deleteSharedPreferences(PREFERENCES_FILE)
            }
        }
    }

    private fun encryptedPreferences(context: Context) = EncryptedSharedPreferences.create(
        context,
        PREFERENCES_FILE,
        MasterKey.Builder(context)
            .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
            .build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )
}
