package cc.edecan.app.vm

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ArtifactRef
import cc.edecan.shared.ChatBlock
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.ChatMessageIn
import cc.edecan.shared.ConfirmIn
import cc.edecan.shared.Conversation
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.Message
import cc.edecan.shared.SseClient
import cc.edecan.shared.UploadContent
import cc.edecan.shared.UploadedFile
import cc.edecan.shared.adjuntos
import cc.edecan.shared.edecanJson
import cc.edecan.shared.texto
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
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
    val estadoEntrega: EstadoEntrega? = null,
) {
    enum class Rol { USUARIO, ASISTENTE }
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
    val confirmacionPendiente: ConfirmacionPendiente? = null,
    val enviando: Boolean = false,
    val errorMensaje: String? = null,
)

internal fun aplicarFinDeHerramienta(
    estado: ChatUiState,
    idRespuesta: String,
    nuevos: List<ArtifactRef>,
    bloquesNuevos: List<ChatBlock> = emptyList(),
    toolCallId: String? = null,
): ChatUiState = estado.copy(
    herramientaActiva = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) null else estado.herramientaActiva,
    herramientaActivaCallId = if (
        estado.herramientaActivaCallId == null || toolCallId == null ||
        estado.herramientaActivaCallId == toolCallId
    ) null else estado.herramientaActivaCallId,
    mensajes = estado.mensajes.map { mensaje ->
        if (mensaje.id != idRespuesta) return@map mensaje
        val ids = mensaje.artefactos.mapTo(mutableSetOf()) { it.fileId }
        val bloques = mensaje.bloques.toMutableList()
        bloquesNuevos.forEach { bloque -> if (bloque !in bloques) bloques += bloque }
        mensaje.copy(
            artefactos = mensaje.artefactos + nuevos.filter { ids.add(it.fileId) },
            bloques = bloques,
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
                val ends = message.toolEndsPersistidos()
                MensajeUi(
                    id = message.id,
                    rol = MensajeUi.Rol.ASISTENTE,
                    texto = message.texto,
                    artefactos = ends.flatMap { it.artifacts }.distinctBy { it.fileId },
                    bloques = ends.filter { it.blocksVersion == 1 }.flatMap { it.blocks }.distinct(),
                )
            }
            else -> null
        }
    }

private fun Message.toolEndsPersistidos(): List<ChatEvent.ToolEnd> =
    (toolCalls as? JsonArray).orEmpty().mapNotNull { raw ->
        runCatching { edecanJson.decodeFromJsonElement<ChatEvent>(raw) }.getOrNull() as? ChatEvent.ToolEnd
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
    private val clavesIdempotencia = ClavesIntentoLogico()
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

    private suspend fun cargarListaInicial(api: EdecanApi) {
        val version = ++cargaVersion
        _uiState.update { it.copy(cargandoHistorial = true, errorMensaje = null) }
        try {
            val conversaciones = api.conversations()
            val actual = _uiState.value.conversationId
            val destino = conversaciones.firstOrNull { it.id == actual } ?: conversaciones.firstOrNull()
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
        viewModelScope.launch {
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
            actualizarBorrador("")
            _uiState.update {
                it.copy(
                    mensajes = it.mensajes +
                        MensajeUi(
                            idUsuario,
                            MensajeUi.Rol.USUARIO,
                            texto,
                            adjuntos = refs,
                            estadoEntrega = EstadoEntrega.ENVIANDO,
                        ) + MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                    adjuntosComposer = emptyList(),
                    enviando = true,
                    errorMensaje = null,
                    herramientaActiva = null,
                    confirmacionPendiente = null,
                )
            }
            val body = ChatMessageIn(text = texto, attachments = archivos.map { it.id })
            correrTurno(
                api,
                "/v1/conversations/$idConversacion/messages",
                edecanJson.encodeToString(body),
                idRespuesta,
                idUsuario,
                idempotencyKey,
            )
        }
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
        viewModelScope.launch {
            correrTurno(
                api,
                "/v1/conversations/$idConversacion/messages",
                edecanJson.encodeToString(
                    ChatMessageIn(mensaje.texto, mensaje.adjuntos.map { it.fileId }),
                ),
                idRespuesta,
                idUsuario,
                idempotencyKey,
            )
        }
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
        bodyJson: String,
        idRespuesta: String,
        idUsuario: String?,
        idempotencyKey: String? = null,
    ): Boolean {
        var completado = false
        try {
            var yaRefresco = false
            while (true) {
                try {
                    val url = api.urlCompleta(path)
                    val token = api.tokenDeAccesoValido()
                    sseClient.stream(
                        api.httpClientParaStream,
                        url,
                        token,
                        bodyJson,
                        idempotencyKey,
                    ).collect { evento ->
                        idUsuario?.let(::marcarEntregado)
                        aplicar(evento, idRespuesta)
                    }
                    completado = true
                    break
                } catch (e: SseClient.SseException.Servidor) {
                    if (e.status != 401 || yaRefresco) throw e
                    yaRefresco = true
                    api.refrescar()
                }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _uiState.update { state ->
                state.copy(
                    errorMensaje = e.message ?: "No pude completar el mensaje.",
                    borrador = if (state.borrador.isBlank() && idUsuario != null) {
                        state.mensajes.firstOrNull { it.id == idUsuario }?.texto.orEmpty()
                    } else state.borrador,
                    mensajes = state.mensajes.map { message ->
                        if (message.id == idUsuario) message.copy(estadoEntrega = EstadoEntrega.FALLIDO) else message
                    },
                )
            }
            savedStateHandle[DRAFT_KEY] = _uiState.value.borrador
        } finally {
            _uiState.update { estado ->
                estado.copy(
                    enviando = false,
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
        if (completado && idUsuario != null) clavesIdempotencia.completar(idUsuario)
        return completado
    }

    private fun marcarEntregado(idUsuario: String) {
        _uiState.update { state ->
            state.copy(mensajes = state.mensajes.map {
                if (it.id == idUsuario) it.copy(estadoEntrega = EstadoEntrega.ENTREGADO) else it
            })
        }
    }

    private fun aplicar(evento: ChatEvent, idRespuesta: String) {
        when (evento) {
            is ChatEvent.TextDelta -> actualizarRespuesta(idRespuesta) { it.copy(texto = it.texto + evento.text) }
            is ChatEvent.ToolStart -> _uiState.update {
                it.copy(herramientaActiva = evento.name, herramientaActivaCallId = evento.toolCallId)
            }
            is ChatEvent.ToolEnd -> _uiState.update {
                aplicarFinDeHerramienta(
                    it,
                    idRespuesta,
                    evento.artifacts,
                    evento.blocks.takeIf { evento.blocksVersion == 1 } ?: emptyList(),
                    evento.toolCallId,
                )
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
            is ChatEvent.Done -> Unit
            is ChatEvent.ErrorEvent -> throw IllegalStateException(evento.message)
            is ChatEvent.Unknown -> Unit
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

    override fun onCleared() {
        // SavedStateHandle pertenece al Activity padre: se borra
        // explícitamente para que una cuenta nueva no restaure el borrador.
        savedStateHandle[DRAFT_KEY] = ""
        tareasSubida.values.forEach(Job::cancel)
        tareasSubida.clear()
        archivosAdjuntos.values.forEach(ArchivoSubidaLocal::eliminar)
        archivosAdjuntos.clear()
        clavesIdempotencia.limpiar()
        cargaVersion += 1
        _uiState.value = ChatUiState()
        super.onCleared()
    }
}
