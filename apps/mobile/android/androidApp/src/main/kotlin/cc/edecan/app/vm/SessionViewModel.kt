package cc.edecan.app.vm

import android.app.Application
import android.os.Build
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.DataStoreTokenStore
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Me
import cc.edecan.shared.TokenStore
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicLong

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
    /** Cambia para cada identidad autenticada y al invalidar/cerrar sesión.
     * App lo usa como frontera del ViewModelStore sensible. */
    val sessionGeneration: Long = 0,
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
    private val sessionRequestEpoch = AtomicLong(0)

    /** `null` hasta que se conoce una URL de servidor (paso 1 del
     * onboarding, o una sesión anterior ya la había guardado).
     * `ChatScreen`/las demás pantallas lo leen desde aquí — nunca lo
     * construyen ellas mismas, mismo criterio que `SessionStore.client` en
     * iOS. */
    var api: EdecanApi? = null
        private set

    init {
        val bootstrapEpoch = sessionRequestEpoch.get()
        viewModelScope.launch {
            val persistedUrl = tokenStore.getServerUrl()
            val url = persistedUrl?.let(::validarUrlServidor)
            val emparejado = url != null && tokenStore.isPaired()
            // Un deep link puede llegar mientras DataStore/Keystore todavía
            // se está leyendo. Nunca dejar que el bootstrap viejo pise ese
            // claim más reciente.
            if (sessionRequestEpoch.get() != bootstrapEpoch) return@launch
            if (url != null) {
                api = crearApi(url)
            }
            _uiState.update {
                it.copy(
                    cargandoInicial = false,
                    serverUrl = url,
                    isPaired = emparejado,
                    paso = if (emparejado) PasoOnboarding.SESION else PasoOnboarding.SERVIDOR,
                    errorMensaje = if (persistedUrl != null && url == null) {
                        "La conexión guardada ya no cumple la política segura. Escanea un QR nuevo o corrígela manualmente."
                    } else null,
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
        val validado = validarUrlServidor(url)
        if (validado == null) {
            _uiState.update {
                it.copy(
                    errorMensaje = "Usa HTTPS; o HTTP solo con localhost, un nombre LAN, .local o una IP privada/local, sin credenciales ni parámetros.",
                )
            }
            return
        }
        viewModelScope.launch { tokenStore.saveServerUrl(validado) }
        api?.actualizarBaseUrl(validado) ?: run { api = crearApi(validado) }
        _uiState.update { it.copy(serverUrl = validado, paso = PasoOnboarding.SESION, errorMensaje = null) }
    }

    /** Consume el deep link que abrió el escáner/cámara. El token efímero no
     * entra al estado observable ni al SavedStateHandle: se canjea y se deja
     * caer dentro de esta coroutine. */
    fun procesarEnlaceEmparejamiento(raw: String) {
        val solicitud = parsearEnlaceEmparejamiento(raw)
        if (solicitud == null) {
            _uiState.update {
                it.copy(
                    cargandoInicial = false,
                    paso = if (it.isPaired) it.paso else PasoOnboarding.SERVIDOR,
                    errorMensaje = "Ese código de emparejamiento no es válido. Genera uno nuevo en tu computador.",
                )
            }
            return
        }

        val requestEpoch = sessionRequestEpoch.incrementAndGet()
        val apiActual = api?.also { it.actualizarBaseUrl(solicitud.serverUrl) }
            ?: crearApi(solicitud.serverUrl).also { api = it }
        _uiState.update {
            it.copy(
                cargandoInicial = false,
                serverUrl = solicitud.serverUrl,
                isPaired = false,
                me = null,
                iniciandoSesion = true,
                paso = PasoOnboarding.SERVIDOR,
                errorMensaje = null,
                sessionGeneration = it.sessionGeneration + 1,
            )
        }
        viewModelScope.launch {
            try {
                tokenStore.saveServerUrl(solicitud.serverUrl)
                val (nombre, huella) = identidadDispositivo()
                apiActual.reclamarEmparejamiento(solicitud.pairingToken, nombre, huella)
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                _uiState.update {
                    it.copy(
                        iniciandoSesion = false,
                        isPaired = true,
                        me = null,
                        errorMensaje = null,
                    )
                }
                cargarMe()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                val message = if (e is ApiException.CredencialesInvalidas) {
                    "Ese código ya expiró o fue usado. Genera uno nuevo en tu computador."
                } else {
                    e.message ?: "No pude emparejar este teléfono."
                }
                _uiState.update { it.copy(iniciandoSesion = false, isPaired = false, errorMensaje = message) }
            }
        }
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
        val requestEpoch = sessionRequestEpoch.incrementAndGet()
        viewModelScope.launch {
            _uiState.update { it.copy(iniciandoSesion = true, errorMensaje = null) }
            try {
                apiActual.login(email, password, totp.trim().ifEmpty { null })
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                emparejarDispositivo(apiActual)
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                _uiState.update {
                    it.copy(
                        iniciandoSesion = false,
                        isPaired = true,
                        me = null,
                        sessionGeneration = it.sessionGeneration + 1,
                    )
                }
                cargarMe()
            } catch (e: ApiException) {
                if (sessionRequestEpoch.get() == requestEpoch) {
                    _uiState.update { it.copy(iniciandoSesion = false, errorMensaje = e.message) }
                }
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
        val requestEpoch = sessionRequestEpoch.incrementAndGet()
        viewModelScope.launch {
            _uiState.update { it.copy(iniciandoSesion = true, errorMensaje = null) }
            try {
                apiActual.registrar(email, password, tenantName)
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                emparejarDispositivo(apiActual)
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                _uiState.update {
                    it.copy(
                        iniciandoSesion = false,
                        isPaired = true,
                        me = null,
                        sessionGeneration = it.sessionGeneration + 1,
                    )
                }
                cargarMe()
            } catch (e: ApiException) {
                if (sessionRequestEpoch.get() == requestEpoch) {
                    _uiState.update { it.copy(iniciandoSesion = false, errorMensaje = e.message) }
                }
            }
        }
    }

    /** `GET /v1/me` — se llama tras el login y cada vez que Inicio/Perfil
     * aparecen en pantalla. Silenciosa ante error de red: deja
     * `errorMensaje` para que la pantalla decida cómo mostrarlo, nunca
     * lanza (mismo criterio que `SessionStore.cargarMe()` en iOS). */
    fun cargarMe() {
        val apiActual = api ?: return
        val requestEpoch = sessionRequestEpoch.get()
        viewModelScope.launch {
            _uiState.update { it.copy(cargandoMe = true) }
            try {
                val me = apiActual.me()
                if (sessionRequestEpoch.get() == requestEpoch) {
                    _uiState.update { it.copy(cargandoMe = false, me = me, errorMensaje = null) }
                }
            } catch (e: ApiException) {
                if (sessionRequestEpoch.get() != requestEpoch) return@launch
                if (e is ApiException.SesionExpirada) {
                    manejarSesionExpirada(e.message)
                } else {
                    _uiState.update { it.copy(cargandoMe = false, errorMensaje = e.message) }
                }
            }
        }
    }

    private fun crearApi(url: String): EdecanApi {
        val validado = requireNotNull(validarUrlServidor(url)) { "URL de servidor no permitida" }
        return EdecanApi(validado, tokenStore) {
            manejarSesionExpirada(ApiException.SesionExpirada().message)
        }
    }

    /** Solo una renovación rechazada invalida el emparejamiento. Los errores
     * offline conservan tokens y pantalla para poder reintentar al volver la
     * conexión. */
    private fun manejarSesionExpirada(mensaje: String?) {
        sessionRequestEpoch.incrementAndGet()
        viewModelScope.launch { tokenStore.clearDevicePairing() }
        _uiState.update {
            it.copy(
                cargandoMe = false,
                iniciandoSesion = false,
                isPaired = false,
                me = null,
                paso = PasoOnboarding.SERVIDOR,
                modoSesion = ModoSesion.INICIAR,
                errorMensaje = mensaje,
                sessionGeneration = it.sessionGeneration + 1,
            )
        }
    }

    /** Cierra sesión en este dispositivo: revoca el emparejamiento en el
     * servidor (mejor esfuerzo, ver `EdecanApi.revocarDispositivo`), borra
     * los tokens y el id de dispositivo guardados, y vuelve a mostrar el
     * onboarding. Deliberadamente NO borra `serverUrl` — ver docstring de
     * [TokenStore]. */
    fun cerrarSesion() {
        val apiActual = api ?: return
        sessionRequestEpoch.incrementAndGet()
        _uiState.update {
            it.copy(
                isPaired = false,
                me = null,
                cargandoMe = false,
                iniciandoSesion = false,
                paso = PasoOnboarding.SERVIDOR,
                modoSesion = ModoSesion.INICIAR,
                errorMensaje = null,
                sessionGeneration = it.sessionGeneration + 1,
            )
        }
        viewModelScope.launch {
            val deviceId = tokenStore.getDeviceId()
            if (deviceId != null) runCatching { apiActual.deletePushToken(deviceId) }
            tokenStore.clearDevicePairing()
            apiActual.cerrarSesion(deviceId)
        }
    }

    /** `POST /v1/devices` (contrato de WP-V4-01, en paralelo — ver
     * `EdecanApi.emparejarDispositivo`, que nunca lanza) con el nombre del
     * equipo y un id aleatorio app-scoped como huella — se llama después de un login o
     * registro exitosos, antes de marcar `isPaired = true`. Guarda el `id`
     * devuelto para poder revocarlo en [cerrarSesion]; si el servidor
     * todavía no expone el endpoint, sencillamente no guarda nada. */
    private suspend fun emparejarDispositivo(apiActual: EdecanApi) {
        val (nombre, huella) = identidadDispositivo()
        apiActual.emparejarDispositivo(nombre, huella)?.let { tokenStore.saveDeviceId(it) }
    }

    private suspend fun identidadDispositivo(): Pair<String, String?> {
        val nombre = "${Build.MANUFACTURER} ${Build.MODEL}".trim().ifBlank { "Android" }
        val huella = tokenStore.getInstallationId()
        return nombre to huella
    }
}
