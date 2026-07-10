package cc.edecan.shared

/**
 * Persistencia del "emparejamiento" de ESTE dispositivo con un tenant de
 * Edecán: la URL del servidor propio del tenant + el par de tokens JWT de la
 * sesión (`docs/api.md` §"Autenticación y sesión").
 *
 * **v1: el "emparejamiento" es, literalmente, tener sesión iniciada** — no
 * hay un protocolo de emparejamiento propio para el móvil todavía (a
 * diferencia del código corto que usa el companion de escritorio,
 * `POST /v1/companion/pair-code`). Mientras el refresh token viva acá, el
 * dispositivo cuenta como "emparejado". Mismo criterio, a propósito, que
 * `PairingStore`/`Keychain` en `apps/mobile/ios/EdecanKit` — ver
 * "Qué queda pendiente" en `docs/movil-android.md` para cuándo esto pasa a
 * ser un emparejamiento real por dispositivo (tabla `devices`,
 * `ROADMAP_V2.md` §6.1/§7.4).
 *
 * Interfaz simple en `commonMain` en vez de `expect`/`actual`: cada target
 * (hoy solo Android) inyecta su propia implementación concreta por
 * constructor — más flexible que `expect class` (permite fakes en tests,
 * distintos backings de almacenamiento por target sin recompilar el
 * contrato). La implementación Android real vive en
 * `androidMain/.../TokenStore.android.kt` — [DataStoreTokenStore]: DataStore
 * Preferences para la URL del servidor (no sensible) + `EncryptedSharedPreferences`
 * (`androidx.security.crypto`) para los tokens (SIEMPRE cifrados en reposo).
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

    /** `true` si hay un refresh token guardado — ver el doc de la interfaz:
     * es la definición completa de "emparejado" en este esqueleto v1. */
    suspend fun isPaired(): Boolean = getRefreshToken() != null

    /** El `id` (`devices.id`) que devolvió `POST /v1/devices` la última vez
     * que `SessionViewModel` emparejó este dispositivo con un tenant
     * (`ARCHITECTURE.md`/`ROADMAP_V2.md` §6.1/§7.4, contrato de WP-V4-01 en
     * paralelo — ver `EdecanApi.emparejarDispositivo`), o `null` si todavía
     * no se emparejó (o el servidor no tenía ese endpoint todavía). No es
     * secreto — un id de dispositivo no autentica nada por sí solo — así que
     * comparte el mismo almacenamiento no cifrado que [getServerUrl], nunca
     * el de [getAccessToken]/[getRefreshToken]. */
    suspend fun getDeviceId(): String?
    suspend fun saveDeviceId(deviceId: String)

    /** Borra el `id` guardado por [saveDeviceId] — se llama al cerrar sesión,
     * después de intentar revocarlo en el servidor (`EdecanApi.revocarDispositivo`),
     * para que un login posterior (mismo u otro tenant) empareje un
     * dispositivo nuevo en vez de reusar un id que ya se pidió revocar. */
    suspend fun clearDeviceId()
}
