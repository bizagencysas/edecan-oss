package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.REMOTE_KIND_VIEW
import cc.edecan.shared.REMOTE_STATUS_ACTIVE
import cc.edecan.shared.REMOTE_STATUS_DENIED
import cc.edecan.shared.REMOTE_STATUS_ENDED
import cc.edecan.shared.RemoteFrame
import cc.edecan.shared.RemoteSession
import cc.edecan.shared.haTerminado
import cc.edecan.shared.remoteFramePollDelayMillis
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/** `(this as? ApiException.Servidor)?.status` — las otras variantes de
 * [ApiException] (sin conexión, sesión expirada, etc.) no traen un status
 * HTTP real, así que quedan `null` acá y se tratan como "error genérico". */
private val ApiException.statusOrNull: Int?
    get() = (this as? ApiException.Servidor)?.status

/** Una tecla especial de la barra de teclado de `RemotoScreen` — mismo
 * criterio que `LlmKind` en `PerfilViewModel.kt` (un `enum`/lista con un
 * campo `.valor` para el vocabulario real que espera el backend, en la capa
 * `vm/` en vez de `ui/`, para poder testearlo sin Compose/Android). [valor]
 * DEBE ser uno de `RemoteModels.kt::REMOTE_SPECIAL_KEYS`
 * (`RemotoTeclasTest.kt` lo fija). */
data class TeclaEspecialUi(val valor: String, val etiqueta: String, val titulo: String)

/** Mismo orden que `RemoteControlPanel.tsx` (panel web). */
val TECLAS_ESPECIALES: List<TeclaEspecialUi> = listOf(
    TeclaEspecialUi("enter", "Enter", "Enter"),
    TeclaEspecialUi("tab", "Tab", "Tab"),
    TeclaEspecialUi("escape", "Esc", "Escape"),
    TeclaEspecialUi("backspace", "⌫", "Backspace"),
    TeclaEspecialUi("arrow_up", "↑", "Flecha arriba"),
    TeclaEspecialUi("arrow_down", "↓", "Flecha abajo"),
    TeclaEspecialUi("arrow_left", "←", "Flecha izquierda"),
    TeclaEspecialUi("arrow_right", "→", "Flecha derecha"),
    TeclaEspecialUi("delete_forward", "Del", "Borrar hacia delante"),
    TeclaEspecialUi("home", "Home", "Inicio"),
    TeclaEspecialUi("end", "End", "Fin"),
    TeclaEspecialUi("page_up", "Pg↑", "Página arriba"),
    TeclaEspecialUi("page_down", "Pg↓", "Página abajo"),
    TeclaEspecialUi("space", "Espacio", "Espacio"),
)
// La lista de arriba está a mano (no derivada de `REMOTE_SPECIAL_KEYS`) para
// poder darle una etiqueta/título propios a cada una — `RemotoTeclasTest.kt`
// fija que `TECLAS_ESPECIALES.map { it.valor }` coincide EXACTO con
// `REMOTE_SPECIAL_KEYS`, así que un backend que sume/quite una tecla especial
// hace fallar ESE test en vez de dejar la barra de teclado silenciosamente
// desincronizada.

data class RemotoUiState(
    val iniciando: Boolean = false,
    val errorIniciar: String? = null,
    /** La sesión actual (recién creada o ya en curso) — no `null` implica
     * SIEMPRE que `RemotoScreen` debe pintar el indicador permanente
     * "Sesión remota activa" + el botón Terminar (guardrail no negociable,
     * ver el docstring de [RemotoViewModel]). */
    val sesionActual: RemoteSession? = null,
    val frame: RemoteFrame? = null,
    val cargandoFrame: Boolean = false,
    val errorFrame: String? = null,
    val enviandoInput: Boolean = false,
    val terminando: Boolean = false,
)

/**
 * Estado y lógica de la pestaña "Remoto" (`/v1/remote`, `ARCHITECTURE.md`
 * §13.c/§14, `docs/control-remoto.md` §7bis/§10 — WP-V6-09, espejo Android de
 * WP-V6-08 en iOS): "Nueva sesión" (Ver/Controlar) → espera la aprobación
 * LOCAL en el companion → visor con *polling* + input de
 * teclado/mouse si `kind = "control"`.
 *
 * GUARDRAIL NO NEGOCIABLE (`ARCHITECTURE.md` §0/§13.c,
 * `docs/control-remoto.md` §2): esto es control remoto TIPO TEAMVIEWER,
 * NUNCA un backdoor silencioso — cada sesión exige `consent = true` explícito
 * (checkbox de `RemotoScreen`, [iniciar] SIEMPRE lo manda fijo en `true`, no
 * es configurable desde acá) MÁS una segunda aprobación LOCAL en el companion
 * antes de que salga un solo frame o se mueva un solo píxel: esa segunda
 * aprobación es exactamente lo que dispara el PRIMER `GET .../frame` (puede
 * tardar hasta ~30s mientras alguien responde en la propia Mac — de ahí que
 * NO se reintente solo hasta que esa primera llamada resuelva).
 *
 * *Polling* automático (mismo criterio que `MisionesViewModel`/`IdeViewModel`):
 * arranca SOLO una vez la sesión ya está `active` (primera aprobación ya
 * confirmada) — nunca antes, para no espamear al companion con prompts de
 * aprobación repetidos mientras la primera sigue pendiente. Es un [Job]
 * cancelable propio de este `ViewModel`: [pausarPolling] (llamado desde
 * `DisposableEffect(Unit) { onDispose { ... } }` en `RemotoScreen` cuando la
 * pantalla deja de estar en composición) y [onCleared] SIEMPRE lo cancelan
 * — cero *polling* huérfano en segundo plano. La sesión remota en sí SIGUE
 * activa del lado del servidor mientras tanto (mismo criterio que el panel
 * web: salir de la pantalla no la termina, solo [terminar] lo hace); [cargar]
 * reanuda el *polling* si el usuario vuelve a esta pantalla mientras la
 * sesión sigue viva.
 */
class RemotoViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(RemotoUiState())
    val uiState: StateFlow<RemotoUiState> = _uiState.asStateFlow()

    private var pollingJob: Job? = null
    /** Cola FIFO para no perder toques o teclas rápidos mientras el comando
     * anterior sigue viajando al companion. */
    private val inputMutex = Mutex()

    /** Llamada desde `LaunchedEffect(api)` en `RemotoScreen`. Si ya hay una
     * sesión `active` en curso, reanuda el *polling*. */
    fun cargar(api: EdecanApi) {
        val sesion = _uiState.value.sesionActual
        if (sesion != null && sesion.status == REMOTE_STATUS_ACTIVE) {
            iniciarPolling(api, sesion.id)
        }
    }

    /** Checkbox de consentimiento ya marcado en `RemotoScreen` — crea la
     * sesión (`consent = true` SIEMPRE) y pide el primer frame de inmediato:
     * es lo que dispara la aprobación LOCAL en el companion (mismo criterio
     * que `handleStart` en el panel web). [kind] es [REMOTE_KIND_VIEW] o
     * `REMOTE_KIND_CONTROL` — `RemotoScreen` decide cuál según el checkbox
     * "control remoto" (solo visible con el flag `companion.remote_input`). */
    fun iniciar(api: EdecanApi, kind: String = REMOTE_KIND_VIEW) {
        if (_uiState.value.iniciando || _uiState.value.sesionActual != null) return
        viewModelScope.launch {
            _uiState.update { it.copy(iniciando = true, errorIniciar = null) }
            try {
                val sesion = api.createRemoteSession(consent = true, kind = kind)
                _uiState.update {
                    it.copy(iniciando = false, sesionActual = sesion, frame = null, errorFrame = null)
                }
                pedirFrame(api, sesion.id)
            } catch (e: ApiException) {
                _uiState.update { it.copy(iniciando = false, errorIniciar = e.message) }
            }
        }
    }

    /** Botón "Actualizar" (manual) Y "Reintentar" mientras se espera la
     * primera aprobación — ambos reusan [pedirFrame]. */
    fun actualizarFrame(api: EdecanApi) {
        val sesionId = _uiState.value.sesionActual?.id ?: return
        if (_uiState.value.cargandoFrame) return
        viewModelScope.launch { pedirFrame(api, sesionId) }
    }

    private suspend fun pedirFrame(api: EdecanApi, sessionId: String) {
        if (_uiState.value.sesionActual?.id != sessionId) return
        _uiState.update { it.copy(cargandoFrame = true, errorFrame = null) }
        try {
            val frame = api.getRemoteFrame(sessionId)
            if (_uiState.value.sesionActual?.id != sessionId) return // la sesión cambió mientras esta llamada estaba en vuelo.
            _uiState.update { estado ->
                estado.copy(
                    cargandoFrame = false,
                    frame = frame,
                    errorFrame = null,
                    sesionActual = estado.sesionActual?.copy(status = REMOTE_STATUS_ACTIVE, framesCount = frame.seq),
                )
            }
            iniciarPolling(api, sessionId) // recién ahora, con la primera aprobación ya confirmada.
        } catch (e: ApiException) {
            if (_uiState.value.sesionActual?.id != sessionId) return
            val status = e.statusOrNull
            if (status == 429) {
                // Silencioso: el polling pisó el rate limit, se reintenta solo
                // en el próximo tick (mismo criterio que `handleFrame` en el
                // panel web) — no es un error real que el usuario deba ver.
                _uiState.update { it.copy(cargandoFrame = false) }
                return
            }
            _uiState.update { estado ->
                val sesionActualizada = when (status) {
                    403 -> estado.sesionActual?.copy(status = REMOTE_STATUS_DENIED)
                    409 -> estado.sesionActual?.copy(status = REMOTE_STATUS_ENDED)
                    else -> estado.sesionActual
                }
                estado.copy(cargandoFrame = false, errorFrame = e.message, sesionActual = sesionActualizada)
            }
            if (status == 403 || status == 409) {
                pausarPolling()
            }
        }
    }

    private fun iniciarPolling(api: EdecanApi, sessionId: String) {
        if (pollingJob?.isActive == true) return
        pollingJob = viewModelScope.launch {
            while (isActive) {
                delay(remoteFramePollDelayMillis())
                val sesion = _uiState.value.sesionActual
                if (sesion == null || sesion.id != sessionId || sesion.haTerminado) break
                pedirFrame(api, sessionId)
            }
        }
    }

    /** Pausa el *polling* automático SIN terminar la sesión remota — ver el
     * docstring de la clase. Público a propósito: `RemotoScreen` lo llama
     * desde `DisposableEffect(Unit) { onDispose { ... } }`. */
    fun pausarPolling() {
        pollingJob?.cancel()
        pollingJob = null
    }

    /** `POST /v1/remote/sessions/{id}/end` — botón "Terminar sesión",
     * siempre visible mientras haya [RemotoUiState.sesionActual]. Aunque
     * falle el POST en el servidor, igual soltamos la vista localmente
     * (mismo criterio que `handleEnd` en el panel web): no tiene sentido
     * dejar al usuario atrapado en el visor por un error de red al cerrar. */
    fun terminar(api: EdecanApi) {
        val sesion = _uiState.value.sesionActual ?: return
        if (_uiState.value.terminando) return
        pausarPolling()
        viewModelScope.launch {
            _uiState.update { it.copy(terminando = true) }
            try {
                api.endRemoteSession(sesion.id)
            } catch (e: ApiException) {
                // Ver KDoc de la función: se ignora a propósito.
            } finally {
                _uiState.update {
                    it.copy(sesionActual = null, frame = null, terminando = false, errorFrame = null)
                }
            }
        }
    }

    // -------------------------------------------------------------------
    // Input remoto (`kind = "control"` únicamente — `RemotoScreen` no debe
    // llamar estas tres si `sesionActual?.isControl != true`, pero el
    // backend las rechaza con 403 igual si se intentara).
    // -------------------------------------------------------------------

    /** Tap/doble-tap/mantener-presionado sobre el frame -> `input_pointer`.
     * [x]/[y] YA deben venir mapeadas a coordenadas reales del frame (ver
     * `RemoteCoords.kt::mapPointToRemoteCoords`, usado por `RemotoScreen`
     * ANTES de llamar esto) — este método nunca vuelve a escalar nada. */
    fun enviarPointer(
        api: EdecanApi,
        x: Int,
        y: Int,
        accion: String,
        button: String? = null,
        startX: Int? = null,
        startY: Int? = null,
        deltaX: Int = 0,
        deltaY: Int = 0,
    ) {
        val sesionId = _uiState.value.sesionActual?.id ?: return
        viewModelScope.launch {
            inputMutex.withLock {
                if (_uiState.value.sesionActual?.id != sesionId) return@withLock
                _uiState.update { it.copy(enviandoInput = true, errorFrame = null) }
                try {
                    api.sendRemotePointerInput(
                        sesionId, x, y, accion, button, startX, startY, deltaX, deltaY
                    )
                } catch (e: ApiException) {
                    manejarErrorInput(api, sesionId, e)
                } finally {
                    _uiState.update { it.copy(enviandoInput = false) }
                }
            }
        }
    }

    /** Barra de teclado: campo de texto + Enviar -> `input_key {texto}`. */
    fun enviarTexto(api: EdecanApi, texto: String) {
        if (texto.isEmpty()) return
        val sesionId = _uiState.value.sesionActual?.id ?: return
        viewModelScope.launch {
            inputMutex.withLock {
                if (_uiState.value.sesionActual?.id != sesionId) return@withLock
                _uiState.update { it.copy(enviandoInput = true, errorFrame = null) }
                try {
                    api.sendRemoteKeyTexto(sesionId, texto)
                } catch (e: ApiException) {
                    manejarErrorInput(api, sesionId, e)
                } finally {
                    _uiState.update { it.copy(enviandoInput = false) }
                }
            }
        }
    }

    /** Barra de teclado: botón de tecla especial -> `input_key {tecla}`.
     * [tecla] debe ser una de `RemoteModels.kt::REMOTE_SPECIAL_KEYS`. */
    fun enviarTecla(api: EdecanApi, tecla: String, modifiers: List<String> = emptyList()) {
        val sesionId = _uiState.value.sesionActual?.id ?: return
        viewModelScope.launch {
            inputMutex.withLock {
                if (_uiState.value.sesionActual?.id != sesionId) return@withLock
                _uiState.update { it.copy(enviandoInput = true, errorFrame = null) }
                try {
                    api.sendRemoteKeyTecla(sesionId, tecla, modifiers)
                } catch (e: ApiException) {
                    manejarErrorInput(api, sesionId, e)
                } finally {
                    _uiState.update { it.copy(enviandoInput = false) }
                }
            }
        }
    }

    /** Un `403` (el usuario denegó ESTE comando en su companion — deniega la
     * SESIÓN completa, mismo criterio conservador que `send_input`, ver su
     * docstring) o `409` (la sesión ya no está activa) implican que el
     * estado cambió del lado del servidor: se refleja acá igual que
     * [pedirFrame]. */
    private suspend fun manejarErrorInput(api: EdecanApi, sessionId: String, e: ApiException) {
        if (_uiState.value.sesionActual?.id != sessionId) return
        _uiState.update { it.copy(errorFrame = e.message) }
        val status = e.statusOrNull
        if (status == 403 || status == 409) {
            pausarPolling()
            val nuevoEstado = if (status == 403) REMOTE_STATUS_DENIED else REMOTE_STATUS_ENDED
            _uiState.update { estado -> estado.copy(sesionActual = estado.sesionActual?.copy(status = nuevoEstado)) }
        }
    }

    /** La sesión ya quedó `ended`/`denied` del lado del servidor
     * ([pedirFrame]/[manejarErrorInput] ya lo reflejaron acá) — este método
     * solo limpia el estado LOCAL para volver a la lista ("Volver" en la
     * tarjeta de sesión terminada de `RemotoScreen`), sin ninguna llamada de
     * red nueva (a diferencia de [terminar], que sí llama `POST .../end`). */
    fun descartarSesionTerminada() {
        val sesion = _uiState.value.sesionActual ?: return
        if (!sesion.haTerminado) return
        _uiState.update { it.copy(sesionActual = null, frame = null, errorFrame = null) }
    }

    override fun onCleared() {
        pausarPolling()
    }
}
