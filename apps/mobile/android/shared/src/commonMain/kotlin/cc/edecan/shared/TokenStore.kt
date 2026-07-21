package cc.edecan.shared

/**
 * Persistencia del "emparejamiento" de ESTE dispositivo con un tenant de
 * Edecán: URL del servidor, sesión JWT y credencial durable del dispositivo.
 * La implementación Android cifra todos estos valores con Android Keystore.
 *
 * Desde v0.7 el QR entrega una credencial durable por dispositivo. Los JWT
 * siguen rotando normalmente; si su refresh deja de servir, esa credencial
 * puede recuperar un par nuevo sin pedir contraseña.
 *
 * Interfaz simple en `commonMain` en vez de `expect`/`actual`: cada target
 * (hoy solo Android) inyecta su propia implementación concreta por
 * constructor — más flexible que `expect class` (permite fakes en tests,
 * distintos backings de almacenamiento por target sin recompilar el
 * contrato). La implementación Android real vive en
 * `androidMain/.../TokenStore.android.kt` — [DataStoreTokenStore]: DataStore
 * Preferences únicamente como fuente de migración. La URL, los JWT, el id y
 * el token durable terminan todos bajo Android Keystore + AES-GCM.
 */
interface TokenStore {
    suspend fun getServerUrl(): String?
    suspend fun saveServerUrl(url: String)

    suspend fun getAccessToken(): String?
    suspend fun getRefreshToken(): String?

    /** Guarda el par de tokens de una sesión nueva (tras login/register) o
     * renovada (tras refresh) — reemplaza cualquier valor previo. */
    suspend fun saveTokens(accessToken: String, refreshToken: String)

    /** Cierra sesión EN ESTE DISPOSITIVO: borra los tokens. Deliberadamente
     * NO borra la URL del servidor — la próxima vez que se abra la app (este
     * u otro tenant del mismo servidor) no debería hacer falta volver a
     * escribirla. */
    suspend fun clearTokens()

    /** Un JWT renovable o una credencial durable completa permiten recuperar
     * la sesión sin volver a pedir correo/contraseña. */
    suspend fun isPaired(): Boolean = getRefreshToken() != null ||
        (getDeviceId() != null && getDeviceToken() != null)

    /** El `id` (`devices.id`) que devolvió `POST /v1/devices` la última vez
     * que `SessionViewModel` emparejó este dispositivo con un tenant
     * (`ARCHITECTURE.md`/`ROADMAP_V2.md` §6.1/§7.4, contrato de WP-V4-01 en
     * paralelo — ver `EdecanApi.emparejarDispositivo`), o `null` si todavía
     * no se emparejó (o el servidor no tenía ese endpoint todavía). Aunque
     * no autentica por sí solo, Android v0.7 también lo cifra para que toda
     * la identidad persistida siga una única política. */
    suspend fun getDeviceId(): String?
    suspend fun saveDeviceId(deviceId: String)

    /** Secreto durable emitido únicamente por el claim QR. Nunca debe
     * guardarse en DataStore plano, logs, SavedStateHandle ni UI. */
    suspend fun getDeviceToken(): String? = null

    /** Persiste id+secreto como una sola identidad lógica. Implementaciones
     * antiguas degradan a guardar solo el id y no habilitan refresh durable. */
    suspend fun saveDevicePairing(deviceId: String, deviceToken: String) {
        saveDeviceId(deviceId)
    }

    /** Borra el `id` guardado por [saveDeviceId] — se llama al cerrar sesión,
     * después de intentar revocarlo en el servidor (`EdecanApi.revocarDispositivo`),
     * para que un login posterior (mismo u otro tenant) empareje un
     * dispositivo nuevo en vez de reusar un id que ya se pidió revocar. */
    suspend fun clearDeviceId()

    suspend fun clearDevicePairing() {
        clearDeviceId()
    }
}
