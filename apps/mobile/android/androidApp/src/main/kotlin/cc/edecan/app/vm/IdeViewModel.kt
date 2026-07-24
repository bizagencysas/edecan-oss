package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.IdeGitDiff
import cc.edecan.shared.IdeGitLog
import cc.edecan.shared.IdeGitStatus
import cc.edecan.shared.IdeSession
import cc.edecan.shared.IdeSessionEvent
import cc.edecan.shared.IdeTreeNode
import cc.edecan.shared.IdeTreeOut
import cc.edecan.shared.IdeWorkspace
import cc.edecan.shared.esBinario
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

private const val POLL_INTERVAL_MILLIS = 900L

data class IdeEntrada(
    val nombre: String,
    val ruta: String,
    val esDirectorio: Boolean,
    val profundidad: Int,
)

data class IdeUiState(
    val cargando: Boolean = false,
    /** `null` = todavía no se sabe; `false` = sin companion conectado. */
    val conectado: Boolean? = null,
    val workspaces: List<IdeWorkspace> = emptyList(),
    val workspaceId: String? = null,
    val cambiandoWorkspace: Boolean = false,
    val nuevaRuta: String = "",
    val autorizandoRuta: Boolean = false,
    val entradas: List<IdeEntrada> = emptyList(),
    val truncado: Boolean = false,
    val archivoRuta: String? = null,
    val archivoContenido: String? = null,
    val archivoContenidoOriginal: String? = null,
    val archivoEsBinario: Boolean = false,
    val cargandoArchivo: Boolean = false,
    val guardandoArchivo: Boolean = false,
    val rutaActual: String = "",
    // Terminal durable
    val terminales: List<IdeSession> = emptyList(),
    val terminal: IdeSession? = null,
    val terminalEventos: List<IdeSessionEvent> = emptyList(),
    val terminalCursor: Int = 0,
    val terminalEntrada: String = "",
    val iniciandoTerminal: Boolean = false,
    // Agente durable
    val agentes: List<IdeSession> = emptyList(),
    val agente: IdeSession? = null,
    val agenteEventos: List<IdeSessionEvent> = emptyList(),
    val agenteCursor: Int = 0,
    val agentePrompt: String = "",
    val agenteProvider: String = "auto",
    val iniciandoAgente: Boolean = false,
    // Git tipado
    val gitStatus: IdeGitStatus? = null,
    val gitDiff: IdeGitDiff? = null,
    val gitLog: IdeGitLog? = null,
    val gitCargando: Boolean = false,
    val gitCommitMessage: String = "",
    val gitBranchName: String = "",
    val errorMensaje: String? = null,
)

/**
 * Estado del estudio de código de Edecán.
 *
 * Los procesos de Terminal y Agente viven en el companion, no en este
 * ViewModel. La app solo conserva un cursor y puede reconstruir la sesión
 * desde `/v1/ide/{terminals|agents}` después de minimizarse, perder la red o
 * reiniciarse. Un error transitorio de polling nunca marca la tarea como
 * fallida ni ofrece reenviarla.
 */
class IdeViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(IdeUiState())
    val uiState: StateFlow<IdeUiState> = _uiState.asStateFlow()

    private var loadJob: Job? = null
    private var fileLoadJob: Job? = null
    private var gitLoadJob: Job? = null
    private var terminalPolling: Job? = null
    private var agentPolling: Job? = null

    fun cargar(api: EdecanApi, forzar: Boolean = false) {
        if (loadJob?.isActive == true) {
            if (!forzar) return
            loadJob?.cancel()
        }
        loadJob = viewModelScope.launch {
            _uiState.update { it.copy(cargando = true, errorMensaje = null) }
            try {
                val status = api.ideStatus()
                if (!status.connected) {
                    limpiarContenidoRemoto(conectado = false)
                    return@launch
                }
                _uiState.update { it.copy(conectado = true) }
                val workspaces = api.ideWorkspaces().workspaces
                val selected = seleccionarWorkspace(workspaces, _uiState.value.workspaceId)
                if (selected == null) {
                    limpiarWorkspace()
                    _uiState.update {
                        it.copy(
                            cargando = false,
                            conectado = true,
                            workspaces = emptyList(),
                            workspaceId = null,
                        )
                    }
                    return@launch
                }
                val activeWorkspace = if (selected.active) {
                    selected
                } else {
                    api.ideActivateWorkspace(selected.id)
                }
                val workspaceChanged = activeWorkspace.id != _uiState.value.workspaceId
                if (workspaceChanged) limpiarWorkspace()
                _uiState.update {
                    it.copy(
                        conectado = true,
                        workspaces = workspaces.map { workspace ->
                            workspace.copy(active = workspace.id == activeWorkspace.id)
                        },
                        workspaceId = activeWorkspace.id,
                    )
                }
                cargarWorkspace(api, activeWorkspace.id)
                restaurarSesiones(api, activeWorkspace.id)
                cargarGit(api, activeWorkspace.id)
            } catch (error: ApiException) {
                _uiState.update {
                    it.copy(cargando = false, errorMensaje = error.message)
                }
            }
        }
    }

    fun cambiarNuevaRuta(value: String) = _uiState.update { it.copy(nuevaRuta = value) }

    fun autorizarRuta(api: EdecanApi) {
        val state = _uiState.value
        val path = state.nuevaRuta.trim()
        if (path.isEmpty() || state.autorizandoRuta || state.cambiandoWorkspace) return
        _uiState.update { it.copy(autorizandoRuta = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                val created = api.ideAddWorkspace(path)
                val workspace = api.ideActivateWorkspace(created.id)
                limpiarWorkspace()
                _uiState.update {
                    it.copy(
                        autorizandoRuta = false,
                        nuevaRuta = "",
                        workspaces = (
                            it.workspaces
                                .filterNot { old -> old.id == workspace.id }
                                .map { old -> old.copy(active = false) } +
                                workspace.copy(active = true)
                            ),
                        workspaceId = workspace.id,
                    )
                }
                cargarWorkspace(api, workspace.id)
                restaurarSesiones(api, workspace.id)
                cargarGit(api)
            } catch (error: ApiException) {
                _uiState.update {
                    it.copy(autorizandoRuta = false, errorMensaje = error.message)
                }
            }
        }
    }

    fun seleccionarWorkspace(id: String, api: EdecanApi) {
        val state = _uiState.value
        if (id == state.workspaceId || state.cambiandoWorkspace || state.autorizandoRuta) return
        _uiState.update { it.copy(cambiandoWorkspace = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                val workspace = api.ideActivateWorkspace(id)
                limpiarWorkspace()
                _uiState.update {
                    it.copy(
                        cambiandoWorkspace = false,
                        workspaceId = workspace.id,
                        workspaces = it.workspaces.map { old ->
                            old.copy(active = old.id == workspace.id)
                        },
                    )
                }
                cargarWorkspace(api, id)
                restaurarSesiones(api, id)
                cargarGit(api, id)
            } catch (error: ApiException) {
                _uiState.update {
                    it.copy(cambiandoWorkspace = false, errorMensaje = error.message)
                }
            }
        }
    }

    fun cambiarRuta(value: String) = _uiState.update { it.copy(rutaActual = value) }

    fun abrirRuta(api: EdecanApi) {
        val workspaceId = _uiState.value.workspaceId ?: return
        viewModelScope.launch { cargarWorkspace(api, workspaceId) }
    }

    fun abrirArchivo(ruta: String, api: EdecanApi) {
        val workspaceId = _uiState.value.workspaceId ?: return
        fileLoadJob?.cancel()
        _uiState.update {
            it.copy(
                archivoRuta = ruta,
                archivoContenido = null,
                archivoContenidoOriginal = null,
                archivoEsBinario = false,
                cargandoArchivo = true,
                errorMensaje = null,
            )
        }
        fileLoadJob = viewModelScope.launch {
            try {
                val archivo = api.ideWorkspaceFile(workspaceId, ruta)
                _uiState.update {
                    if (it.workspaceId != workspaceId || it.archivoRuta != ruta) return@update it
                    it.copy(
                        cargandoArchivo = false,
                        archivoContenido = archivo.content,
                        archivoContenidoOriginal = archivo.content,
                        archivoEsBinario = archivo.esBinario,
                    )
                }
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.workspaceId != workspaceId || it.archivoRuta != ruta) return@update it
                    it.copy(
                        cargandoArchivo = false,
                        archivoContenido = null,
                        archivoContenidoOriginal = null,
                        errorMensaje = error.message,
                    )
                }
            }
        }
    }

    fun cambiarContenido(value: String) = _uiState.update { it.copy(archivoContenido = value) }

    fun guardarArchivo(api: EdecanApi) {
        val state = _uiState.value
        val workspaceId = state.workspaceId ?: return
        val path = state.archivoRuta ?: return
        val content = state.archivoContenido ?: return
        if (
            state.archivoEsBinario ||
            content == state.archivoContenidoOriginal ||
            state.guardandoArchivo
        ) {
            return
        }
        _uiState.update { it.copy(guardandoArchivo = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                api.ideWorkspaceWrite(workspaceId, path, content)
                _uiState.update {
                    if (it.workspaceId != workspaceId || it.archivoRuta != path) return@update it
                    it.copy(guardandoArchivo = false, archivoContenidoOriginal = content)
                }
                cargarGit(api, workspaceId)
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.workspaceId != workspaceId || it.archivoRuta != path) return@update it
                    it.copy(guardandoArchivo = false, errorMensaje = error.message)
                }
            }
        }
    }

    fun cerrarArchivo() {
        fileLoadJob?.cancel()
        fileLoadJob = null
        _uiState.update {
            it.copy(
                archivoRuta = null,
                archivoContenido = null,
                archivoContenidoOriginal = null,
                archivoEsBinario = false,
                cargandoArchivo = false,
                guardandoArchivo = false,
            )
        }
    }

    fun descartarError() = _uiState.update { it.copy(errorMensaje = null) }

    // Terminal ------------------------------------------------------------

    fun cambiarTerminalEntrada(value: String) =
        _uiState.update { it.copy(terminalEntrada = value) }

    fun iniciarTerminal(api: EdecanApi) {
        val workspaceId = _uiState.value.workspaceId ?: return
        if (_uiState.value.iniciandoTerminal) return
        _uiState.update { it.copy(iniciandoTerminal = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                val session = api.ideStartTerminal(workspaceId, title = "Terminal")
                _uiState.update {
                    if (it.workspaceId != workspaceId) {
                        return@update it.copy(iniciandoTerminal = false)
                    }
                    it.copy(
                        iniciandoTerminal = false,
                        terminal = session,
                        terminalEventos = emptyList(),
                        terminalCursor = 0,
                        terminales = listOf(session) + it.terminales.filterNot { old -> old.id == session.id },
                    )
                }
                if (
                    _uiState.value.workspaceId == workspaceId &&
                    _uiState.value.terminal?.id == session.id
                ) {
                    iniciarPollingTerminal(api, session.id)
                }
            } catch (error: ApiException) {
                _uiState.update { current ->
                    if (current.workspaceId != workspaceId) return@update current
                    current.copy(
                        iniciandoTerminal = false,
                        errorMensaje = error.message,
                    )
                }
            }
        }
    }

    fun seleccionarTerminal(id: String, api: EdecanApi) {
        val session = _uiState.value.terminales.firstOrNull { it.id == id } ?: return
        if (session.id == _uiState.value.terminal?.id) return
        terminalPolling?.cancel()
        _uiState.update {
            it.copy(
                terminal = session,
                terminalEventos = emptyList(),
                terminalCursor = 0,
                errorMensaje = null,
            )
        }
        iniciarPollingTerminal(api, session.id)
    }

    fun enviarTerminal(api: EdecanApi) {
        val session = _uiState.value.terminal ?: return
        val input = _uiState.value.terminalEntrada
        if (input.isBlank() || !session.activa) return
        _uiState.update { it.copy(terminalEntrada = "", errorMensaje = null) }
        viewModelScope.launch {
            try {
                api.ideTerminalInput(session.id, input + "\n")
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.terminal?.id != session.id) return@update it
                    it.copy(
                        terminalEntrada = it.terminalEntrada.ifEmpty { input },
                        errorMensaje = error.message,
                    )
                }
                return@launch
            }
            try {
                leerTerminal(api, session.id)
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.terminal?.id != session.id) return@update it
                    it.copy(errorMensaje = error.message)
                }
            }
        }
    }

    fun cerrarTerminal(api: EdecanApi) {
        val sessionId = _uiState.value.terminal?.id ?: return
        viewModelScope.launch {
            try {
                val closed = api.ideCloseTerminal(sessionId)
                actualizarTerminal(closed)
            } catch (error: ApiException) {
                _uiState.update { it.copy(errorMensaje = error.message) }
            }
        }
    }

    // Agente --------------------------------------------------------------

    fun cambiarAgentePrompt(value: String) =
        _uiState.update { it.copy(agentePrompt = value) }

    fun cambiarAgenteProvider(value: String) =
        _uiState.update { it.copy(agenteProvider = value) }

    fun iniciarAgente(api: EdecanApi) {
        val state = _uiState.value
        val workspaceId = state.workspaceId ?: return
        val prompt = state.agentePrompt.trim()
        if (prompt.isEmpty() || state.iniciandoAgente) return
        _uiState.update { it.copy(iniciandoAgente = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                val session = api.ideStartAgent(
                    workspaceId = workspaceId,
                    prompt = prompt,
                    provider = state.agenteProvider,
                    title = tituloCorto(prompt),
                )
                _uiState.update {
                    if (it.workspaceId != workspaceId) {
                        return@update it.copy(iniciandoAgente = false)
                    }
                    it.copy(
                        iniciandoAgente = false,
                        agentePrompt = "",
                        agente = session,
                        agenteEventos = emptyList(),
                        agenteCursor = 0,
                        agentes = listOf(session) + it.agentes.filterNot { old -> old.id == session.id },
                    )
                }
                if (
                    _uiState.value.workspaceId == workspaceId &&
                    _uiState.value.agente?.id == session.id
                ) {
                    iniciarPollingAgente(api, session.id)
                }
            } catch (error: ApiException) {
                _uiState.update { current ->
                    if (current.workspaceId != workspaceId) return@update current
                    current.copy(
                        iniciandoAgente = false,
                        errorMensaje = error.message,
                    )
                }
            }
        }
    }

    fun seleccionarAgente(id: String, api: EdecanApi) {
        val session = _uiState.value.agentes.firstOrNull { it.id == id } ?: return
        if (session.id == _uiState.value.agente?.id) return
        agentPolling?.cancel()
        _uiState.update {
            it.copy(
                agente = session,
                agenteEventos = emptyList(),
                agenteCursor = 0,
                errorMensaje = null,
            )
        }
        iniciarPollingAgente(api, session.id)
    }

    fun cancelarAgente(api: EdecanApi) {
        val sessionId = _uiState.value.agente?.id ?: return
        viewModelScope.launch {
            try {
                val cancelled = api.ideCancelAgent(sessionId)
                actualizarAgente(cancelled)
            } catch (error: ApiException) {
                _uiState.update { it.copy(errorMensaje = error.message) }
            }
        }
    }

    // Git -----------------------------------------------------------------

    fun cambiarCommitMessage(value: String) =
        _uiState.update { it.copy(gitCommitMessage = value) }

    fun cambiarBranchName(value: String) =
        _uiState.update { it.copy(gitBranchName = value) }

    fun cargarGit(api: EdecanApi) {
        val workspaceId = _uiState.value.workspaceId ?: return
        cargarGit(api, workspaceId)
    }

    private fun cargarGit(api: EdecanApi, workspaceId: String) {
        if (_uiState.value.workspaceId != workspaceId) return
        gitLoadJob?.cancel()
        _uiState.update { it.copy(gitCargando = true) }
        gitLoadJob = viewModelScope.launch {
            try {
                val status = api.ideGitStatus(workspaceId)
                val diff = api.ideGitDiff(workspaceId)
                val log = api.ideGitLog(workspaceId)
                _uiState.update {
                    if (it.workspaceId != workspaceId) return@update it
                    it.copy(
                        gitCargando = false,
                        gitStatus = status,
                        gitDiff = diff,
                        gitLog = log,
                    )
                }
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.workspaceId != workspaceId) return@update it
                    it.copy(gitCargando = false, errorMensaje = error.message)
                }
            }
        }
    }

    fun stage(path: String, api: EdecanApi) = gitMutation(api) {
        workspaceId -> api.ideGitStage(workspaceId, listOf(path))
    }

    fun unstage(path: String, api: EdecanApi) = gitMutation(api) {
        workspaceId -> api.ideGitUnstage(workspaceId, listOf(path))
    }

    fun commit(api: EdecanApi) {
        val message = _uiState.value.gitCommitMessage.trim()
        if (message.isEmpty()) return
        gitMutation(api) { workspaceId ->
            api.ideGitCommit(workspaceId, message)
            _uiState.update {
                if (it.workspaceId != workspaceId) return@update it
                it.copy(gitCommitMessage = "")
            }
        }
    }

    fun crearRama(api: EdecanApi) {
        val name = _uiState.value.gitBranchName.trim()
        if (name.isEmpty()) return
        gitMutation(api) { workspaceId ->
            api.ideGitCreateBranch(workspaceId, name, checkout = true)
            _uiState.update {
                if (it.workspaceId != workspaceId) return@update it
                it.copy(gitBranchName = "")
            }
        }
    }

    fun push(api: EdecanApi) = gitMutation(api) { workspaceId ->
        api.ideGitPush(workspaceId, setUpstream = true)
    }

    /** Detiene solo las lecturas móviles. Terminales y agentes continúan
     * ejecutándose en el companion y se rehidratan al volver a la pantalla. */
    fun pausar() {
        detenerPollers()
    }

    override fun onCleared() {
        detenerPollers()
        super.onCleared()
    }

    // Internos ------------------------------------------------------------

    private suspend fun cargarWorkspace(api: EdecanApi, workspaceId: String) {
        if (_uiState.value.workspaceId != workspaceId) return
        _uiState.update { it.copy(cargando = true, errorMensaje = null) }
        try {
            val path = _uiState.value.rutaActual.trim().ifBlank { null }
            val tree = api.ideWorkspaceTree(workspaceId, path)
            _uiState.update {
                if (it.workspaceId != workspaceId) return@update it
                it.copy(
                    cargando = false,
                    entradas = aplanar(tree),
                    rutaActual = tree.path.takeUnless { value -> value == "." }.orEmpty(),
                    truncado = tree.truncated,
                )
            }
        } catch (error: ApiException) {
            _uiState.update {
                if (it.workspaceId != workspaceId) return@update it
                it.copy(cargando = false, errorMensaje = error.message)
            }
        }
    }

    private suspend fun restaurarSesiones(api: EdecanApi, workspaceId: String) {
        try {
            val terminals = api.ideTerminalSessions(workspaceId).sessions
            val agents = api.ideAgentSessions(workspaceId).sessions
            if (_uiState.value.workspaceId != workspaceId) return
            val previous = _uiState.value
            val terminal = terminals.firstOrNull { it.id == previous.terminal?.id }
                ?: terminals.firstOrNull { it.activa }
                ?: terminals.firstOrNull()
            val agent = agents.firstOrNull { it.id == previous.agente?.id }
                ?: agents.firstOrNull { it.activa }
                ?: agents.firstOrNull()
            val sameTerminal = terminal?.id == previous.terminal?.id
            val sameAgent = agent?.id == previous.agente?.id
            _uiState.update { current ->
                if (current.workspaceId != workspaceId) return@update current
                current.copy(
                    terminales = terminals,
                    terminal = terminal,
                    terminalEventos = if (sameTerminal) {
                        current.terminalEventos
                    } else {
                        emptyList()
                    },
                    terminalCursor = if (sameTerminal) current.terminalCursor else 0,
                    agentes = agents,
                    agente = agent,
                    agenteEventos = if (sameAgent) current.agenteEventos else emptyList(),
                    agenteCursor = if (sameAgent) current.agenteCursor else 0,
                )
            }
            terminal?.let { iniciarPollingTerminal(api, it.id) }
            agent?.let { iniciarPollingAgente(api, it.id) }
        } catch (error: ApiException) {
            _uiState.update {
                if (it.workspaceId != workspaceId) return@update it
                it.copy(errorMensaje = error.message)
            }
        }
    }

    private fun iniciarPollingTerminal(api: EdecanApi, sessionId: String) {
        terminalPolling?.cancel()
        terminalPolling = viewModelScope.launch {
            while (isActive && _uiState.value.terminal?.id == sessionId) {
                try {
                    leerTerminal(api, sessionId)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    // La sesión sigue viva en la computadora. El siguiente
                    // tick reanuda desde el mismo cursor sin mostrar un falso
                    // fallo ni pedir reenviar el comando.
                }
                if (
                    _uiState.value.terminal?.id != sessionId ||
                    _uiState.value.terminal?.activa != true
                ) {
                    break
                }
                delay(POLL_INTERVAL_MILLIS)
            }
        }
    }

    private suspend fun leerTerminal(api: EdecanApi, sessionId: String) {
        if (_uiState.value.terminal?.id != sessionId) return
        val cursor = _uiState.value.terminalCursor
        val out = api.ideReadTerminal(sessionId, cursor)
        _uiState.update {
            if (it.terminal?.id != sessionId) return@update it
            it.copy(
                terminal = out.session,
                terminales = reemplazarSesion(it.terminales, out.session),
                terminalEventos = anexarEventos(it.terminalEventos, out.events),
                terminalCursor = maxOf(it.terminalCursor, out.nextCursor),
            )
        }
    }

    private fun iniciarPollingAgente(api: EdecanApi, sessionId: String) {
        agentPolling?.cancel()
        agentPolling = viewModelScope.launch {
            while (isActive && _uiState.value.agente?.id == sessionId) {
                try {
                    leerAgente(api, sessionId)
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    // Igual que terminal: la desconexión móvil no cancela el
                    // proceso local. Se recupera por cursor automáticamente.
                }
                if (
                    _uiState.value.agente?.id != sessionId ||
                    _uiState.value.agente?.activa != true
                ) {
                    break
                }
                delay(POLL_INTERVAL_MILLIS)
            }
        }
    }

    private suspend fun leerAgente(api: EdecanApi, sessionId: String) {
        if (_uiState.value.agente?.id != sessionId) return
        val cursor = _uiState.value.agenteCursor
        val out = api.ideReadAgent(sessionId, cursor)
        _uiState.update {
            if (it.agente?.id != sessionId) return@update it
            it.copy(
                agente = out.session,
                agentes = reemplazarSesion(it.agentes, out.session),
                agenteEventos = anexarEventos(it.agenteEventos, out.events),
                agenteCursor = maxOf(it.agenteCursor, out.nextCursor),
            )
        }
    }

    private fun gitMutation(api: EdecanApi, block: suspend (String) -> Unit) {
        val workspaceId = _uiState.value.workspaceId ?: return
        if (_uiState.value.gitCargando) return
        _uiState.update { it.copy(gitCargando = true, errorMensaje = null) }
        viewModelScope.launch {
            try {
                block(workspaceId)
                cargarGit(api, workspaceId)
            } catch (error: ApiException) {
                _uiState.update {
                    if (it.workspaceId != workspaceId) return@update it
                    it.copy(gitCargando = false, errorMensaje = error.message)
                }
            }
        }
    }

    private fun actualizarTerminal(session: IdeSession) {
        _uiState.update {
            if (it.terminal?.id != session.id) return@update it
            it.copy(
                terminal = session,
                terminales = reemplazarSesion(it.terminales, session),
            )
        }
    }

    private fun actualizarAgente(session: IdeSession) {
        _uiState.update {
            if (it.agente?.id != session.id) return@update it
            it.copy(
                agente = session,
                agentes = reemplazarSesion(it.agentes, session),
            )
        }
    }

    private fun reemplazarSesion(
        sessions: List<IdeSession>,
        updated: IdeSession,
    ): List<IdeSession> =
        if (sessions.any { it.id == updated.id }) {
            sessions.map { if (it.id == updated.id) updated else it }
        } else {
            listOf(updated) + sessions
        }

    private fun limpiarContenidoRemoto(conectado: Boolean) {
        limpiarWorkspace()
        _uiState.update {
            it.copy(
                cargando = false,
                conectado = conectado,
                workspaces = emptyList(),
                workspaceId = null,
            )
        }
    }

    private fun limpiarWorkspace() {
        detenerPollers()
        fileLoadJob?.cancel()
        fileLoadJob = null
        gitLoadJob?.cancel()
        gitLoadJob = null
        _uiState.update {
            it.copy(
                cargando = false,
                cambiandoWorkspace = false,
                entradas = emptyList(),
                truncado = false,
                archivoRuta = null,
                archivoContenido = null,
                archivoContenidoOriginal = null,
                archivoEsBinario = false,
                cargandoArchivo = false,
                guardandoArchivo = false,
                rutaActual = "",
                terminales = emptyList(),
                terminal = null,
                terminalEventos = emptyList(),
                terminalCursor = 0,
                terminalEntrada = "",
                iniciandoTerminal = false,
                agentes = emptyList(),
                agente = null,
                agenteEventos = emptyList(),
                agenteCursor = 0,
                agentePrompt = "",
                iniciandoAgente = false,
                gitStatus = null,
                gitDiff = null,
                gitLog = null,
                gitCargando = false,
                gitCommitMessage = "",
                gitBranchName = "",
            )
        }
    }

    private fun detenerPollers() {
        terminalPolling?.cancel()
        agentPolling?.cancel()
        terminalPolling = null
        agentPolling = null
    }

    private fun seleccionarWorkspace(
        workspaces: List<IdeWorkspace>,
        preferred: String?,
    ): IdeWorkspace? =
        workspaces.firstOrNull { it.id == preferred }
            ?: workspaces.firstOrNull { it.active }
            ?: workspaces.firstOrNull()

    private fun anexarEventos(
        actuales: List<IdeSessionEvent>,
        nuevos: List<IdeSessionEvent>,
    ): List<IdeSessionEvent> {
        if (nuevos.isEmpty()) return actuales
        val cursorExistentes = actuales.asSequence().map { it.cursor }.toHashSet()
        return (actuales + nuevos.filterNot { it.cursor in cursorExistentes })
            .sortedBy { it.cursor }
    }

    private fun tituloCorto(prompt: String): String =
        prompt.lineSequence().firstOrNull().orEmpty().trim().take(72)

    private fun aplanar(tree: IdeTreeOut): List<IdeEntrada> {
        val result = mutableListOf<IdeEntrada>()
        fun walk(nodes: List<IdeTreeNode>, prefix: String, depth: Int) {
            for (node in nodes) {
                val fallbackPath = if (prefix.isBlank() || prefix == ".") {
                    node.name
                } else {
                    "$prefix/${node.name}"
                }
                val path = node.path?.takeIf { it.isNotBlank() } ?: fallbackPath
                result += IdeEntrada(node.name, path, node.isDir, depth)
                node.children?.let { walk(it, path, depth + 1) }
            }
        }
        walk(tree.entries, tree.path, 0)
        return result
    }
}
