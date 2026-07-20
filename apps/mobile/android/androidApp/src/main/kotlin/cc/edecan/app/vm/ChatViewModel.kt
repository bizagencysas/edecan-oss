package cc.edecan.app.vm

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.ChatMessageIn
import cc.edecan.shared.ConfirmIn
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.SseClient
import cc.edecan.shared.edecanJson
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** Un mensaje en pantalla. [id] estable para `LazyColumn`/`key`. */
data class MensajeUi(
    val id: String,
    val rol: Rol,
    val texto: String = "",
    val enProgreso: Boolean = false,
) {
    enum class Rol { USUARIO, ASISTENTE }
}

/** Una herramienta `dangerous` que el agente quiere usar y todavía no está
 * aprobada (evento SSE `confirmation_required`, `ARCHITECTURE.md` §10.7) —
 * `ChatScreen` la pinta como una tarjeta Aprobar/Rechazar sobre la lista de
 * mensajes, en vez del aviso de solo-texto que traía el esqueleto v1. */
data class ConfirmacionPendiente(val toolCallId: String, val nombre: String, val argumentos: String)

data class ChatUiState(
    val mensajes: List<MensajeUi> = emptyList(),
    /** Nombre de la herramienta que el agente está usando ahora mismo
     * (evento `tool_start` sin su `tool_end` correspondiente todavía), o
     * `null` si no hay ninguna en curso — `ChatScreen` la muestra como un
     * banner corto encima del campo de texto. */
    val herramientaActiva: String? = null,
    val confirmacionPendiente: ConfirmacionPendiente? = null,
    val enviando: Boolean = false,
    val errorMensaje: String? = null,
)

/**
 * Estado y lógica de una conversación de `ChatScreen` — el equivalente
 * Kotlin de `ChatViewModel.swift` (`EdecanApp`/iOS). Arma a mano la
 * petición SSE de `POST /v1/conversations/{id}/messages` (`docs/api.md`)
 * usando `EdecanApi.urlCompleta`/`tokenDeAccesoValido` (la autenticación) +
 * [SseClient] (el framing del stream) — exactamente la división de
 * responsabilidades que documenta `SseClient`.
 *
 * `ViewModel` plano, no `AndroidViewModel`: a propósito no guarda un
 * [EdecanApi] propio, lo recibe como parámetro en [enviar] — igual que la
 * versión iOS recibe `client: APIClient` en `ChatViewModel.enviar(texto:client:)`
 * en vez de guardarlo. El dueño de esa instancia es `SessionViewModel`.
 */
class ChatViewModel : ViewModel() {
    private val _uiState = MutableStateFlow(ChatUiState())
    val uiState: StateFlow<ChatUiState> = _uiState.asStateFlow()

    private val sseClient = SseClient()
    private var conversationId: String? = null
    private var siguienteId = 0L

    private fun idNuevo(): String = "msg-${siguienteId++}"

    /** `POST /v1/conversations` una sola vez por pantalla — los siguientes
     * envíos reutilizan `conversationId`. */
    private suspend fun asegurarConversacion(api: EdecanApi) {
        if (conversationId != null) return
        try {
            conversationId = api.createConversation().id
        } catch (e: ApiException) {
            _uiState.update { it.copy(errorMensaje = e.message) }
        }
    }

    fun enviar(texto: String, api: EdecanApi) {
        val textoLimpio = texto.trim()
        if (textoLimpio.isEmpty() || _uiState.value.enviando) return

        viewModelScope.launch {
            asegurarConversacion(api)
            val idConversacion = conversationId ?: return@launch

            val idUsuario = idNuevo()
            val idRespuesta = idNuevo()
            _uiState.update {
                it.copy(
                    mensajes = it.mensajes +
                        MensajeUi(idUsuario, MensajeUi.Rol.USUARIO, textoLimpio) +
                        MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                    enviando = true,
                    errorMensaje = null,
                    herramientaActiva = null,
                    confirmacionPendiente = null,
                )
            }

            val bodyJson = edecanJson.encodeToString(ChatMessageIn(textoLimpio))
            correrTurno(api, "/v1/conversations/$idConversacion/messages", bodyJson, idRespuesta)
        }
    }

    /** Resuelve una [ConfirmacionPendiente] mostrada por `ChatScreen`:
     * `POST /v1/conversations/{id}/confirm` (`ARCHITECTURE.md` §10.12).
     * Igual que [enviar], el turno es otro stream SSE que llega a los mismos
     * eventos (`message.delta`/`tool.start`/.../`message.done`) — se reutiliza
     * el mismo [correrTurno] para no duplicar el manejo del stream. */
    fun confirmar(aprobado: Boolean, api: EdecanApi) {
        val pendiente = _uiState.value.confirmacionPendiente ?: return
        val idConversacion = conversationId ?: return
        if (_uiState.value.enviando) return

        viewModelScope.launch {
            val idRespuesta = idNuevo()
            _uiState.update {
                it.copy(
                    mensajes = it.mensajes + MensajeUi(idRespuesta, MensajeUi.Rol.ASISTENTE, enProgreso = true),
                    enviando = true,
                    errorMensaje = null,
                    confirmacionPendiente = null,
                )
            }

            val bodyJson = edecanJson.encodeToString(ConfirmIn(pendiente.toolCallId, aprobado))
            correrTurno(api, "/v1/conversations/$idConversacion/confirm", bodyJson, idRespuesta)
        }
    }

    /** Abre el POST SSE contra `path` (turno normal o confirmación) y
     * aplica cada [ChatEvent] al mensaje [idRespuesta] hasta que el stream
     * cierra — código compartido entre [enviar] y [confirmar]. */
    private suspend fun correrTurno(api: EdecanApi, path: String, bodyJson: String, idRespuesta: String) {
        try {
            var yaRefresco = false
            while (true) {
                try {
                    val url = api.urlCompleta(path)
                    val token = api.tokenDeAccesoValido()
                    sseClient.stream(api.httpClientParaStream, url, token, bodyJson).collect { evento ->
                        aplicar(evento, idRespuesta)
                    }
                    break
                } catch (e: SseClient.SseException.Servidor) {
                    if (e.status != 401 || yaRefresco) throw e
                    // El stream SSE no pasa por conAutoRefresh. Ante el 401
                    // inicial renovamos una sola vez y reconstruimos toda la
                    // petición con el access token nuevo.
                    yaRefresco = true
                    api.refrescar()
                }
            }
        } catch (e: Exception) {
            _uiState.update { it.copy(errorMensaje = e.message ?: "Error desconocido") }
        } finally {
            _uiState.update { estado ->
                estado.copy(
                    enviando = false,
                    herramientaActiva = null,
                    mensajes = estado.mensajes.map { m ->
                        if (m.id == idRespuesta) m.copy(enProgreso = false) else m
                    },
                )
            }
        }
    }

    private fun aplicar(evento: ChatEvent, idRespuesta: String) {
        when (evento) {
            is ChatEvent.TextDelta -> actualizarRespuesta(idRespuesta) { it.copy(texto = it.texto + evento.text) }
            is ChatEvent.ToolStart -> _uiState.update { it.copy(herramientaActiva = evento.name) }
            is ChatEvent.ToolEnd -> _uiState.update { it.copy(herramientaActiva = null) }
            is ChatEvent.ConfirmationRequired -> _uiState.update {
                it.copy(
                    confirmacionPendiente =
                        ConfirmacionPendiente(evento.toolCallId, evento.name, formatearArgumentos(evento.args)),
                )
            }
            is ChatEvent.Done -> Unit
            is ChatEvent.ErrorEvent -> _uiState.update { it.copy(errorMensaje = evento.message) }
            is ChatEvent.Unknown -> Unit
        }
    }

    private fun actualizarRespuesta(idRespuesta: String, transformar: (MensajeUi) -> MensajeUi) {
        _uiState.update { estado ->
            estado.copy(mensajes = estado.mensajes.map { m -> if (m.id == idRespuesta) transformar(m) else m })
        }
    }

    /** `{"para": "...", "asunto": "..."}` -> `"para: ... · asunto: ..."` —
     * texto plano simple para la tarjeta de confirmación; nunca lanza ante
     * un argumento anidado (lo imprime tal cual con `toString()`). */
    private fun formatearArgumentos(args: JsonElement): String {
        val objeto = args as? JsonObject ?: return ""
        if (objeto.isEmpty()) return ""
        return objeto.entries.joinToString(" · ") { (clave, valor) ->
            val texto = (valor as? JsonPrimitive)?.contentOrNull ?: valor.toString()
            "$clave: $texto"
        }
    }
}
