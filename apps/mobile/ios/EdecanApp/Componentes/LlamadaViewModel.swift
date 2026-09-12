import AVFoundation
import EdecanKit
import Foundation
import Observation
import UIKit

/// Llamada continua con Edecán — modo "siempre escuchando": al entrar, el
/// micrófono queda abierto y la pantalla escucha sin parar hasta tocar la X.
/// La transcripción del usuario aparece EN VIVO (interims `transcript.partial`
/// estilo ChatGPT) y, cuando Edecán responde, empieza a HABLAR de inmediato
/// (PCM en streaming, sin esperar el mp3 completo). Durante la respuesta se
/// bloquea la entrada para no transcribir el altavoz; el orb permite interrumpir.
///
/// El cerebro es TODO el pipeline del chat (`chat.enviar`, tools incluidas).
/// La voz es la que el usuario eligió en ``VocesView`` (`vozId`).
///
/// El transporte `WS /v1/voice/realtime` se abre una vez al entrar y se
/// mantiene durante toda la llamada. Un único loop de recepción enruta los
/// eventos del socket (parciales, final, chunks `audio/pcm24`, `done`) para no
/// tener dos `receive()` concurrentes sobre la misma conexión — `URLSessionWebSocketTask`
/// es de a un mensaje por vez. Si el servidor no soporta STT live
/// (`stt_live == false`), el turno se captura como una sola toma (acumulando
/// el PCM16 del micrófono continuo y envolviéndolo en WAV) y se transcribe con
/// ``TranscripcionVoz``; si no soporta TTS PCM (`tts_pcm == false`), se cae a
/// `hablarStream` + ``ReproductorMPEGStream``.
@MainActor
@Observable
final class LlamadaViewModel {
    enum Estado: Equatable {
        case inactivo
        case inicializando
        case escuchando
        case transcribiendo
        case pensando
        case hablando
    }

    var chat: ChatViewModel?
    /// Voz de ElevenLabs elegida por el usuario (`@AppStorage("vozElegidaId")`
    /// en la vista). La vista la copia acá al aparecer y cuando cambia.
    var vozId = "0uHpKhb0ymsdvmCtPV8y"

    private let recorderContinuo = VozRecorderContinuo()
    private let reconocedorLocal = ReconocedorVozLocal()
    private let reproductorPCM = ReproductorPCMStream()
    private let reproductorMPEG = ReproductorMPEGStream()
    private let vozLocal = VozRespuestaLocal()
    private var ttsConectado = false
    private var generacion: UInt64 = 0
    private var bytesRespuesta = 0
    private var turnoSalida: Int?
    private var turnoEntrada: Int?

    private(set) var estado: Estado = .inactivo
    /// Texto en vivo del turno del usuario (interim `transcript.partial`).
    private(set) var textoParcial: String?
    private(set) var ultimaTranscripcion: String?
    private(set) var ultimaRespuesta: String?
    var errorMensaje: String?
    /// Solo vive en memoria y se elimina al enviar el texto, descartar o cerrar.
    private var audioPendiente: Data?
    var puedeReintentar: Bool { audioPendiente != nil && estado == .escuchando }
    /// RMS (0…1) del último chunk de micrófono, para la onda de la pantalla.
    private(set) var nivelEntrada: Float = 0

    // Configuración de VAD: puerta de ruido adaptativa + rachas sostenidas (ver
    // ``VadAdaptativo``). El micrófono "solo se enfoca en la voz": el umbral
    // sube con el ruido de fondo del ambiente y un turno necesita voz real.
    private var vad = VadAdaptativo()
    private let maxSegundosTurno: TimeInterval = 30
    private var puertaEco = PuertaEcoVoz()

    // Transporte y estado interno de la llamada.
    private var realtime: RealtimeVoiceClient?
    private var sttLive = false
    private var ttsPCM = false
    private var sttConectado = false
    private var streamAudio: AsyncThrowingStream<ChunkAudioPCM16, Error>?

    private var tareaPrincipal: Task<Void, Never>?
    private var tareaAudio: Task<Void, Never>?
    private var tareaRecepcion: Task<Void, Never>?
    private var tareaTurno: Task<Void, Never>?
    private var tareaVozRespaldo: Task<Void, Never>?
    private var continuacionFinal: CheckedContinuation<String, Error>?
    private var tareaTimeoutFinal: Task<Void, Never>?
    /// Final que llegó antes de que `esperarTranscripcionFinal` registrara su
    /// continuación (el `commit` y la respuesta pueden cruzarse): se guarda
    /// acá y la espera lo consume de inmediato. Sin esto, el turno se perdía.
    private var transcriptPendiente: String?
    /// El turno descartado por ruido también dispara un `transcript` en el
    /// servidor; esta bandera lo tira para que no lo recoja el turno REAL
    /// siguiente desde `transcriptPendiente`.
    private var ignorarSiguienteTranscript = false
    nonisolated(unsafe) private var tokensAudio: [NSObjectProtocol] = []

    init() {
        observarInterrupcionesDeAudio()
    }

    deinit {
        for token in tokensAudio {
            NotificationCenter.default.removeObserver(token)
        }
    }

    /// Entra a la llamada: pide permiso, abre el WS realtime (best effort),
    /// arranca el micrófono continuo y los loops de escucha/recepción.
    func iniciar(client: APIClient?) {
        guard estado == .inactivo else { return }
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        estado = .inicializando
        generacion &+= 1
        vad = VadAdaptativo()
        puertaEco = PuertaEcoVoz()
        errorMensaje = nil
        audioPendiente = nil
        textoParcial = nil
        ultimaTranscripcion = nil
        ultimaRespuesta = nil
        tareaPrincipal = Task { [weak self] in
            await self?.arrancar(client: client)
        }
    }

    /// Corta TODO: micrófono continuo, players, WS y tareas en vuelo.
    func terminarLlamada() {
        generacion &+= 1
        tareaPrincipal?.cancel()
        tareaPrincipal = nil
        tareaAudio?.cancel()
        tareaAudio = nil
        tareaRecepcion?.cancel()
        tareaRecepcion = nil
        tareaTurno?.cancel()
        tareaTurno = nil
        tareaVozRespaldo?.cancel()
        tareaVozRespaldo = nil
        tareaTimeoutFinal?.cancel()
        tareaTimeoutFinal = nil
        if let continuacion = continuacionFinal {
            continuacionFinal = nil
            continuacion.resume(throwing: CancellationError())
        }
        transcriptPendiente = nil
        ignorarSiguienteTranscript = false
        recorderContinuo.detener()
        reproductorPCM.detener()
        reproductorMPEG.detener()
        vozLocal.detener()
        turnoSalida = nil
        turnoEntrada = nil
        if let realtime {
            realtime.close()
        }
        realtime = nil
        streamAudio = nil
        textoParcial = nil
        audioPendiente = nil
        errorMensaje = nil
        estado = .inactivo
        do { try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation) }
        catch { registrarFallo(error, etapa: "close_audio_session") }
    }

    private func arrancar(client: APIClient) async {
        guard !Task.isCancelled else { return }
        let permitido = await recorderContinuo.solicitarPermiso()
        guard !Task.isCancelled else { return }
        guard permitido else {
            estado = .inactivo
            errorMensaje = VozRecorderContinuo.ErrorRecorder.permisoDenegado.errorDescription
            return
        }

        // Capacidades del tenant para los fallbacks de transcripción.
        let credenciales = try? await client.credenciales()
        guard !Task.isCancelled else { return }
        sttConectado = credenciales?.voiceStt != nil
        ttsConectado = credenciales?.voiceTts != nil

        // Abrir el WS realtime y leer sus capacidades del `ready`.
        var realtimeAbierto: RealtimeVoiceClient?
        var sttLiveDetectado = false
        var ttsPCMDetectado = false
        do {
            let rt = try await client.abrirVozRealtime()
            let ready = try await rt.connect()
            if ready.type == "ready" {
                sttLiveDetectado = ready.sttLive ?? false
                ttsPCMDetectado = ready.ttsPCM ?? false
                realtimeAbierto = rt
            } else {
                rt.close()
            }
        } catch {
            // Sin WS (backend viejo / sin red): todo cae a los fallbacks HTTP.
            realtimeAbierto = nil
        }

        realtime = realtimeAbierto
        guard !Task.isCancelled else {
            realtimeAbierto?.close()
            realtime = nil
            return
        }
        sttLive = sttLiveDetectado
        ttsPCM = ttsPCMDetectado

        do {
            let stream = try recorderContinuo.iniciar()
            streamAudio = stream
        } catch {
            estado = .inactivo
            registrarFallo(error, etapa: "start_capture")
            errorMensaje = "No pude activar el micrófono. Inténtalo de nuevo."
            return
        }

        estado = .escuchando
        iniciarBucleAudio(client: client)
        if realtime != nil {
            iniciarRecepcion()
        }
    }

    // MARK: - Loop de escucha (VAD)

    private func iniciarBucleAudio(client: APIClient) {
        tareaAudio?.cancel()
        tareaAudio = Task { [weak self] in
            do {
                try await self?.bucleDeEscucha(client: client)
            } catch {
                guard let self, !Task.isCancelled else { return }
                self.terminarLlamada()
                self.registrarFallo(error, etapa: "capture_stream")
                self.errorMensaje = "Se interrumpió la escucha. Activa el micrófono de nuevo."
            }
        }
    }

    /// Consume el stream del micrófono continuo. El VAD solo decide CUÁNDO
    /// abre y cierra el turno; dentro del turno se envía TODO el audio (voz y
    /// silencio — recortar por umbral mutilaba sílabas y pausas). Un pre-roll
    /// de ~400 ms conserva el arranque de la frase que disparó el turno.
    private func bucleDeEscucha(client: APIClient) async throws {
        guard let stream = streamAudio else { return }
        var turnoActivo = false
        var inicioTurno = Date()
        var acumuladoPCM = Data()
        var framesEnviados = 0
        var preRoll: [Data] = []

        for try await chunk in stream {
            if Task.isCancelled { break }
            let ahora = ProcessInfo.processInfo.systemUptime
            // Bloquear ANTES del VAD y del pre-roll: de otro modo el propio
            // TTS se convierte en "voz reciente" y abre un turno al terminar.
            guard estado == .escuchando,
                  puertaEco.acepta(capturadoEn: chunk.capturadoEn, ahora: ahora),
                  !(audioPendiente != nil && errorMensaje != nil),
                  chat?.confirmacionPendiente == nil,
                  chat?.estaGenerando != true
            else {
                if turnoActivo, sttLive, let realtime {
                    do { try await realtime.interrupt() }
                    catch { degradarTransporte(motivo: "No se pudo descartar el turno de entrada.") }
                }
                nivelEntrada = 0
                vad.reiniciarTurno()
                preRoll.removeAll()
                turnoActivo = false
                acumuladoPCM.removeAll()
                framesEnviados = 0
                continue
            }
            nivelEntrada = chunk.rms
            _ = vad.alimentar(rms: chunk.rms)

            preRoll.append(chunk.datos)
            if preRoll.count > 4 { preRoll.removeFirst() }


            if !turnoActivo {
                if vad.vozReciente {
                    turnoActivo = true
                    errorMensaje = nil
                    inicioTurno = Date()
                    textoParcial = nil
                    // Pre-roll: los chunks que dispararon el turno también son
                    // parte de él — sin esto se perdía el arranque de la frase.
                    acumuladoPCM = Data(preRoll.joined())
                    framesEnviados = 0
                    turnoEntrada = nil
                    for trozo in preRoll where sttLive {
                        await enviarFrame(trozo, contador: &framesEnviados)
                    }
                }
                continue
            }

            // Dentro del turno va TODO el audio; Deepgram segmenta solo.
            acumuladoPCM.append(chunk.datos)
            if sttLive {
                await enviarFrame(chunk.datos, contador: &framesEnviados)
            }

            let excedioTurno = Date().timeIntervalSince(inicioTurno) >= maxSegundosTurno
            let cerroPorSilencio = vad.silencioMs >= VadAdaptativo.silencioMaxMs
            if excedioTurno || cerroPorSilencio {
                await cerrarTurno(
                    client: client, acumulado: acumuladoPCM,
                    framesEnviados: framesEnviados
                )
                turnoActivo = false
                acumuladoPCM = Data()
                framesEnviados = 0
                vad.reiniciarTurno()
                preRoll.removeAll()
            }
        }
    }

    /// Envía un frame por el WS; si el transporte falla, degrada al camino
    /// WAV/HTTP de una sola toma (el audio acumulado localmente es el
    /// respaldo completo del turno).
    private func enviarFrame(_ datos: Data, contador: inout Int) async {
        guard let realtime else { return }
        do {
            try await realtime.sendPCM16(datos)
            contador += 1
        } catch {
            degradarTransporte(motivo: "Se cayó la conexión de voz en vivo.")
        }
    }

    /// WS muerto: la llamada NO muere — sigue por los caminos de respaldo
    /// (transcripción WAV de una toma + `hablarStream` HTTP). Solo se avisa
    /// una vez para no llenar la pantalla del mismo error.
    private func degradarTransporte(motivo: String) {
        guard sttLive || ttsPCM else { return }
        NSLog("Voice transport fallback: %@", motivo)
        sttLive = false
        ttsPCM = false
        realtime?.close()
        realtime = nil
        transcriptPendiente = nil
        if let continuacion = continuacionFinal {
            continuacionFinal = nil
            tareaTimeoutFinal?.cancel()
            tareaTimeoutFinal = nil
            continuacion.resume(throwing: ErrorLlamada.conexionCerrada)
        }
        if estado == .hablando {
            reproductorPCM.detener()
            reproductorMPEG.detener()
            terminarHabla()
        }
    }

    /// Cierra el turno actual. Con STT live manda `commit` (el `transcript`
    /// final llega por el loop de recepción; si llega antes de que la espera
    /// se registre, queda en `transcriptPendiente`); con fallo de commit o sin
    /// STT live transcribe la toma acumulada con ``TranscripcionVoz``. Los
    /// turnos de puro ruido se descartan sin esperar nada.
    private func cerrarTurno(client: APIClient, acumulado: Data, framesEnviados: Int) async {
        guard vad.turnoValido else {
            if sttLive, let realtime, framesEnviados > 0 {
                // Cierra el turno abierto en el servidor (limpia su buffer);
                // el `transcript` que emita se ignora porque nadie lo espera.
                // La bandera solo se fija si el commit SALIÓ: si falló, el
                // transcript que ignoraría sería el de un turno real.
                do {
                    // No producir una transcripcion huérfana de un turno descartado.
                    try await realtime.interrupt()
                } catch {
                    degradarTransporte(motivo: "Se cayó la conexión de voz en vivo.")
                }
            }
            return
        }
        tareaTurno?.cancel()
        tareaTurno = nil
        audioPendiente = acumulado
        reproductorPCM.detener()
        reproductorMPEG.detener()
        transcriptPendiente = nil

        if sttLive, let realtime, framesEnviados > 0 {
            estado = .transcribiendo
            do {
                try await realtime.commit()
            } catch {
                degradarTransporte(motivo: "Se cayó la conexión de voz en vivo.")
            }
        }
        if sttLive, realtime != nil {
            tareaTurno = Task { [weak self] in
                await self?.procesarTurnoLive(client: client, acumulado: acumulado)
            }
        } else {
            // Respaldo completo: TODO el audio del turno se acumuló local.
            estado = .transcribiendo
            tareaTurno = Task { [weak self] in
                await self?.procesarTurnoWAV(client: client, acumulado: acumulado)
            }
        }
    }

    // MARK: - Turno: transcripción → agente → TTS

    func reintentarTurno(client: APIClient?) {
        guard puedeReintentar, let audio = audioPendiente, let client else { return }
        errorMensaje = nil
        estado = .transcribiendo
        tareaTurno = Task { [weak self] in
            await self?.procesarTurnoWAV(client: client, acumulado: audio)
        }
    }

    func descartarTurno() {
        guard estado == .escuchando || estado == .inactivo else { return }
        audioPendiente = nil
        errorMensaje = nil
        textoParcial = nil
        vad.reiniciarTurno()
    }

    private func registrarFallo(_ error: Error, etapa: String) {
        // Sin textos de usuario, URLs, cabeceras ni secretos del proveedor.
        NSLog("Voice failure stage=%@ code=%ld", etapa, (error as NSError).code)
    }

    private func procesarTurnoLive(client: APIClient, acumulado: Data) async {
        do {
            let texto = try await esperarTranscripcionFinal()
            await procesarTextoFinal(texto, client: client)
        } catch is CancellationError {
            return
        } catch {
            guard estado == .transcribiendo || estado == .pensando || estado == .hablando else { return }
            degradarTransporte(motivo: "Reintentando la transcripción del turno por HTTP.")
            await procesarTurnoWAV(client: client, acumulado: acumulado)
        }
    }

    private func procesarTurnoWAV(client: APIClient, acumulado: Data) async {
        guard !acumulado.isEmpty else {
            estado = .escuchando
            return
        }
        do {
            let wav = Self.wavDesdePCM16(acumulado)
            let texto = try await TranscripcionVoz.transcribir(
                audio: wav,
                client: client,
                sttConectado: sttConectado,
                reconocedorLocal: reconocedorLocal
            )
            await procesarTextoFinal(texto, client: client)
        } catch is CancellationError {
            return
        } catch {
            guard estado == .transcribiendo || estado == .pensando || estado == .hablando else { return }
            registrarFallo(error, etapa: "transcribe_fallback")
            errorMensaje = "No pude procesarlo. Puedes reintentar sin repetirlo."
            estado = .escuchando
        }
    }

    /// Con el texto final del turno: corre el chat completo (tools incluidas)
    /// y manda la respuesta a hablar por el transporte que toque.
    private func procesarTextoFinal(_ texto: String, client: APIClient) async {
        guard !Task.isCancelled, estado != .inactivo else { return }
        let ciclo = generacion
        errorMensaje = nil
        let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoLimpio.isEmpty else {
            ultimaTranscripcion = nil
            textoParcial = nil
            estado = .escuchando
            errorMensaje = "No pude entenderlo. Puedes reintentar sin repetirlo."
            return
        }
        ultimaTranscripcion = textoLimpio
        ultimaRespuesta = nil
        textoParcial = nil
        estado = .pensando
        // Desde aqui el chat conserva el mensaje y su intento idempotente.
        audioPendiente = nil

        let enviado = await chat?.enviar(texto: textoLimpio, client: client) ?? false
        guard !Task.isCancelled, generacion == ciclo, estado != .inactivo else { return }
        guard enviado else {
            errorMensaje = "No pude completar el mensaje. Quedó en el chat para reintentar."
            if !Task.isCancelled { estado = .escuchando }
            return
        }
        // Edecán pidió aprobación de una tool: la tarjeta se muestra en la
        // pantalla y la llamada espera la decisión (el loop no abre turnos
        // mientras haya una confirmación pendiente).
        if chat?.confirmacionPendiente != nil {
            if let pregunta = chat?.ultimaRespuestaHablada,
               !pregunta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                ultimaRespuesta = pregunta
                await hablarRespuesta(pregunta, client: client)
            } else {
                estado = .escuchando
            }
            return
        }
        guard let respuesta = chat?.ultimaRespuestaHablada,
              !respuesta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            errorMensaje = "La respuesta está en el chat, pero no pude leerla en voz alta."
            if !Task.isCancelled { estado = .escuchando }
            return
        }
        ultimaRespuesta = respuesta
        await hablarRespuesta(respuesta, client: client)
    }

    /// Resuelve la confirmación pendiente de una tool (Aprobar/Rechazar de la
    /// tarjeta) y retoma el turno: la respuesta de Edecán se habla igual que
    /// cualquier otra.
    func resolverConfirmacion(aprobado: Bool, client: APIClient) async {
        guard chat?.confirmacionPendiente != nil else { return }
        let ciclo = generacion
        reproductorPCM.detener()
        reproductorMPEG.detener()
        vozLocal.detener()
        estado = .pensando
        await chat?.resolverConfirmacion(aprobado: aprobado, client: client)
        guard !Task.isCancelled, generacion == ciclo, estado != .inactivo else { return }
        guard let respuesta = chat?.ultimaRespuestaHablada,
              !respuesta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            estado = .escuchando
            return
        }
        ultimaRespuesta = respuesta
        await hablarRespuesta(respuesta, client: client)
    }

    /// Sintetiza y reproduce la respuesta. Con `tts_pcm` usa el WS en streaming
    /// (chunks pcm24 → ``ReproductorPCMStream``, habla de inmediato); sin él
    /// cae a `hablarStream` + ``ReproductorMPEGStream``.
    private func hablarRespuesta(_ respuesta: String, client: APIClient) async {
        guard !Task.isCancelled, estado != .inactivo else { return }
        let ciclo = generacion
        let textoHablar = SpeechTags.ocultar(respuesta).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoHablar.isEmpty else {
            estado = .escuchando
            return
        }
        estado = .hablando
        puertaEco.comenzarSalida()
        bytesRespuesta = 0
        turnoSalida = nil
        if !ttsConectado {
            do {
                try await vozLocal.hablar(textoHablar)
                guard !Task.isCancelled, generacion == ciclo else { return }
                terminarHabla()
            } catch is CancellationError {
                return
            } catch {
                guard generacion == ciclo else { return }
                registrarFallo(error, etapa: "local_speech")
                errorMensaje = "No pude reproducir la respuesta. El texto está en el chat."
                terminarHabla()
            }
            return
        }
        if ttsPCM, let realtime {
            // El loop de recepción enruta los chunks pcm24 al reproductor y el
            // `done` llama `finalizar()`, que dispara `alTerminar` al drenar.
            reproductorPCM.alTerminar = { [weak self] in
                guard let self, self.generacion == ciclo else { return }
                self.terminarHabla()
            }
            reproductorPCM.alError = { [weak self] error in
                guard let self, self.generacion == ciclo else { return }
                self.registrarFallo(error, etapa: "pcm_playback")
                self.errorMensaje = "No pude reproducir la respuesta. El texto está en el chat."
                self.reproductorPCM.detener()
                self.terminarHabla()
            }
            do {
                try await realtime.speak(
                    text: textoHablar, voiceId: vozId,
                    modelId: "eleven_turbo_v2_5", pcm: true
                )
            } catch is CancellationError {
                if generacion == ciclo { terminarHabla() }
                return
            } catch {
                registrarFallo(error, etapa: "tts_socket")
                if generacion == ciclo { recuperarVozLocal(textoHablar) }
            }
        } else {
            do {
                let stream = try await client.hablarStream(
                    texto: textoHablar, voiceId: vozId, modelId: "eleven_turbo_v2_5"
                )
                _ = try await reproductorMPEG.reproducir(stream: stream, mantenerMicrofono: true)
                if !Task.isCancelled, generacion == ciclo { terminarHabla() }
            } catch is CancellationError {
                if generacion == ciclo { terminarHabla() }
                return
            } catch {
                registrarFallo(error, etapa: "tts_http")
                if generacion == ciclo { recuperarVozLocal(textoHablar) }
            }
        }
    }

    /// Aviso del reproductor PCM cuando drena todo lo encolado: vuelve a
    /// escuchar.
    private func terminarHabla() {
        if estado == .hablando {
            puertaEco.terminarSalida(ahora: ProcessInfo.processInfo.systemUptime)
            vad.reiniciarTurno()
            estado = .escuchando
        }
    }

    /// Un fallo de red antes del audio no tiene por que dejar la respuesta muda.
    private func recuperarVozLocal(_ texto: String) {
        guard estado == .hablando, tareaVozRespaldo == nil else { return }
        let ciclo = generacion
        turnoSalida = nil
        reproductorPCM.detener()
        reproductorMPEG.detener()
        tareaVozRespaldo = Task { [weak self] in
            guard let self else { return }
            do {
                try await self.vozLocal.hablar(texto)
                guard !Task.isCancelled, self.generacion == ciclo else { return }
                self.errorMensaje = nil
                self.terminarHabla()
            } catch is CancellationError {
                return
            } catch {
                guard self.generacion == ciclo else { return }
                self.registrarFallo(error, etapa: "local_speech_recovery")
                self.errorMensaje = "No pude reproducir la respuesta. El texto está en el chat."
                self.terminarHabla()
            }
            if self.generacion == ciclo { self.tareaVozRespaldo = nil }
        }
    }

    /// Interrupcion manual del orb: no se dispara por energia del microfono.
    private func bargeIn() {
        let ciclo = generacion
        tareaVozRespaldo?.cancel()
        tareaVozRespaldo = nil
        tareaTurno?.cancel()
        tareaTurno = nil
        reproductorPCM.detener()
        reproductorMPEG.detener()
        vozLocal.detener()
        turnoSalida = nil
        // Avisa al servidor para que pare el TTS viejo y sane el turno abierto
        // (descarta el audio a medias y su pump) antes del próximo frame.
        if let realtime {
            // Esperar el envio antes de reabrir la entrada mantiene el orden
            // interrupt -> primer audio del turno nuevo en el mismo socket.
            Task { [weak self] in
                do { try await realtime.interrupt() }
                catch {
                    guard let self, self.generacion == ciclo else { return }
                    self.degradarTransporte(motivo: "Interrupción del audio")
                }
                guard let self, self.generacion == ciclo else { return }
                self.terminarHabla()
            }
        } else {
            terminarHabla()
        }
        ultimaRespuesta = nil
        transcriptPendiente = nil
        ignorarSiguienteTranscript = false
        if let continuacion = continuacionFinal {
            continuacionFinal = nil
            tareaTimeoutFinal?.cancel()
            tareaTimeoutFinal = nil
            continuacion.resume(throwing: CancellationError())
        }
    }

    func interrumpirDesdeChat() {
        if estado == .hablando { bargeIn() }
    }

    // MARK: - Recepción del WS (loop unificado)

    private func iniciarRecepcion() {
        tareaRecepcion?.cancel()
        tareaRecepcion = Task { [weak self] in
            await self?.bucleDeRecepcion()
        }
    }

    private func bucleDeRecepcion() async {
        guard let realtime else { return }
        do {
            while !Task.isCancelled {
                let evento = try await realtime.receive()
                manejarEvento(evento)
            }
        } catch is CancellationError {
            return
        } catch {
            // El WS murió: la llamada SIGUE por los caminos de respaldo
            // (WAV de una toma + `hablarStream` HTTP). Sin esto el micrófono
            // seguía abierto sin canal operativo.
            degradarTransporte(motivo: "Se perdió la conexión de voz en vivo.")
        }
    }

    private func manejarEvento(_ evento: RealtimeVoiceEvent) {
        guard estado != .inactivo else { return }
        switch evento.type {
        case "speaking":
            if estado == .hablando { turnoSalida = evento.turnId }
        case "audio.accepted":
            if estado == .escuchando || estado == .transcribiendo { turnoEntrada = evento.turnId }
        case "transcript.partial":
            if evento.turnId == turnoEntrada, let texto = evento.text { textoParcial = texto }
        case "transcript":
            guard estado == .transcribiendo, evento.turnId == turnoEntrada else { return }
            if let texto = evento.text {
                textoParcial = nil
                // El turno descartado por ruido también produce un `transcript`:
                // se tira para que no lo recoja el turno real siguiente.
                if ignorarSiguienteTranscript {
                    ignorarSiguienteTranscript = false
                    return
                }
                if let continuacion = continuacionFinal {
                    continuacionFinal = nil
                    tareaTimeoutFinal?.cancel()
                    tareaTimeoutFinal = nil
                    ultimaTranscripcion = texto
                    continuacion.resume(returning: texto)
                } else {
                    // Llegó antes de que la espera se registrara (el commit y
                    // la respuesta se cruzaron): queda acá para que la espera
                    // lo consuma de inmediato. Sin esto el turno se perdía.
                    transcriptPendiente = texto
                }
            }
        case "audio" where evento.mime == "audio/pcm24":
            if estado == .hablando, evento.turnId == turnoSalida, let audio = evento.audio {
                bytesRespuesta += audio.count
                reproductorPCM.agregar(audio)
            }
        case "done":
            if estado == .hablando, evento.turnId == turnoSalida {
                if bytesRespuesta == 0 {
                    if let respuesta = ultimaRespuesta { recuperarVozLocal(respuesta) }
                    else { terminarHabla() }
                    return
                }
                reproductorPCM.finalizar()
            }
        case "error":
            if let continuacion = continuacionFinal {
                continuacionFinal = nil
                tareaTimeoutFinal?.cancel()
                tareaTimeoutFinal = nil
                continuacion.resume(throwing: ErrorLlamada.transcripcionFallida)
            } else {
                if estado == .hablando, bytesRespuesta == 0, let respuesta = ultimaRespuesta {
                    recuperarVozLocal(respuesta)
                } else if estado == .hablando {
                    errorMensaje = "No pude completar la respuesta de voz. El texto está en el chat."
                    reproductorPCM.detener()
                    terminarHabla()
                } else if estado == .transcribiendo {
                    // La tarea del turno conserva el audio y hara el reintento.
                    degradarTransporte(motivo: "Error antes del resultado de transcripción")
                }
            }
        default:
            break
        }
    }

    /// Espera (con timeout) el `transcript` final tras el `commit`. Primero
    /// consume el que ya haya llegado adelantado (`transcriptPendiente`).
    private func esperarTranscripcionFinal() async throws -> String {
        if let pendiente = transcriptPendiente {
            transcriptPendiente = nil
            return pendiente
        }
        return try await withCheckedThrowingContinuation { continuation in
            continuacionFinal = continuation
            tareaTimeoutFinal?.cancel()
            tareaTimeoutFinal = Task { [weak self] in
                do { try await Task.sleep(for: .seconds(20)) }
                catch { return }
                guard let self, let pendiente = self.continuacionFinal else { return }
                self.continuacionFinal = nil
                pendiente.resume(throwing: ErrorLlamada.tiempoAgotado)
            }
        }
    }

    // MARK: - Utilidades

    /// Envuelve PCM16 s16le 16 kHz mono en un contenedor WAV (44 bytes RIFF),
    /// el mismo formato que produce ``VozRecorder`` y que esperan los
    /// endpoints de transcripción. Así el fallback "una toma" no necesita
    /// correr un segundo `AVAudioEngine` con otro tap del input.
    private static func wavDesdePCM16(_ pcm: Data) -> Data {
        let sampleRate: UInt32 = 16_000
        let bitsPorMuestra: UInt16 = 16
        let canales: UInt16 = 1
        let byteRate = sampleRate * UInt32(canales) * UInt32(bitsPorMuestra / 8)
        let blockAlign = canales * (bitsPorMuestra / 8)
        let dataSize = UInt32(pcm.count)

        var wav = Data()
        wav.append(contentsOf: Array("RIFF".utf8))
        appendLittleEndian(UInt32(36) + dataSize, to: &wav)
        wav.append(contentsOf: Array("WAVE".utf8))
        wav.append(contentsOf: Array("fmt ".utf8))
        appendLittleEndian(UInt32(16), to: &wav)
        appendLittleEndian(UInt16(1), to: &wav) // PCM
        appendLittleEndian(canales, to: &wav)
        appendLittleEndian(sampleRate, to: &wav)
        appendLittleEndian(byteRate, to: &wav)
        appendLittleEndian(blockAlign, to: &wav)
        appendLittleEndian(bitsPorMuestra, to: &wav)
        wav.append(contentsOf: Array("data".utf8))
        appendLittleEndian(dataSize, to: &wav)
        wav.append(pcm)
        return wav
    }

    private static func appendLittleEndian<T: FixedWidthInteger>(_ valor: T, to data: inout Data) {
        var v = valor.littleEndian
        withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
    }

    private func observarInterrupcionesDeAudio() {
        let center = NotificationCenter.default
        let interruption = center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            guard rawType == AVAudioSession.InterruptionType.began.rawValue else { return }
            Task { @MainActor [weak self] in
                self?.terminarLlamada()
            }
        }
        let background = center.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.terminarLlamada()
            }
        }
        tokensAudio = [interruption, background]
    }
}

enum ErrorLlamada: LocalizedError {
    case tiempoAgotado
    case transcripcionFallida
    case conexionCerrada

    var errorDescription: String? {
        switch self {
        case .tiempoAgotado:
            return "No se entendió nada en el tiempo esperado."
        case .transcripcionFallida:
            return "No se pudo transcribir el audio."
        case .conexionCerrada:
            return "Se perdió la conexión de voz."
        }
    }
}
