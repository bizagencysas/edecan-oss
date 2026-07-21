package cc.edecan.app.vm

import android.app.Application
import android.media.MediaPlayer
import android.media.MediaRecorder
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import android.speech.tts.TextToSpeech
import android.speech.tts.UtteranceProgressListener
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import cc.edecan.shared.ApiException
import cc.edecan.shared.ChatEvent
import cc.edecan.shared.ChatMessageIn
import cc.edecan.shared.ConfirmIn
import cc.edecan.shared.EdecanApi
import cc.edecan.shared.SseClient
import cc.edecan.shared.VoiceAudio
import cc.edecan.shared.edecanJson
import java.io.File
import java.util.Locale
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

private data class ResultadoTurnoVoz(
    val texto: String,
    val confirmacion: ConfirmacionPendiente? = null,
)

data class VozUiState(
    val grabando: Boolean = false,
    val procesando: Boolean = false,
    val reproduciendo: Boolean = false,
    val textoTranscrito: String? = null,
    val respuesta: String? = null,
    val errorMensaje: String? = null,
    /** La confirmación aparece y se resuelve dentro de Voz. Una frase hablada
     * nunca obliga a abandonar el flujo para buscar una pestaña técnica. */
    val confirmacionPendiente: ConfirmacionPendiente? = null,
    /** `true` si faltan ambos proveedores. Android usa sus servicios locales
     * y nunca llama los stubs del servidor en ese caso. */
    val vozNoConectada: Boolean = true,
    /** Defaults fail-safe: hasta confirmar credenciales, no se envía audio
     * ni texto a los stubs del servidor. */
    val sttDelDispositivo: Boolean = true,
    val ttsDelDispositivo: Boolean = true,
    /** Mismo hilo del chat principal cuando Voz se abrió desde él. */
    val conversationId: String? = null,
)

/**
 * Push-to-talk de la pantalla de voz accesible desde Chat: graba con [MediaRecorder]
 * (`RECORD_AUDIO`), transcribe (`POST /v1/voice/transcribe`), manda el
 * texto transcrito por el MISMO flujo de turno que usa el chat (`POST
 * /v1/conversations/{id}/messages`, SSE) y reproduce la respuesta con
 * [MediaPlayer] tras sintetizarla (`POST /v1/voice/speak`). `AndroidViewModel`
 * porque tanto grabar como reproducir necesitan un `Context` (archivos
 * temporales en `cacheDir`).
 *
 * Recibe el `conversationId` del chat al abrirse. Si todavía no existe,
 * crea uno y lo publica para que Chat lo adopte al volver.
 */
class VozViewModel(application: Application) : AndroidViewModel(application) {
    private val _uiState = MutableStateFlow(VozUiState())
    val uiState: StateFlow<VozUiState> = _uiState.asStateFlow()

    private val sseClient = SseClient()
    private var grabador: MediaRecorder? = null
    private var archivoGrabacion: File? = null
    private var reconocimientoLocal: SpeechRecognizer? = null
    private var apiReconocimientoLocal: EdecanApi? = null
    private var ttsLocal: TextToSpeech? = null
    private var reproductorRespuesta: MediaPlayer? = null
    private var archivoRespuestaVoz: File? = null
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
                    it.copy(
                        vozNoConectada = credenciales.voiceStt == null && credenciales.voiceTts == null,
                        sttDelDispositivo = credenciales.voiceStt == null,
                        ttsDelDispositivo = credenciales.voiceTts == null,
                    )
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Silencioso — ver docstring de la función.
            }
        }
    }

    fun usarConversacion(id: String?) {
        if (_uiState.value.procesando || _uiState.value.grabando) return
        conversationId = id
        _uiState.update { it.copy(conversationId = id) }
    }

    fun iniciarGrabacion(api: EdecanApi) {
        if (_uiState.value.grabando || _uiState.value.procesando) return
        if (_uiState.value.sttDelDispositivo) {
            iniciarReconocimientoDelDispositivo(api)
            return
        }
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
                it.copy(
                    grabando = true,
                    errorMensaje = null,
                    textoTranscrito = null,
                    respuesta = null,
                    confirmacionPendiente = null,
                )
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

    private fun iniciarReconocimientoDelDispositivo(api: EdecanApi) {
        val contexto = getApplication<Application>()
        if (!SpeechRecognizer.isRecognitionAvailable(contexto)) {
            _uiState.update {
                it.copy(errorMensaje = "Este dispositivo no tiene reconocimiento de voz disponible.")
            }
            return
        }
        val recognizer = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S &&
                SpeechRecognizer.isOnDeviceRecognitionAvailable(contexto)
            ) {
                SpeechRecognizer.createOnDeviceSpeechRecognizer(contexto)
            } else {
                SpeechRecognizer.createSpeechRecognizer(contexto)
            }
        } catch (e: Exception) {
            _uiState.update { it.copy(errorMensaje = "No pude abrir el reconocimiento de voz: ${e.message}") }
            return
        }
        reconocimientoLocal?.destroy()
        reconocimientoLocal = recognizer
        apiReconocimientoLocal = api
        recognizer.setRecognitionListener(object : RecognitionListener {
            override fun onReadyForSpeech(params: Bundle?) = Unit
            override fun onBeginningOfSpeech() = Unit
            override fun onRmsChanged(rmsdB: Float) = Unit
            override fun onBufferReceived(buffer: ByteArray?) = Unit
            override fun onEndOfSpeech() {
                _uiState.update { it.copy(grabando = false, procesando = true) }
            }
            override fun onError(error: Int) {
                liberarReconocimientoLocal()
                _uiState.update {
                    it.copy(
                        grabando = false,
                        procesando = false,
                        errorMensaje = mensajeErrorReconocimiento(error),
                    )
                }
            }
            override fun onResults(results: Bundle?) {
                val texto = results?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                    ?.firstOrNull()?.trim().orEmpty()
                val currentApi = apiReconocimientoLocal
                liberarReconocimientoLocal()
                if (texto.isBlank() || currentApi == null) {
                    _uiState.update {
                        it.copy(grabando = false, procesando = false, errorMensaje = "No entendí lo que dijiste. Intenta de nuevo.")
                    }
                } else {
                    procesarTextoReconocido(texto, currentApi)
                }
            }
            override fun onPartialResults(partialResults: Bundle?) = Unit
            override fun onEvent(eventType: Int, params: Bundle?) = Unit
        })
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1)
        }
        try {
            recognizer.startListening(intent)
            _uiState.update {
                it.copy(
                    grabando = true,
                    procesando = false,
                    errorMensaje = null,
                    textoTranscrito = null,
                    respuesta = null,
                    confirmacionPendiente = null,
                )
            }
        } catch (e: Exception) {
            liberarReconocimientoLocal()
            _uiState.update { it.copy(errorMensaje = "No pude empezar a escuchar: ${e.message}") }
        }
    }

    private fun mensajeErrorReconocimiento(error: Int): String = when (error) {
        SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS -> "Android no tiene permiso para usar el micrófono."
        SpeechRecognizer.ERROR_NETWORK, SpeechRecognizer.ERROR_NETWORK_TIMEOUT ->
            "El reconocimiento de este dispositivo necesita conexión y no pudo acceder a ella."
        SpeechRecognizer.ERROR_NO_MATCH -> "No entendí lo que dijiste. Intenta de nuevo."
        SpeechRecognizer.ERROR_SPEECH_TIMEOUT -> "No escuché ninguna voz. Intenta de nuevo."
        SpeechRecognizer.ERROR_RECOGNIZER_BUSY -> "El reconocimiento de voz está ocupado. Espera un momento."
        else -> "El reconocimiento de voz del dispositivo falló ($error)."
    }

    private fun liberarReconocimientoLocal() {
        reconocimientoLocal?.destroy()
        reconocimientoLocal = null
        apiReconocimientoLocal = null
    }

    fun detenerYEnviar(api: EdecanApi) {
        if (_uiState.value.sttDelDispositivo && reconocimientoLocal != null) {
            _uiState.update { it.copy(grabando = false, procesando = true) }
            reconocimientoLocal?.stopListening()
            return
        }
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
        if (_uiState.value.sttDelDispositivo && reconocimientoLocal != null) {
            reconocimientoLocal?.cancel()
            liberarReconocimientoLocal()
            _uiState.update { it.copy(grabando = false, procesando = false) }
            return
        }
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

                procesarTextoEnConversacion(texto, api)
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

    private fun procesarTextoReconocido(texto: String, api: EdecanApi) {
        viewModelScope.launch {
            _uiState.update {
                it.copy(grabando = false, procesando = true, textoTranscrito = texto, errorMensaje = null)
            }
            try {
                procesarTextoEnConversacion(texto, api)
            } catch (e: ApiException) {
                _uiState.update { it.copy(procesando = false, errorMensaje = e.message) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update { it.copy(procesando = false, errorMensaje = e.message ?: "Error desconocido") }
            }
        }
    }

    private suspend fun procesarTextoEnConversacion(texto: String, api: EdecanApi) {
        val resultado = ejecutarTurno(
            api = api,
            path = "/v1/conversations/${asegurarConversacion(api)}/messages",
            bodyJson = edecanJson.encodeToString(ChatMessageIn(texto)),
        )
        if (resultado.confirmacion != null) {
            _uiState.update {
                it.copy(
                    respuesta = resultado.texto.ifBlank { null },
                    procesando = false,
                    confirmacionPendiente = resultado.confirmacion,
                )
            }
        } else {
            val respuesta = resultado.texto.ifBlank { "Listo." }
            _uiState.update { it.copy(respuesta = respuesta, procesando = false) }
            reproducir(respuesta, api)
        }
    }

    private suspend fun asegurarConversacion(api: EdecanApi): String {
        conversationId?.let { return it }
        val id = api.createConversation(titulo = "Voz").id
        conversationId = id
        _uiState.update { it.copy(conversationId = id) }
        return id
    }

    /** Resuelve la única confirmación humana del turno sin salir de Voz. */
    fun resolverConfirmacion(aprobado: Boolean, api: EdecanApi) {
        val pendiente = _uiState.value.confirmacionPendiente ?: return
        val idConversacion = conversationId ?: return
        if (_uiState.value.procesando) return

        viewModelScope.launch {
            _uiState.update {
                it.copy(
                    procesando = true,
                    errorMensaje = null,
                    confirmacionPendiente = null,
                )
            }
            try {
                val resultado = ejecutarTurno(
                    api = api,
                    path = "/v1/conversations/$idConversacion/confirm",
                    bodyJson = edecanJson.encodeToString(
                        ConfirmIn(pendiente.toolCallId, aprobado),
                    ),
                )
                if (resultado.confirmacion != null) {
                    _uiState.update {
                        it.copy(
                            procesando = false,
                            respuesta = resultado.texto.ifBlank { null },
                            confirmacionPendiente = resultado.confirmacion,
                        )
                    }
                    return@launch
                }
                val respuesta = resultado.texto.ifBlank { "Listo." }
                _uiState.update { it.copy(procesando = false, respuesta = respuesta) }
                reproducir(respuesta, api)
            } catch (e: ApiException) {
                _uiState.update { it.copy(procesando = false, errorMensaje = e.message) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                _uiState.update {
                    it.copy(procesando = false, errorMensaje = e.message ?: "Error desconocido")
                }
            }
        }
    }

    /** Corre un mensaje o su confirmación por el mismo SSE del chat y
     * devuelve texto + confirmación estructurada. También comparte el
     * retry único tras 401 que ya usa [ChatViewModel]. */
    private suspend fun ejecutarTurno(
        api: EdecanApi,
        path: String,
        bodyJson: String,
    ): ResultadoTurnoVoz {
        val textoAcumulado = StringBuilder()
        var errorDelTurno: String? = null
        var confirmacion: ConfirmacionPendiente? = null
        var yaRefresco = false
        val idempotencyKey = path.takeIf { it.endsWith("/messages") }?.let { UUID.randomUUID().toString() }

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
                    when (evento) {
                        is ChatEvent.TextDelta -> textoAcumulado.append(evento.text)
                        is ChatEvent.ConfirmationRequired ->
                            confirmacion = ConfirmacionPendiente(
                                evento.toolCallId,
                                evento.name,
                                formatearArgumentos(evento.args),
                            )
                        is ChatEvent.ErrorEvent -> errorDelTurno = evento.message
                        else -> Unit
                    }
                }
                break
            } catch (e: SseClient.SseException.Servidor) {
                if (e.status != 401 || yaRefresco) throw e
                yaRefresco = true
                api.refrescar()
            }
        }

        errorDelTurno?.let { throw IllegalStateException(it) }
        return ResultadoTurnoVoz(textoAcumulado.toString(), confirmacion)
    }

    private fun formatearArgumentos(args: JsonElement): String {
        val objeto = args as? JsonObject ?: return ""
        return objeto.entries.joinToString(" · ") { (clave, valor) ->
            val texto = (valor as? JsonPrimitive)?.contentOrNull ?: valor.toString()
            "${clave.replace('_', ' ')}: $texto"
        }
    }

    private suspend fun reproducir(texto: String, api: EdecanApi) {
        if (_uiState.value.ttsDelDispositivo) {
            reproducirConVozDelDispositivo(texto)
            return
        }
        try {
            val audio = api.hablar(texto)
            reproducirAudio(audio)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            _uiState.update { it.copy(errorMensaje = "No se pudo reproducir la respuesta: ${e.message}") }
        }
    }

    private fun reproducirConVozDelDispositivo(texto: String) {
        val contexto = getApplication<Application>()
        ttsLocal?.stop()
        ttsLocal?.shutdown()
        _uiState.update { it.copy(reproduciendo = true) }
        lateinit var engine: TextToSpeech
        engine = TextToSpeech(contexto) { status ->
            if (status != TextToSpeech.SUCCESS) {
                _uiState.update {
                    it.copy(reproduciendo = false, errorMensaje = "Android no pudo iniciar la voz del dispositivo.")
                }
                engine.shutdown()
                if (ttsLocal === engine) ttsLocal = null
                return@TextToSpeech
            }
            val languageStatus = engine.setLanguage(Locale.getDefault())
            if (languageStatus == TextToSpeech.LANG_MISSING_DATA ||
                languageStatus == TextToSpeech.LANG_NOT_SUPPORTED
            ) {
                _uiState.update {
                    it.copy(reproduciendo = false, errorMensaje = "La voz del dispositivo no admite tu idioma actual.")
                }
                engine.shutdown()
                if (ttsLocal === engine) ttsLocal = null
                return@TextToSpeech
            }
            val utteranceId = "edecan-${System.currentTimeMillis()}"
            engine.setOnUtteranceProgressListener(object : UtteranceProgressListener() {
                override fun onStart(utteranceId: String?) = Unit
                override fun onDone(utteranceId: String?) = terminarTts(engine)
                @Deprecated("Deprecated in Java")
                override fun onError(utteranceId: String?) = fallarTts(engine)
                override fun onError(utteranceId: String?, errorCode: Int) = fallarTts(engine)
            })
            if (engine.speak(texto, TextToSpeech.QUEUE_FLUSH, null, utteranceId) == TextToSpeech.ERROR) {
                fallarTts(engine)
            }
        }
        ttsLocal = engine
    }

    private fun terminarTts(engine: TextToSpeech) {
        engine.shutdown()
        if (ttsLocal === engine) ttsLocal = null
        _uiState.update { it.copy(reproduciendo = false) }
    }

    private fun fallarTts(engine: TextToSpeech) {
        engine.shutdown()
        if (ttsLocal === engine) ttsLocal = null
        _uiState.update {
            it.copy(reproduciendo = false, errorMensaje = "Android no pudo leer la respuesta en voz alta.")
        }
    }

    private suspend fun reproducirAudio(audio: VoiceAudio) {
        val contexto = getApplication<Application>()
        val extension = if (audio.contentType.contains("wav", ignoreCase = true)) "wav" else "mp3"
        val archivo = File(contexto.cacheDir, "voz_respuesta_${System.currentTimeMillis()}.$extension")
        withContext(Dispatchers.IO) { archivo.writeBytes(audio.bytes) }

        _uiState.update { it.copy(reproduciendo = true) }
        val player = MediaPlayer()
        reproductorRespuesta?.let { anterior ->
            anterior.setOnCompletionListener(null)
            anterior.setOnErrorListener(null)
            anterior.release()
        }
        archivoRespuestaVoz?.delete()
        reproductorRespuesta = player
        archivoRespuestaVoz = archivo

        fun terminar() {
            if (reproductorRespuesta !== player) {
                archivo.delete()
                return
            }
            player.release()
            reproductorRespuesta = null
            archivo.delete()
            if (archivoRespuestaVoz === archivo) archivoRespuestaVoz = null
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
        liberarReconocimientoLocal()
        ttsLocal?.stop()
        ttsLocal?.shutdown()
        ttsLocal = null
        reproductorRespuesta?.let { player ->
            player.setOnCompletionListener(null)
            player.setOnErrorListener(null)
            runCatching { player.stop() }
            player.release()
        }
        reproductorRespuesta = null
        archivoRespuestaVoz?.delete()
        archivoRespuestaVoz = null
        conversationId = null
        yaVerificoVoz = false
        _uiState.value = VozUiState()
    }
}
