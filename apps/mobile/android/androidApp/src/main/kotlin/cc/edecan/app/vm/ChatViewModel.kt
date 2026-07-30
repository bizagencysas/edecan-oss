package cc.edecan.app.vm

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.ChatBlock
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.ChatMessageIn
import cc.edecan.shared.ChatSecretRedaction
import cc.edecan.shared.ConfirmIn
import cc.edecan.shared.Conversation
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Message
import cc.edecan.shared.MissionDetail
import cc.edecan.shared.SseClient
import cc.edecan.shared.UploadContent
import cc.edecan.shared.UploadedFile
import cc.edecan.shared.adjuntos
import cc.edecan.shared.edecanJson
import cc.edecan.shared.texto
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.io.buffered
import kotlinx.io.files.Path
import kotlinx.io.files.SystemFileSystem
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.decodeFromJsonElement
import java.io.File
import java.util.UUID

enum class EstadoEntrega { ENVIANDO, ENTREGADO, FALLIDO }
enum class EstadoAdjunto { SUBIENDO, LISTO, ERROR }

data class AdjuntoComposerUi(
    val localId: String,
    val filename: String,
    val mime: String,
    val sizeBytes: Long,
    val previewBytes: ByteArray? = null,
    val estado: EstadoAdjunto = EstadoAdjunto.SUBIENDO,
    val archivo: UploadedFile? = null,
    val error: String? = null,
)

/** Archivo privado preparado en `cacheDir` y reabrible para el multipart.
 * Mantener una ruta corta evita retener dos copias de hasta 25 MB en heap. */
internal data class ArchivoSubidaLocal(
    val file: File,
    val filename: String,
    val mime: String,
    val previewBytes: ByteArray? = null,
) {
    val sizeBytes: Long get() = file.length()

    fun contenido(): UploadContent = UploadContent(sizeBytes) {
        SystemFileSystem.source(Path(file.absolutePath)).buffered()
    }

    fun eliminar() {
        if (file.exists()) file.delete()
    }
}

internal fun interface AdjuntoUploader {
    suspend fun subir(api: EdecanApi, local: ArchivoSubidaLocal): UploadedFile
}

/** Conserva la UUID únicamente mientras un intento lógico puede necesitar
 * `Reintentar`. Un mensaje nuevo o un éxito cierran ese intento. */
internal class ClavesIntentoLogico(
    private val generar: () -> String = { UUID.randomUUID().toString() },
) {
    private val porMensaje = mutableMapOf<String, String>()

    fun nueva(idMensaje: String): String {
        porMensaje.clear()
        return generar().also { porMensaje[idMensaje] = it }
    }

    fun reintentar(idMensaje: String): String = porMensaje.getOrPut(idMensaje, generar)
    fun completar(idMensaje: String) { porMensaje.remove(idMensaje) }
    fun limpiar() { porMensaje.clear() }
}

/** Un mensaje en pantalla. [id] es estable para `LazyColumn`/`key`. */
data class MensajeUi(
    val id: String,
    val rol: Rol,
    val texto: String = "",
    val enProgreso: Boolean = false,
    val artefactos: List<ArtifactRef> = emptyList(),
    val bloques: List<ChatBlock> = emptyList(),
    val adjuntos: List<ArtifactRef> = emptyList(),
    val trabajo: TrabajoUi? = null,
    val estadoEntrega: EstadoEntrega? = null,
) {
    enum class Rol { USUARIO, ASISTENTE }
}

data class PasoTrabajoUi(
    val id: String,
    val nombre: String,
    val estado: Estado = Estado.EJECUTANDO,
    val detalle: String? = null,
    val segundos: Int = 0,
) {
    enum class Estado { EJECUTANDO, COMPLETADO, ERROR }
}

data class TrabajoUi(
    val activo: Boolean = true,
    val segundos: Int = 0,
    val pasos: List<PasoTrabajoUi> = emptyList(),
    val missionId: String? = null,
    val missionStatus: String? = null,
    val missionError: String? = null,
)

internal val TrabajoUi.tituloEstado: String
    get() = when (missionStatus) {
        "waiting_confirmation" -> "Necesita tu aprobación"
        "error" -> "El trabajo encontró un error"
        "cancelled" -> "Trabajo cancelado"
        "done" -> "Trabajo completado"
        else -> if (activo) "Edecán está trabajando" else "Trabajo completado"
    }

private fun TrabajoUi?.vincularMision(missionId: String): TrabajoUi =
    (this ?: TrabajoUi()).copy(activo = true, missionId = missionId, missionStatus = "planning")

private fun TrabajoUi?.actualizarMision(detail: MissionDetail): TrabajoUi {
    val actual = this ?: TrabajoUi()
    val pasos = actual.pasos.toMutableList()
    detail.steps.forEach { step ->
        val id = "mission:${step.seq}"
        val estado = when (step.status) {
            "done", "skipped" -> PasoTrabajoUi.Estado.COMPLETADO
            "error" -> PasoTrabajoUi.Estado.ERROR
            else -> PasoTrabajoUi.Estado.EJECUTANDO
        }
        val item = PasoTrabajoUi(
            id = id,
            nombre = step.instruccion,
            estado = estado,
            detalle = step.resultado,
        )
        val index = pasos.indexOfFirst { it.id == id }
        if (index >= 0) pasos[index] = item else pasos += item
    }
    return actual.copy(
        activo = detail.mission.status == "planning" || detail.mission.status == "running",
        pasos = pasos,
        missionId = detail.mission.id,
        missionStatus = detail.mission.status,
        missionError = detail.mission.error,
    )
}

private fun TrabajoUi?.iniciarPaso(toolCallId: String?, nombre: String): TrabajoUi {
    val actual = this ?: TrabajoUi()
    val id = toolCallId ?: "$nombre-${actual.pasos.size}"
    if (actual.pasos.any { it.id == id }) return actual.copy(activo = true)
    return actual.copy(
        activo = true,
        pasos = actual.pasos + PasoTrabajoUi(id = id, nombre = nombre),
    )
}

private fun TrabajoUi?.actualizarPaso(
    toolCallId: String?,
    nombre: String,
    segundos: Int,
    detalle: String,
): TrabajoUi {
    val iniciado = iniciarPaso(toolCallId, nombre)
    val indice = iniciado.pasos.indexOfLast {
        (toolCallId != null && it.id == toolCallId) ||
            (toolCallId == null && it.nombre == nombre && it.estado == PasoTrabajoUi.Estado.EJECUTANDO)
    }
    if (indice < 0) return iniciado
    val pasos = iniciado.pasos.toMutableList()
    pasos[indice] = pasos[indice].copy(
        segundos = maxOf(pasos[indice].segundos, segundos),
        detalle = detalle,
    )
    return iniciado.copy(activo = true, segundos = maxOf(iniciado.segundos, segundos), pasos = pasos)
}

private fun TrabajoUi?.completarPaso(
    toolCallId: String?,
    nombre: String,
    resultado: String,
): TrabajoUi {
    val actual = iniciarPaso(toolCallId, nombre)
    val indice = actual.pasos.indexOfLast {
        (toolCallId != null && it.id == toolCallId) ||
            (toolCallId == null && it.nombre == nombre && it.estado == PasoTrabajoUi.Estado.EJECUTANDO)
    }
    if (indice < 0) return actual
    val pasos = actual.pasos.toMutableList()
    val fallo = resultado.trim().startsWith("error:", ignoreCase = true)
    pasos[indice] = pasos[indice].copy(
        estado = if (fallo) PasoTrabajoUi.Estado.ERROR else PasoTrabajoUi.Estado.COMPLETADO,
        detalle = resultado.trim().ifBlank { null },
    )
    return actual.copy(pasos = pasos)
}

data class ConfirmacionPendiente(val toolCallId: String, val nombre: String, val argumentos: String)

internal fun confirmacionPersistida(conversation: Conversation): ConfirmacionPendiente? =
    conversation.pendingConfirmation?.let { pending ->
        ConfirmacionPendiente(
            toolCallId = pending.toolCallId,
            nombre = pending.name,
            argumentos = formatearArgumentos(pending.args),
        )
    }

internal fun formatearArgumentos(args: JsonElement): String {
    val objeto = args as? JsonObject ?: return ""
    return objeto.entries.joinToString(" · ") { (clave, valor) ->
        val texto = (valor as? JsonPrimitive)?.contentOrNull ?: valor.toString()
        "${clave.replace('_', ' ')}: $texto"
    }
}

data class ChatUiState(
    val mensajes: List<MensajeUi> = emptyList(),
    val conversaciones: List<Conversation> = emptyList(),
    val conversationId: String? = null,
    val tituloConversacion: String? = null,
    val cargandoHistorial: Boolean = false,
    val historialInicializado: Boolean = false,
    val borrador: String = "",
    val adjuntosComposer: List<AdjuntoComposerUi> = emptyList(),
    val herramientaActiva: String? = null,
    val herramientaActivaCallId: String? = null,
    val herramientaSegundos: Int = 0,
    val herramientaMensaje: String? = null,
    val confirmacionPendiente: ConfirmacionPendiente? = null,
    val enviando: Boolean = false,
    /** El servidor conserva el turno aunque Android pierda el socket. */
    val recuperandoTurno: Boolean = false,
    val errorMensaje: String? = null,
)

/** Identidad mínima de un turno que el servidor ya puede estar procesando.
 * Deliberadamente no guarda texto, adjuntos ni credenciales en el Bundle. */
internal data class TurnoPendientePersistido(
    val conversationId: String,
    val idempotencyKey: String,
)

private const val PENDING_CONVERSATION_KEY = "chat_pending_conversation_id"
private const val PENDING_IDEMPOTENCY_KEY = "chat_pending_idempotency_key"

internal fun SavedStateHandle.turnoPendientePersistido(): TurnoPendientePersistido? {
    val conversationId = get<String>(PENDING_CONVERSATION_KEY)?.takeIf { it.isNotBlank() }
    val key = get<String>(PENDING_IDEMPOTENCY_KEY)?.takeIf { it.isNotBlank() }
    return if (conversationId != null && key != null) TurnoPendientePersistido(conversationId, key) else null
}

/** Un `error` del agente es un cierre válido del protocolo SSE, no una caída
 * del transporte. Se extrae sin lanzar dentro del collector porque cualquier
 * excepción de ese collector atraviesa `emit` y la capa Ktor podría
 * normalizarla erróneamente como `SseException.Conexion`. */
internal fun ChatEvent.mensajeDeFalloTerminal(): String? =
    (this as? ChatEvent.ErrorEvent)?.message
        ?.trim()
        ?.ifEmpty { "Edecán no pudo completar este mensaje." }

/** Quita cualquier fragmento incompleto antes de consumir el replay completo.
 * Sin este reset, los deltas que llegaron antes de minimizar se duplicarían. */
internal fun ChatUiState.prepararReanudacion(idRespuesta: String): ChatUiState = copy(
    recuperandoTurno = true,
    errorMensaje = null,
    herramientaActiva = null,
    herramientaActivaCallId = null,
    herramientaSegundos = 0,
    herramientaMensaje = null,
    mensajes = mensajes.map { mensaje ->
        if (mensaje.id == idRespuesta) {
            mensaje.copy(
                texto = "",
                enProgreso = true,
                artefactos = emptyList(),
                bloques = emptyList(),
                trabajo = null,
            )
        } else {
            mensaje
        }
    },
)

/** Aplica la lista canónica del servidor sin tocar el turno que acaba de
 * renderizarse. El backend asigna el título después del primer mensaje, por
 * eso Android debe refrescar esta metadata al terminar el stream igual que
 * web e iOS. */
internal fun ChatUiState.conConversacionesActualizadas(
    actualizadas: List<Conversation>,
): ChatUiState {
    val title = actualizadas.firstOrNull { it.id == conversationId }?.title
    return copy(
        conversaciones = actualizadas,
        tituloConversacion = title ?: tituloConversacion,
    )
}

internal fun aplicarFinDeHerramienta(
    estado: ChatUiState,
    idRespuesta: String,
    nuevos: List<ArtifactRef>,
    bloquesNuevos: List<ChatBlock> = emptyList(),
    toolCallId: String? = null,
    nombre: String = "herramienta",
    resultado: String = "",
    missionId: String? = null,
): ChatUiState = estado.copy(
    herramientaActiva = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) null else estado.herramientaActiva,
    herramientaActivaCallId = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) null else estado.herramientaActivaCallId,
    herramientaSegundos = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) 0 else estado.herramientaSegundos,
    herramientaMensaje = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) null else estado.herramientaMensaje,
    mensajes = estado.mensajes.map { mensaje ->
        if (mensaje.id != idRespuesta) return@map mensaje
        val ids = mensaje.artefactos.mapTo(mutableSetOf()) { it.fileId }
        val bloques = mensaje.bloques.toMutableList()
        bloquesNuevos.forEach { bloque -> if (bloque !in bloques) bloques += bloque }
        mensaje.copy(
            artefactos = mensaje.artefactos + nuevos.filter { ids.add(it.fileId) },
            bloques = bloques,
            trabajo = mensaje.trabajo.completarPaso(toolCallId, nombre, resultado).let { trabajo ->
                missionId?.let(trabajo::vincularMision) ?: trabajo
            },
        )
    },
)

/** Reconstruye texto, adjuntos y rich blocks desde el contrato persistido de
 * `GET /v1/conversations/{id}`. Los `tool_end` viven en `tool_calls`. */
internal fun mensajesPersistidos(conversation: Conversation): List<MensajeUi> =
    conversation.messages.mapNotNull { message ->
        when (message.role.lowercase()) {
            "user" -> MensajeUi(
                id = message.id,
                rol = MensajeUi.Rol.USUARIO,
                texto = message.texto,
                adjuntos = message.adjuntos.map { ArtifactRef(it.fileId, it.filename, it.mime) },
                estadoEntrega = EstadoEntrega.ENTREGADO,
            )
            "assistant" -> {
                val eventos = message.toolEventsPersistidos()
                val ends = eventos.filterIsInstance<ChatEvent.ToolEnd>()
                val trabajo = eventos.fold<ChatEvent, TrabajoUi?>(null) { actual, evento ->
                    when (evento) {
                        is ChatEvent.ToolStart -> actual.iniciarPaso(evento.toolCallId, evento.name)
                        is ChatEvent.ToolEnd -> actual.completarPaso(
                            evento.toolCallId, evento.name, evento.resultPreview,
                        ).let { trabajo ->
                            evento.missionId?.let(trabajo::vincularMision) ?: trabajo
                        }
                        else -> actual
                    }
                }?.let { work ->
                    if (work.missionId == null) work.copy(activo = false) else work
                }
                MensajeUi(
                    id = message.id,
                    rol = MensajeUi.Rol.ASISTENTE,
                    texto = message.texto,
                    artefactos = ends.flatMap { it.artifacts }.distinctBy { it.fileId },
                    bloques = ends.filter { it.blocksVersion == 1 }.flatMap { it.blocks }.distinct(),
                    trabajo = trabajo,
                )
            }
            else -> null
        }
    }

private fun Message.toolEventsPersistidos(): List<ChatEvent> =
    (toolCalls as? JsonArray).orEmpty().mapNotNull { raw ->
        runCatching { edecanJson.decodeFromJsonElement<ChatEvent>(raw) }.getOrNull()
    }

class ChatViewModel(private val savedStateHandle: SavedStateHandle) : ViewModel() {
    private val _uiState = MutableStateFlow(
        ChatUiState(borrador = savedStateHandle[DRAFT_KEY] ?: ""),
    )
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val sseClient = SseClient()
    private var siguienteId = 0L
    private var cargaVersion = 0L
    private val archivosAdjuntos = mutableMapOf<String, ArchivoSubidaLocal>()
    private val tareasSubida = mutableMapOf<String, Job>()
    private val seguimientoMisiones = mutableMapOf<String, Job>()
    private val clavesIdempotencia = ClavesIntentoLogico()
    private var turnoActivo: Job? = null
    private var despertarReconexion: CompletableDeferred<Unit>? = null
    /** Texto crudo únicamente en memoria durante un intento reintentable. */
    private val textosPrivadosPorMensaje = mutableMapOf<String, String>()
    private var adjuntoUploader = AdjuntoUploader { api, local ->
        api.uploadFile(local.contenido(), local.filename, local.mime)
    }

    internal constructor(savedStateHandle: SavedStateHandle, uploader: AdjuntoUploader) : this(savedStateHandle) {
        adjuntoUploader = uploader
    }

    private fun idNuevo(prefijo: String = "msg"): String = "$prefijo-${siguienteId++}"

    fun actualizarBorrador(texto: String) {
        savedStateHandle[DRAFT_KEY] = texto
        _uiState.update { it.copy(borrador = texto) }
    }

    /** Carga la lista y abre la conversación más reciente. No crea una vacía
     * solo por entrar al chat. */
    fun cargar(api: EdecanApi) {
        if (_uiState.value.historialInicializado || _uiState.value.cargandoHistorial) return
        viewModelScope.launch { cargarListaInicial(api) }
    }

    /** Llamado por la pantalla al volver al primer plano. Despierta un poll
     * que Android pudo haber ralentizado y, tras recrear el proceso, recupera
     * el intento usando solo la UUID persistida. */
    fun reanudarAlVolver(api: EdecanApi) {
        despertarReconexion?.complete(Unit)
        val pendiente = savedStateHandle.turnoPendientePersistido() ?: return
        if (turnoActivo?.isActive == true) return
        reanudarTurnoPersistido(pendiente, api)
    }

    private suspend fun cargarListaInicial(api: EdecanApi) {
        val version = ++cargaVersion
        _uiState.update { it.copy(cargandoHistorial = true, errorMensaje = null) }
        try {
            val conversaciones = api.conversations()
            val turnoPendiente = savedStateHandle.turnoPendientePersistido()
            val actual = _uiState.value.conversationId
            val destino = conversaciones.firstOrNull { it.id == turnoPendiente?.conversationId }
                ?: conversaciones.firstOrNull { it.id == actual }
                ?: conversaciones.firstOrNull()
            val completa = destino?.let { api.conversation(it.id) }
            if (version != cargaVersion) return
            _uiState.update {
                it.copy(
                    conversaciones = conversaciones,
                    conversationId = completa?.id,
                    tituloConversacion = completa?.title,
                    mensajes = completa?.let(::mensajesPersistidos).orEmpty(),
                    confirmacionPendiente = completa?.let(::confirmacionPersistida),
                    cargandoHistorial = false,
                    historialInicializado = true,
                )
            }
            iniciarSeguimientosPersistidos(api)
            if (turnoPendiente != null && completa?.id == turnoPendiente.conversationId) {
                if (completa.messages.lastOrNull()?.role?.equals("assistant", ignoreCase = true) == true) {
                    limpiarTurnoPendiente(turnoPendiente.idempotencyKey)
                } else {
                    reanudarTurnoPersistido(turnoPendiente, api)
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (version == cargaVersion) {
                _uiState.update {
                    it.copy(
                        cargandoHistorial = false,
                        historialInicializado = true,
                        errorMensaje = e.message ?: "No pude cargar tus conversaciones.",
                    )
                }
            }
        }
    }

    fun seleccionarConversacion(id: String, api: EdecanApi) {
        if (_uiState.value.enviando || id == _uiState.value.conversationId) return
        abrirConversacion(id, api)
    }

    fun abrirConversacion(id: String, api: EdecanApi) {
        val version = ++cargaVersion
        clavesIdempotencia.limpiar()
        textosPrivadosPorMensaje.clear()
        viewModelScope.launch {
            _uiState.update { it.copy(cargandoHistorial = true, errorMensaje = null) }
            try {
                val conversation = api.conversation(id)
                if (version != cargaVersion) return@launch
                _uiState.update {
                    it.copy(
                        conversaciones = listOf(conversation.copy(messages = emptyList())) +
                            it.conversaciones.filterNot { old -> old.id == conversation.id },
                        conversationId = conversation.id,
                        tituloConversacion = conversation.title,
                        mensajes = mensajesPersistidos(conversation),
                        cargandoHistorial = false,
                        historialInicializado = true,
                        confirmacionPendiente = confirmacionPersistida(conversation),
                    )
                }
                iniciarSeguimientosPersistidos(api)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (version == cargaVersion) {
                    _uiState.update {
                        it.copy(cargandoHistorial = false, errorMensaje = e.message ?: "No pude abrir ese chat.")
                    }
                }
            }
        }
    }

    fun refrescarActual(api: EdecanApi) {
        _uiState.value.conversationId?.let { abrirConversacion(it, api) }
    }

    fun nuevoChat() {
        if (_uiState.value.enviando) return
        cargaVersion += 1
        clavesIdempotencia.limpiar()
        textosPrivadosPorMensaje.clear()
        _uiState.update {
            it.copy(
                conversationId = null,
                tituloConversacion = null,
                mensajes = emptyList(),
                confirmacionPendiente = null,
                herramientaActiva = null,
                herramientaActivaCallId = null,
                errorMensaje = null,
                cargandoHistorial = false,
            )
        }
    }

    fun renombrarConversacion(id: String, titulo: String, api: EdecanApi) {
        val limpio = titulo.trim()
        if (limpio.isEmpty()) return
        viewModelScope.launch {
            try {
                val actualizada = api.renameConversation(id, limpio)
                _uiState.update { estado ->
                    estado.copy(
                        conversaciones = estado.conversaciones.map {
                            if (it.id == id) actualizada else it
                        },
                        tituloConversacion = if (estado.conversationId == id) {
                            actualizada.title
                        } else estado.tituloConversacion,
                    )
                }
            } catch (error: Exception) {
                _uiState.update {
                    it.copy(errorMensaje = error.message ?: "No pude renombrar ese chat.")
                }
            }
        }
    }

    internal fun subirAdjunto(local: ArchivoSubidaLocal, api: EdecanApi) {
        if (_uiState.value.adjuntosComposer.size >= MAX_ATTACHMENTS) {
            local.eliminar()
            _uiState.update { it.copy(errorMensaje = "Puedes adjuntar hasta $MAX_ATTACHMENTS archivos por mensaje.") }
            return
        }
        val localId = idNuevo("adjunto")
        archivosAdjuntos[localId] = local
        _uiState.update {
            it.copy(
                adjuntosComposer = it.adjuntosComposer + AdjuntoComposerUi(
                    localId = localId,
                    filename = local.filename,
                    mime = local.mime,
                    sizeBytes = local.sizeBytes,
                    previewBytes = local.previewBytes,
                ),
                errorMensaje = null,
            )
        }
        ejecutarSubida(localId, api)
    }

    fun reintentarAdjunto(localId: String, api: EdecanApi) {
        if (_uiState.value.adjuntosComposer.none { it.localId == localId && it.estado == EstadoAdjunto.ERROR }) return
        _uiState.update { state ->
            state.copy(
                adjuntosComposer = state.adjuntosComposer.map {
                    if (it.localId == localId) it.copy(estado = EstadoAdjunto.SUBIENDO, error = null) else it
                },
                errorMensaje = null,
            )
        }
        ejecutarSubida(localId, api)
    }

    private fun ejecutarSubida(localId: String, api: EdecanApi) {
        val local = archivosAdjuntos[localId] ?: return
        tareasSubida.remove(localId)?.cancel()
        val job = viewModelScope.launch {
            val adjunto = _uiState.value.adjuntosComposer.firstOrNull { it.localId == localId } ?: return@launch
            try {
                val uploaded = adjuntoUploader.subir(api, local)
                // Un uploader/engine puede terminar justo después de recibir
                // cancelación. Nunca resucitar el chip que la persona quitó.
                if (!currentCoroutineContext().isActive || archivosAdjuntos[localId] !== local) return@launch
                archivosAdjuntos.remove(localId)
                local.eliminar()
                _uiState.update { state ->
                    state.copy(adjuntosComposer = state.adjuntosComposer.map {
                        if (it.localId == localId) it.copy(estado = EstadoAdjunto.LISTO, archivo = uploaded) else it
                    })
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                if (archivosAdjuntos[localId] !== local) return@launch
                _uiState.update { state ->
                    state.copy(adjuntosComposer = state.adjuntosComposer.map {
                        if (it.localId == localId) {
                            it.copy(estado = EstadoAdjunto.ERROR, error = e.message ?: "No se pudo subir")
                        } else it
                    })
                }
            } finally {
                if (tareasSubida[localId] === currentCoroutineContext()[Job]) {
                    tareasSubida.remove(localId)
                }
            }
        }
        tareasSubida[localId] = job
    }

    fun quitarAdjunto(localId: String) {
        tareasSubida.remove(localId)?.cancel()
        archivosAdjuntos.remove(localId)?.eliminar()
        _uiState.update { it.copy(adjuntosComposer = it.adjuntosComposer.filterNot { item -> item.localId == localId }) }
    }

    fun enviar(texto: String, api: EdecanApi) {
        val state = _uiState.value
        val textoLimpio = texto.trim()
        val listos = state.adjuntosComposer.mapNotNull { it.archivo.takeIf { _ -> it.estado == EstadoAdjunto.LISTO } }
        if (state.enviando || state.adjuntosComposer.any { it.estado != EstadoAdjunto.LISTO }) return
        if (textoLimpio.isEmpty() && listos.isEmpty()) return
        enviarNuevo(textoLimpio, listos, api)
    }

    private fun enviarNuevo(texto: String, archivos: List<UploadedFile>, api: EdecanApi) {
        val job = viewModelScope.launch {
            try {
                val idConversacion = try {
                    asegurarConversacion(api)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    _uiState.update { it.copy(errorMensaje = e.message ?: "No pude iniciar el chat.") }
                    return@launch
                }
                val idUsuario = idNuevo()
                val idRespuesta = idNuevo()
                val refs = archivos.map { ArtifactRef(it.id, it.filename, it.mime) }
                // Un contenido nuevo inaugura otro intento lógico. Las claves de
                // mensajes fallidos anteriores ya no deben heredarse.
                val idempotencyKey = clavesIdempotencia.nueva(idUsuario)
                guardarTurnoPendiente(idConversacion, idempotencyKey)
                textosPrivadosPorMensaje[idUsuario] = texto
                actualizarBorrador("")
                _uiState.update {
                    it.copy(
                        mensajes = it.mensajes +
                            MensajeUi(
                                idUsuario,
                                MensajeUi.Rol.USUARIO,
                                ChatSecretRedaction.redact(texto),
                                adjuntos = refs,
                                estadoEntrega = EstadoEntrega.ENVIANDO,
                            ) + MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                        adjuntosComposer = emptyList(),
                        enviando = true,
                        recuperandoTurno = false,
                        errorMensaje = null,
                        herramientaActiva = null,
                        confirmacionPendiente = null,
                    )
                }
                val body = ChatMessageIn(text = texto, attachments = archivos.map { it.id })
                correrTurno(
                    api = api,
                    path = "/v1/conversations/$idConversacion/messages",
                    bodyJson = edecanJson.encodeToString(body),
                    idRespuesta = idRespuesta,
                    idUsuario = idUsuario,
                    idempotencyKey = idempotencyKey,
                    conversationId = idConversacion,
                )
            } finally {
                if (turnoActivo === currentCoroutineContext()[Job]) turnoActivo = null
            }
        }
        turnoActivo = job
    }

    fun reintentarMensaje(idUsuario: String, api: EdecanApi) {
        val mensaje = _uiState.value.mensajes.firstOrNull {
            it.id == idUsuario && it.rol == MensajeUi.Rol.USUARIO && it.estadoEntrega == EstadoEntrega.FALLIDO
        } ?: return
        if (_uiState.value.enviando) return
        val idConversacion = _uiState.value.conversationId ?: return
        val idempotencyKey = clavesIdempotencia.reintentar(idUsuario)
        val idRespuesta = idNuevo()
        _uiState.update { state ->
            state.copy(
                mensajes = state.mensajes.map {
                    if (it.id == idUsuario) it.copy(estadoEntrega = EstadoEntrega.ENVIANDO) else it
                } + MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                enviando = true,
                errorMensaje = null,
            )
        }
        guardarTurnoPendiente(idConversacion, idempotencyKey)
        val job = viewModelScope.launch {
            try {
                correrTurno(
                    api = api,
                    path = "/v1/conversations/$idConversacion/messages",
                    bodyJson = edecanJson.encodeToString(
                        ChatMessageIn(
                            textosPrivadosPorMensaje[idUsuario] ?: mensaje.texto,
                            mensaje.adjuntos.map { it.fileId },
                        ),
                    ),
                    idRespuesta = idRespuesta,
                    idUsuario = idUsuario,
                    idempotencyKey = idempotencyKey,
                    conversationId = idConversacion,
                )
            } finally {
                if (turnoActivo === currentCoroutineContext()[Job]) turnoActivo = null
            }
        }
        turnoActivo = job
    }

    private fun guardarTurnoPendiente(conversationId: String, idempotencyKey: String) {
        savedStateHandle[PENDING_CONVERSATION_KEY] = conversationId
        savedStateHandle[PENDING_IDEMPOTENCY_KEY] = idempotencyKey
    }

    private fun limpiarTurnoPendiente(idempotencyKey: String? = null) {
        val actual = savedStateHandle.turnoPendientePersistido()
        if (idempotencyKey != null && actual?.idempotencyKey != idempotencyKey) return
        savedStateHandle[PENDING_CONVERSATION_KEY] = null
        savedStateHandle[PENDING_IDEMPOTENCY_KEY] = null
    }

    private fun reanudarTurnoPersistido(pendiente: TurnoPendientePersistido, api: EdecanApi) {
        if (turnoActivo?.isActive == true) return
        val state = _uiState.value
        if (state.conversationId != pendiente.conversationId) return
        if (state.mensajes.lastOrNull()?.rol == MensajeUi.Rol.ASISTENTE) {
            limpiarTurnoPendiente(pendiente.idempotencyKey)
            return
        }

        val idRespuesta = idNuevo()
        _uiState.update {
            it.copy(
                mensajes = it.mensajes + MensajeUi(
                    id = idRespuesta,
                    rol = MensajeUi.Rol.ASISTENTE,
                    enProgreso = true,
                ),
                enviando = true,
            ).prepararReanudacion(idRespuesta)
        }
        val job = viewModelScope.launch {
            try {
                correrTurno(
                    api = api,
                    path = "/v1/conversations/${pendiente.conversationId}/messages",
                    bodyJson = null,
                    idRespuesta = idRespuesta,
                    idUsuario = null,
                    idempotencyKey = pendiente.idempotencyKey,
                    conversationId = pendiente.conversationId,
                )
            } finally {
                if (turnoActivo === currentCoroutineContext()[Job]) turnoActivo = null
            }
        }
        turnoActivo = job
    }

    private suspend fun asegurarConversacion(api: EdecanApi): String {
        _uiState.value.conversationId?.let { return it }
        val nueva = api.createConversation()
        _uiState.update {
            it.copy(
                conversationId = nueva.id,
                tituloConversacion = nueva.title,
                conversaciones = listOf(nueva) + it.conversaciones.filterNot { old -> old.id == nueva.id },
            )
        }
        return nueva.id
    }

    fun confirmar(aprobado: Boolean, api: EdecanApi) {
        val pendiente = _uiState.value.confirmacionPendiente ?: return
        val idConversacion = _uiState.value.conversationId ?: return
        if (_uiState.value.enviando) return
        viewModelScope.launch {
            val idRespuesta = idNuevo()
            _uiState.update {
                it.copy(
                    mensajes = it.mensajes + MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                    enviando = true,
                    errorMensaje = null,
                )
            }
            val completado = correrTurno(
                api,
                "/v1/conversations/$idConversacion/confirm",
                edecanJson.encodeToString(ConfirmIn(pendiente.toolCallId, aprobado)),
                idRespuesta,
                null,
            )
            if (completado) {
                _uiState.update { state ->
                    state.copy(
                        confirmacionPendiente = state.confirmacionPendiente
                            ?.takeUnless { it.toolCallId == pendiente.toolCallId },
                    )
                }
            } else {
                // La respuesta pudo perderse después de que el servidor
                // procesara la decisión. Recargar evita ofrecer de nuevo una
                // acción ya consumida; si la red sigue caída, la tarjeta local
                // permanece para no esconder la decisión sin evidencia.
                runCatching { api.conversation(idConversacion) }
                    .onSuccess { conversation ->
                        _uiState.update { state ->
                            state.copy(
                                conversaciones = listOf(conversation.copy(messages = emptyList())) +
                                    state.conversaciones.filterNot { it.id == conversation.id },
                                mensajes = mensajesPersistidos(conversation),
                                confirmacionPendiente = confirmacionPersistida(conversation),
                            )
                        }
                    }
            }
        }
    }

    private suspend fun correrTurno(
        api: EdecanApi,
        path: String,
        bodyJson: String?,
        idRespuesta: String,
        idUsuario: String?,
        idempotencyKey: String? = null,
        conversationId: String? = null,
    ): Boolean {
        var completado = false
        var recuperar = bodyJson == null
        var fallosDeConexion = 0
        fun registrarFallo(mensaje: String) {
            _uiState.update { state ->
                state.copy(
                    errorMensaje = mensaje,
                    borrador = if (state.borrador.isBlank() && idUsuario != null) {
                        state.mensajes.firstOrNull { it.id == idUsuario }?.texto.orEmpty()
                    } else state.borrador,
                    mensajes = state.mensajes.map { message ->
                        if (message.id == idUsuario) {
                            message.copy(estadoEntrega = EstadoEntrega.FALLIDO)
                        } else {
                            message
                        }
                    },
                )
            }
            savedStateHandle[DRAFT_KEY] = _uiState.value.borrador
        }
        try {
            var yaRefresco = false
            while (true) {
                try {
                    val token = api.tokenDeAccesoValido()
                    val eventos = if (recuperar) {
                        requireNotNull(conversationId)
                        requireNotNull(idempotencyKey)
                        sseClient.resume(
                            client = api.httpClientParaStream,
                            url = api.urlCompleta(
                                "/v1/conversations/$conversationId/message-attempts/$idempotencyKey",
                            ),
                            accessToken = token,
                        )
                    } else {
                        sseClient.stream(
                            client = api.httpClientParaStream,
                            url = api.urlCompleta(path),
                            accessToken = token,
                            bodyJson = requireNotNull(bodyJson),
                            idempotencyKey = idempotencyKey,
                        )
                    }
                    var recibioEvento = false
                    var falloTerminal: String? = null
                    eventos.collect { evento ->
                        if (!recibioEvento) {
                            recibioEvento = true
                            fallosDeConexion = 0
                            _uiState.update { it.copy(recuperandoTurno = false, errorMensaje = null) }
                        }
                        idUsuario?.let(::marcarEntregado)
                        val mensajeDeFallo = evento.mensajeDeFalloTerminal()
                        if (mensajeDeFallo != null) {
                            falloTerminal = mensajeDeFallo
                        } else {
                            aplicar(evento, idRespuesta, api)
                        }
                    }
                    if (falloTerminal != null) {
                        // Se lanza DESPUÉS de que el Flow terminó. Así no cruza
                        // el límite del collector ni puede convertirse en un
                        // falso error de conexión dentro de `SseClient`.
                        throw FalloTerminalDelAgente(requireNotNull(falloTerminal))
                    }
                    completado = true
                    break
                } catch (e: SseClient.SseException.IntentoEnCurso) {
                    recuperar = true
                    marcarRecuperando(idRespuesta)
                    esperarReconexion(e.retryAfterSeconds * 1_000)
                } catch (e: SseClient.SseException.Servidor) {
                    when {
                        e.status == 401 && !yaRefresco -> {
                            yaRefresco = true
                            api.refrescar()
                        }
                        e.status == 409 && e.retryAfterSeconds != null &&
                            idempotencyKey != null && conversationId != null -> {
                            recuperar = true
                            marcarRecuperando(idRespuesta)
                            esperarReconexion(requireNotNull(e.retryAfterSeconds) * 1_000)
                        }
                        recuperar && e.status == 404 && bodyJson != null -> {
                            // El POST pudo no haber llegado al servidor. Con la
                            // misma clave y el mismo cuerpo sigue siendo seguro.
                            recuperar = false
                            marcarRecuperando(idRespuesta)
                            esperarReconexion(500)
                        }
                        recuperar && e.status == 404 && bodyJson == null &&
                            conversationId != null &&
                            sincronizarSiElTurnoYaTermino(api, conversationId) -> {
                            completado = true
                            break
                        }
                        else -> throw e
                    }
                } catch (e: SseClient.SseException.Conexion) {
                    if (idempotencyKey == null || conversationId == null) throw e
                    recuperar = true
                    fallosDeConexion += 1
                    marcarRecuperando(idRespuesta)
                    val espera = (500L * (1 shl minOf(fallosDeConexion - 1, 3))).coerceAtMost(4_000)
                    esperarReconexion(espera)
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: FalloTerminalDelAgente) {
            idempotencyKey?.let(::limpiarTurnoPendiente)
            // El servidor ya cerró y guardó este intento con `error`. Un tap en
            // Reintentar debe crear otra UUID en vez de reproducir eternamente
            // el mismo replay fallido.
            idUsuario?.let(clavesIdempotencia::completar)
            registrarFallo(e.message ?: "Edecán no pudo completar este mensaje.")
        } catch (e: Exception) {
            idempotencyKey?.let(::limpiarTurnoPendiente)
            registrarFallo(e.message ?: "No pude completar el mensaje.")
        } finally {
            despertarReconexion?.complete(Unit)
            despertarReconexion = null
            _uiState.update { estado ->
                estado.copy(
                    enviando = false,
                    recuperandoTurno = false,
                    herramientaActiva = null,
                    herramientaActivaCallId = null,
                    mensajes = estado.mensajes.mapNotNull { message ->
                        if (message.id != idRespuesta) return@mapNotNull message
                        if (!completado && message.texto.isBlank() && message.bloques.isEmpty() && message.artefactos.isEmpty()) {
                            null
                        } else message.copy(enProgreso = false)
                    },
                )
            }
        }
        if (completado) {
            idempotencyKey?.let(::limpiarTurnoPendiente)
            if (idUsuario != null) {
                clavesIdempotencia.completar(idUsuario)
                textosPrivadosPorMensaje.remove(idUsuario)
            }
            if (conversationId != null) {
                sincronizarConversacionSilenciosamente(api, conversationId)
            } else {
                refrescarTitulosDeConversacion(api)
            }
        }
        return completado
    }

    private fun marcarRecuperando(idRespuesta: String) {
        _uiState.update { estado ->
            if (estado.recuperandoTurno) estado else estado.prepararReanudacion(idRespuesta)
        }
    }

    private suspend fun esperarReconexion(millis: Long) {
        val senal = CompletableDeferred<Unit>()
        despertarReconexion = senal
        withTimeoutOrNull(millis.coerceIn(100, 30_000)) { senal.await() }
        if (despertarReconexion === senal) despertarReconexion = null
    }

    private suspend fun sincronizarSiElTurnoYaTermino(api: EdecanApi, conversationId: String): Boolean {
        val conversation = try {
            api.conversation(conversationId)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            return false
        }
        if (conversation.messages.lastOrNull()?.role?.equals("assistant", ignoreCase = true) != true) {
            return false
        }
        aplicarConversacionCanonica(conversation, api)
        return true
    }

    private suspend fun sincronizarConversacionSilenciosamente(api: EdecanApi, conversationId: String) {
        try {
            aplicarConversacionCanonica(api.conversation(conversationId), api)
            refrescarTitulosDeConversacion(api)
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // El replay completo ya está en pantalla. La próxima entrada al
            // chat recuperará los IDs y metadatos canónicos.
        }
    }

    private fun aplicarConversacionCanonica(conversation: Conversation, api: EdecanApi) {
        _uiState.update { state ->
            state.copy(
                conversaciones = listOf(conversation.copy(messages = emptyList())) +
                    state.conversaciones.filterNot { it.id == conversation.id },
                conversationId = conversation.id,
                tituloConversacion = conversation.title,
                mensajes = mensajesPersistidos(conversation),
                confirmacionPendiente = confirmacionPersistida(conversation),
                recuperandoTurno = false,
                errorMensaje = null,
            )
        }
        iniciarSeguimientosPersistidos(api)
    }

    private suspend fun refrescarTitulosDeConversacion(api: EdecanApi) {
        try {
            val actualizadas = api.conversations()
            _uiState.update { it.conConversacionesActualizadas(actualizadas) }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // El mensaje ya terminó y quedó persistido. Un fallo secundario al
            // refrescar el nombre no debe convertirlo en un envío fallido; la
            // próxima carga de historial recuperará la metadata canónica.
        }
    }

    private fun marcarEntregado(idUsuario: String) {
        _uiState.update { state ->
            state.copy(mensajes = state.mensajes.map {
                if (it.id == idUsuario) it.copy(estadoEntrega = EstadoEntrega.ENTREGADO) else it
            })
        }
    }

    private fun aplicar(evento: ChatEvent, idRespuesta: String, api: EdecanApi) {
        when (evento) {
            is ChatEvent.TextDelta -> actualizarRespuesta(idRespuesta) { it.copy(texto = it.texto + evento.text) }
            is ChatEvent.ToolStart -> _uiState.update {
                it.copy(
                    herramientaActiva = evento.name,
                    herramientaActivaCallId = evento.toolCallId,
                    herramientaSegundos = 0,
                    herramientaMensaje = null,
                    mensajes = it.mensajes.map { mensaje ->
                        if (mensaje.id == idRespuesta) {
                            mensaje.copy(trabajo = mensaje.trabajo.iniciarPaso(evento.toolCallId, evento.name))
                        } else mensaje
                    },
                )
            }
            is ChatEvent.ToolProgress -> _uiState.update {
                val corresponde = it.herramientaActivaCallId == null || evento.toolCallId == null ||
                    it.herramientaActivaCallId == evento.toolCallId
                if (!corresponde) it else it.copy(
                    herramientaActiva = evento.name,
                    herramientaActivaCallId = evento.toolCallId ?: it.herramientaActivaCallId,
                    herramientaSegundos = maxOf(it.herramientaSegundos, evento.elapsedSeconds),
                    herramientaMensaje = evento.message,
                    mensajes = it.mensajes.map { mensaje ->
                        if (mensaje.id == idRespuesta) {
                            mensaje.copy(
                                trabajo = mensaje.trabajo.actualizarPaso(
                                    evento.toolCallId,
                                    evento.name,
                                    evento.elapsedSeconds,
                                    evento.message,
                                ),
                            )
                        } else mensaje
                    },
                )
            }
            is ChatEvent.ToolEnd -> {
                _uiState.update {
                    aplicarFinDeHerramienta(
                        it,
                        idRespuesta,
                        evento.artifacts,
                        evento.blocks.takeIf { evento.blocksVersion == 1 } ?: emptyList(),
                        evento.toolCallId,
                        evento.name,
                        evento.resultPreview,
                        evento.missionId,
                    )
                }
                evento.missionId?.let { iniciarSeguimientoMision(it, idRespuesta, api) }
            }
            is ChatEvent.ConfirmationRequired -> _uiState.update {
                it.copy(
                    confirmacionPendiente = ConfirmacionPendiente(
                        evento.toolCallId,
                        evento.name,
                        formatearArgumentos(evento.args),
                    ),
                )
            }
            is ChatEvent.Done -> actualizarRespuesta(idRespuesta) { mensaje ->
                mensaje.copy(
                    trabajo = mensaje.trabajo?.let { trabajo ->
                        if (trabajo.missionId == null) trabajo.copy(activo = false) else trabajo
                    },
                )
            }
            // El cierre `error` se consume en `correrTurno`, fuera del
            // collector. Mantener esta rama inocua hace el `when` exhaustivo y
            // evita que una llamada futura vuelva a confundir agente con red.
            is ChatEvent.ErrorEvent -> Unit
            is ChatEvent.Unknown -> Unit
        }
    }

    private fun iniciarSeguimientosPersistidos(api: EdecanApi) {
        _uiState.value.mensajes.forEach { mensaje ->
            mensaje.trabajo?.missionId?.let { iniciarSeguimientoMision(it, mensaje.id, api) }
        }
    }

    private fun iniciarSeguimientoMision(missionId: String, mensajeId: String, api: EdecanApi) {
        if (seguimientoMisiones[missionId]?.isActive == true) return
        seguimientoMisiones[missionId] = viewModelScope.launch {
            var fallosConsecutivos = 0
            try {
                while (currentCoroutineContext().isActive) {
                    try {
                        val detail = api.getMission(missionId)
                        fallosConsecutivos = 0
                        actualizarRespuesta(mensajeId) { mensaje ->
                            mensaje.copy(trabajo = mensaje.trabajo.actualizarMision(detail))
                        }
                        if (detail.mission.status !in setOf("planning", "running")) break
                    } catch (e: CancellationException) {
                        throw e
                    } catch (_: Exception) {
                        fallosConsecutivos += 1
                        if (fallosConsecutivos >= 3) {
                            actualizarRespuesta(mensajeId) { mensaje ->
                                mensaje.copy(
                                    trabajo = (mensaje.trabajo ?: TrabajoUi()).copy(
                                        missionError = "No pude actualizar el progreso. Sigue disponible en Actividad.",
                                    ),
                                )
                            }
                            break
                        }
                    }
                    delay(3_000)
                }
            } finally {
                seguimientoMisiones.remove(missionId)
            }
        }
    }

    private fun actualizarRespuesta(idRespuesta: String, transformar: (MensajeUi) -> MensajeUi) {
        _uiState.update { estado ->
            estado.copy(mensajes = estado.mensajes.map { m -> if (m.id == idRespuesta) transformar(m) else m })
        }
    }

    private companion object {
        const val DRAFT_KEY = "chat_draft"
        const val MAX_ATTACHMENTS = 10
    }

    private class FalloTerminalDelAgente(message: String) : Exception(message)

    override fun onCleared() {
        // SavedStateHandle pertenece al Activity padre: se borra
        // explícitamente para que una cuenta nueva no restaure el borrador.
        savedStateHandle[DRAFT_KEY] = ""
        tareasSubida.values.forEach(Job::cancel)
        tareasSubida.clear()
        seguimientoMisiones.values.forEach(Job::cancel)
        seguimientoMisiones.clear()
        turnoActivo?.cancel()
        turnoActivo = null
        despertarReconexion?.complete(Unit)
        despertarReconexion = null
        archivosAdjuntos.values.forEach(ArchivoSubidaLocal::eliminar)
        archivosAdjuntos.clear()
        clavesIdempotencia.limpiar()
        textosPrivadosPorMensaje.clear()
        limpiarTurnoPendiente()
        cargaVersion += 1
        _uiState.value = ChatUiState()
    }
}
