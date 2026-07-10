package cc.edecan.app.vm

import android.app.Application
import android.media.MediaPlayer
import android.media.MediaRecorder
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.ChatMessageIn
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.SseClient
import cc.edecan.shared.VoiceAudio
import cc.edecan.shared.edecanJson
import java.io.File
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString

/** Señal de control interna de [VozViewModel.enviarYEsperarRespuesta]: el
 * turno SSE terminó en `confirmation_required` o `error` en vez de
 * `done` — no es un error HTTP (no hay `status`/`ApiException` que le
 * quede natural), así que no se reusa [ApiException.Servidor] solo para
 * tener un mensaje. `procesar` la atrapa en su rama genérica
 * `catch (e: Exception)`, mismo resultado neto que un [ApiException]. */
private class TurnoVozException(message: String) : Exception(message)

data class VozUiState(
    val grabando: Boolean = false,
    val procesando: Boolean = false,
    val reproduciendo: Boolean = false,
    val textoTranscrito: String? = null,
    val respuesta: String? = null,
    val errorMensaje: String? = null,
    /** `true` si el tenant no conectó su propio proveedor de STT ni TTS
     * (`GET /v1/credentials`) — la voz igual "funciona" (el servidor cae al
     * `StubSTT`/`StubTTS` offline, `docs/api.md` §"Voz web") pero
     * `StubSTT` siempre devuelve el mismo texto fijo sin importar lo que se
     * dijo, así que [VozScreen] muestra un aviso claro en vez de dejar que
     * el usuario piense que el reconocimiento real está fallando. */
    val vozNoConectada: Boolean = false,
)

/**
 * Push-to-talk de la pestaña Voz: graba con [MediaRecorder]
 * (`RECORD_AUDIO`), transcribe (`POST /v1/voice/transcribe`), manda el
 * texto transcrito por el MISMO flujo de turno que usa el chat (`POST
 * /v1/conversations/{id}/messages`, SSE) y reproduce la respuesta con
 * [MediaPlayer] tras sintetizarla (`POST /v1/voice/speak`). `AndroidViewModel`
 * porque tanto grabar como reproducir necesitan un `Context` (archivos
 * temporales en `cacheDir`).
 *
 * Usa su propia conversación (no comparte `conversationId` con
 * `ChatViewModel`): mantiene este esqueleto simple —una pestaña, un flujo
 * de ida y vuelta— sin acoplar dos `ViewModel`s que hoy Compose ya
 * resolvería como la MISMA instancia si pidieran `ChatViewModel` con
 * `viewModel()` (mismo `ViewModelStore`, ver docstring de `App.kt`).
 */
class VozViewModel(application: Application) : AndroidViewModel(application) {
    private val _uiState = MutableStateFlow(VozUiState())
    val uiState: StateFlow<VozUiState> = _uiState.asStateFlow()

    private val sseClient = SseClient()
    private var grabador: MediaRecorder? = null
    private var archivoGrabacion: File? = null
    private var conversationId: String? = null
    private var yaVerificoVoz = false

    /** `GET /v1/credentials` una sola vez por pantalla, solo para el aviso
     * de [VozUiState.vozNoConectada] — nunca bloquea el push-to-talk si
     * falla (se queda con el valor por defecto, `false`). */
    fun verificarVozConectada(api: EdecanApi) {
        if (yaVerificoVoz) return
        yaVerificoVoz = true
        viewModelScope.launch {
            try {
                val credenciales = api.credentials()
                _uiState.update {
                    it.copy(vozNoConectada = credenciales.voiceStt == null && credenciales.voiceTts == null)
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Silencioso — ver docstring de la función.
            }
        }
    }

    fun iniciarGrabacion() {
        if (_uiState.value.grabando || _uiState.value.procesando) return
        val contexto = getApplication<Application>()
        val archivo = File(contexto.cacheDir, "voz_${System.currentTimeMillis()}.m4a")
        val recorder = crearMediaRecorder()
        try {
            recorder.setAudioSource(MediaRecorder.AudioSource.MIC)
            recorder.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            recorder.setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            recorder.setOutputFile(archivo.absolutePath)
            recorder.prepare()
            recorder.start()
            grabador = recorder
            archivoGrabacion = archivo
            _uiState.update {
                it.copy(grabando = true, errorMensaje = null, textoTranscrito = null, respuesta = null)
            }
        } catch (e: Exception) {
            recorder.release()
            _uiState.update { it.copy(errorMensaje = "No se pudo iniciar la grabación: ${e.message}") }
        }
    }

    /** Suprime la advertencia de deprecación del constructor sin `Context`
     * de [MediaRecorder] (deprecado desde API 31): `minSdk` de este proyecto
     * es 26 (`shared/build.gradle.kts`), y el reemplazo `MediaRecorder(Context)`
     * no existe antes de API 31 — el constructor viejo sigue siendo la única
     * forma de instanciarlo en el rango completo de dispositivos soportados. */
    @Suppress("DEPRECATION")
    private fun crearMediaRecorder(): MediaRecorder = MediaRecorder()

    fun detenerYEnviar(api: EdecanApi) {
        val recorder = grabador
        val archivo = archivoGrabacion
        grabador = null
        archivoGrabacion = null
        if (recorder == null) return

        try {
            recorder.stop()
        } catch (e: Exception) {
            // Grabación demasiado corta (ni un frame de audio) u otro error
            // de `stop()` — se descarta, no se sube un archivo vacío/roto.
            archivo?.delete()
            recorder.release()
            _uiState.update { it.copy(grabando = false, errorMensaje = "Grabación demasiado corta, intenta de nuevo.") }
            return
        }
        recorder.release()
        _uiState.update { it.copy(grabando = false) }

        if (archivo == null || !archivo.exists() || archivo.length() == 0L) {
            _uiState.update { it.copy(errorMensaje = "No se grabó audio.") }
            return
        }
        procesar(archivo, api)
    }

    /** Cancela una grabación en curso sin enviarla (p. ej. el usuario
     * suelta el botón fuera del área de push-to-talk). */
    fun cancelarGrabacion() {
        val recorder = grabador ?: return
        grabador = null
        try {
            recorder.stop()
        } catch (e: Exception) {
            // Grabación demasiado corta — se descarta igual, nada que hacer.
        }
        recorder.release()
        archivoGrabacion?.delete()
        archivoGrabacion = null
        _uiState.update { it.copy(grabando = false) }
    }

    private fun procesar(archivo: File, api: EdecanApi) {
        viewModelScope.launch {
            _uiState.update { it.copy(procesando = true, errorMensaje = null) }
            try {
                val audioBytes = withContext(Dispatchers.IO) { archivo.readBytes() }
                val texto = api.transcribirVoz(audioBytes, archivo.name, "audio/mp4", language = "es")
                _uiState.update { it.copy(textoTranscrito = texto) }

                val respuesta = enviarYEsperarRespuesta(texto, api)
                _uiState.update { it.copy(respuesta = respuesta, procesando = false) }
                reproducir(respuesta, api)
            } catch (e: ApiException) {
                _uiState.update { it.copy(procesando = false, errorMensaje = e.message) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(procesando = false, errorMensaje = e.message ?: "Error desconocido") }
            } finally {
                withContext(Dispatchers.IO) { archivo.delete() }
            }
        }
    }

    private suspend fun asegurarConversacion(api: EdecanApi): String {
        conversationId?.let { return it }
        val id = api.createConversation(titulo = "Voz").id
        conversationId = id
        return id
    }

    /** Manda `texto` por el mismo turno SSE que usa el chat de texto
     * (`ChatViewModel.enviar`) y devuelve la respuesta completa YA
     * concatenada — a diferencia del chat, acá no hay una burbuja
     * incremental que ir llenando, así que se junta todo antes de hablar. */
    private suspend fun enviarYEsperarRespuesta(texto: String, api: EdecanApi): String {
        val idConversacion = asegurarConversacion(api)
        val url = api.urlCompleta("/v1/conversations/$idConversacion/messages")
        val token = api.tokenDeAccesoValido()
        val bodyJson = edecanJson.encodeToString(ChatMessageIn(texto))
        val textoAcumulado = StringBuilder()
        var errorDelTurno: String? = null

        sseClient.stream(api.httpClientParaStream, url, token, bodyJson).collect { evento ->
            when (evento) {
                is ChatEvent.TextDelta -> textoAcumulado.append(evento.text)
                is ChatEvent.ConfirmationRequired ->
                    errorDelTurno = "Edecán necesita tu confirmación para usar «${evento.name}» — " +
                        "resuélvelo desde la pestaña Chat."
                is ChatEvent.ErrorEvent -> errorDelTurno = evento.message
                else -> Unit
            }
        }

        errorDelTurno?.let { throw TurnoVozException(it) }
        return textoAcumulado.toString().ifBlank { "Listo." }
    }

    private suspend fun reproducir(texto: String, api: EdecanApi) {
        try {
            val audio = api.hablar(texto)
            reproducirAudio(audio)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _uiState.update { it.copy(errorMensaje = "No se pudo reproducir la respuesta: ${e.message}") }
        }
    }

    private suspend fun reproducirAudio(audio: VoiceAudio) {
        val contexto = getApplication<Application>()
        val extension = if (audio.contentType.contains("wav", ignoreCase = true)) "wav" else "mp3"
        val archivo = File(contexto.cacheDir, "voz_respuesta_${System.currentTimeMillis()}.$extension")
        withContext(Dispatchers.IO) { archivo.writeBytes(audio.bytes) }

        _uiState.update { it.copy(reproduciendo = true) }
        val player = MediaPlayer()

        fun terminar() {
            player.release()
            archivo.delete()
            _uiState.update { it.copy(reproduciendo = false) }
        }

        player.setOnCompletionListener { terminar() }
        player.setOnErrorListener { _, _, _ -> terminar(); true }
        try {
            // `prepare()` es síncrono, pero el archivo es local y corto (una
            // respuesta hablada de pocos segundos) — el bloqueo del hilo
            // principal es despreciable; usar `prepareAsync()` acá sería
            // más "correcto" en teoría pero suma complejidad de callbacks
            // real sin un beneficio medible para este tamaño de archivo.
            player.setDataSource(archivo.absolutePath)
            player.prepare()
            player.start()
        } catch (e: Exception) {
            terminar()
            _uiState.update { it.copy(errorMensaje = "No se pudo reproducir la respuesta: ${e.message}") }
        }
    }

    override fun onCleared() {
        super.onCleared()
        grabador?.let { recorder ->
            try {
                recorder.stop()
            } catch (e: Exception) {
                // El `ViewModel` se está destruyendo igual — nada que hacer.
            }
            recorder.release()
        }
        grabador = null
        archivoGrabacion?.delete()
    }
}
