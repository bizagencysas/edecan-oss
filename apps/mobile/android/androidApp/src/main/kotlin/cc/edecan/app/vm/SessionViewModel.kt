package cc.edecan.app.vm

import android.app.Application
import android.os.Build
import android.provider.Settings
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.DataStoreTokenStore
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Me
import cc.edecan.shared.TokenStore
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

/** Paso del onboarding — mismo split de 2 pasos que `OnboardingView.swift`
 * (EdecanApp/iOS): servidor primero, sesión después. */
enum class PasoOnboarding { SERVIDOR, SESION }

/** Dentro del paso [PasoOnboarding.SESION]: iniciar sesión en una cuenta
 * existente, o crear una cuenta+tenant nueva (`POST /v1/auth/register`,
 * WP-V4-04) — mismo formulario, un solo campo extra (`tenantName`). */
enum class ModoSesion { INICIAR, REGISTRAR }

data class SessionUiState(
    /** `true` mientras se lee el [TokenStore] al arrancar la app — la UI
     * no debe decidir onboarding-vs-app hasta que esto sea `false`. */
    val cargandoInicial: Boolean = true,
    val serverUrl: String? = null,
    val isPaired: Boolean = false,
    val paso: PasoOnboarding = PasoOnboarding.SERVIDOR,
    val modoSesion: ModoSesion = ModoSesion.INICIAR,
    val me: Me? = null,
    val cargandoMe: Boolean = false,
    val iniciandoSesion: Boolean = false,
    val errorMensaje: String? = null,
)

/**
 * Punto único de acceso a "emparejamiento + sesión activa" — la fusión
 * deliberada de `PairingStore` + `SessionStore` (`EdecanApp`/iOS) en un
 * solo ViewModel de Android; ver el docstring de [TokenStore] (`shared`)
 * para qué significa "emparejado" en este v1 (tener sesión iniciada, ni
 * más ni menos).
 *
 * `AndroidViewModel` (no `ViewModel` a secas) porque [DataStoreTokenStore]
 * necesita un `Context` — se usa siempre `application.applicationContext`,
 * nunca el de una `Activity` (evita fugas de memoria si la Activity se
 * recrea).
 */
class SessionViewModel(application: Application) : AndroidViewModel(application) {
    private val tokenStore: TokenStore = DataStoreTokenStore(application.applicationContext)

    private val _uiState = MutableStateFlow(SessionUiState())
    val uiState: StateFlow<SessionUiState> = _uiState.asStateFlow()

    /** `null` hasta que se conoce una URL de servidor (paso 1 del
     * onboarding, o una sesión anterior ya la había guardado).
     * `ChatScreen`/las demás pantallas lo leen desde aquí — nunca lo
     * construyen ellas mismas, mismo criterio que `SessionStore.client` en
     * iOS. */
    var api: EdecanApi? = null
        private set

    init {
        viewModelScope.launch {
            val url = tokenStore.getServerUrl()
            val emparejado = tokenStore.isPaired()
            if (url != null) {
                api = EdecanApi(url, tokenStore)
            }
            _uiState.update {
                it.copy(
                    cargandoInicial = false,
                    serverUrl = url,
                    isPaired = emparejado,
                    paso = if (url != null) PasoOnboarding.SESION else PasoOnboarding.SERVIDOR,
                )
            }
            if (emparejado) cargarMe()
        }
    }

    fun irAPasoServidor() {
        _uiState.update { it.copy(paso = PasoOnboarding.SERVIDOR, errorMensaje = null) }
    }

    fun cambiarModoSesion(modo: ModoSesion) {
        _uiState.update { it.copy(modoSesion = modo, errorMensaje = null) }
    }

    /** Paso 1 del onboarding: guarda la URL del servidor propio del tenant
     * y crea/reapunta el [EdecanApi] hacia ella. NO marca "emparejado" —
     * eso solo lo hace un [iniciarSesion]/[registrar] exitoso. */
    fun definirServidor(url: String) {
        val limpio = url.trim()
        if (limpio.isEmpty()) return
        viewModelScope.launch { tokenStore.saveServerUrl(limpio) }
        api?.actualizarBaseUrl(limpio) ?: run { api = EdecanApi(limpio, tokenStore) }
        _uiState.update { it.copy(serverUrl = limpio, paso = PasoOnboarding.SESION, errorMensaje = null) }
    }

    /** Paso 2: `POST /v1/auth/login` — éxito marca este dispositivo como
     * emparejado con el tenant (ver docstring de [TokenStore]). */
    fun iniciarSesion(email: String, password: String, totp: String) {
        val apiActual = api
        if (apiActual == null) {
            _uiState.update {
                it.copy(errorMensaje = "Primero define la URL del servidor.", paso = PasoOnboarding.SERVIDOR)
            }
            return
        }
        if (email.isBlank() || password.isBlank() || _uiState.value.iniciandoSesion) return
        viewModelScope.launch {
            _uiState.update { it.copy(iniciandoSesion = true, errorMensaje = null) }
            try {
                apiActual.login(email, password, totp.trim().ifEmpty { null })
                emparejarDispositivo(apiActual)
                _uiState.update { it.copy(iniciandoSesion = false, isPaired = true) }
                cargarMe()
            } catch (e: ApiException) {
                _uiState.update { it.copy(iniciandoSesion = false, errorMensaje = e.message) }
            }
        }
    }

    /** Paso 2, modo [ModoSesion.REGISTRAR]: `POST /v1/auth/register` — crea
     * tenant + usuario (owner) + persona por defecto y deja la sesión
     * iniciada de una, mismo criterio de emparejamiento que [iniciarSesion]. */
    fun registrar(email: String, password: String, tenantName: String) {
        val apiActual = api
        if (apiActual == null) {
            _uiState.update {
                it.copy(errorMensaje = "Primero define la URL del servidor.", paso = PasoOnboarding.SERVIDOR)
            }
            return
        }
        if (email.isBlank() || password.isBlank() || tenantName.isBlank() || _uiState.value.iniciandoSesion) return
        viewModelScope.launch {
            _uiState.update { it.copy(iniciandoSesion = true, errorMensaje = null) }
            try {
                apiActual.registrar(email, password, tenantName)
                emparejarDispositivo(apiActual)
                _uiState.update { it.copy(iniciandoSesion = false, isPaired = true) }
                cargarMe()
            } catch (e: ApiException) {
                _uiState.update { it.copy(iniciandoSesion = false, errorMensaje = e.message) }
            }
        }
    }

    /** `GET /v1/me` — se llama tras el login y cada vez que Inicio/Perfil
     * aparecen en pantalla. Silenciosa ante error de red: deja
     * `errorMensaje` para que la pantalla decida cómo mostrarlo, nunca
     * lanza (mismo criterio que `SessionStore.cargarMe()` en iOS). */
    fun cargarMe() {
        val apiActual = api ?: return
        viewModelScope.launch {
            _uiState.update { it.copy(cargandoMe = true) }
            try {
                val me = apiActual.me()
                _uiState.update { it.copy(cargandoMe = false, me = me, errorMensaje = null) }
            } catch (e: ApiException) {
                _uiState.update { it.copy(cargandoMe = false, errorMensaje = e.message) }
            }
        }
    }

    /** Cierra sesión en este dispositivo: revoca el emparejamiento en el
     * servidor (mejor esfuerzo, ver `EdecanApi.revocarDispositivo`), borra
     * los tokens y el id de dispositivo guardados, y vuelve a mostrar el
     * onboarding. Deliberadamente NO borra `serverUrl` — ver docstring de
     * [TokenStore]. */
    fun cerrarSesion() {
        val apiActual = api ?: return
        viewModelScope.launch {
            tokenStore.getDeviceId()?.let { apiActual.revocarDispositivo(it) }
            apiActual.cerrarSesion()
            tokenStore.clearDeviceId()
            _uiState.update {
                it.copy(
                    isPaired = false,
                    me = null,
                    paso = PasoOnboarding.SESION,
                    modoSesion = ModoSesion.INICIAR,
                    errorMensaje = null,
                )
            }
        }
    }

    /** `POST /v1/devices` (contrato de WP-V4-01, en paralelo — ver
     * `EdecanApi.emparejarDispositivo`, que nunca lanza) con el nombre del
     * equipo y el `ANDROID_ID` como huella — se llama después de un login o
     * registro exitosos, antes de marcar `isPaired = true`. Guarda el `id`
     * devuelto para poder revocarlo en [cerrarSesion]; si el servidor
     * todavía no expone el endpoint, sencillamente no guarda nada. */
    private suspend fun emparejarDispositivo(apiActual: EdecanApi) {
        val contexto = getApplication<Application>()
        val nombre = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifBlank { "Android" }
        val huella = try {
            Settings.Secure.getString(contexto.contentResolver, Settings.Secure.ANDROID_ID)
        } catch (e: Exception) {
            null
        }
        apiActual.emparejarDispositivo(nombre, huella)?.let { tokenStore.saveDeviceId(it) }
    }
}
