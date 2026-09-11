import AVFoundation
import EdecanKit
import Foundation
import Observation
import UIKit

/// Llamada continua con Edecán — modo "siempre escuchando": al entrar, el
/// micrófono queda abierto y la pantalla escucha sin parar hasta tocar la X.
/// La transcripción del usuario aparece EN VIVO (interims `transcript.partial`
/// estilo ChatGPT) y, cuando Edecán responde, empieza a HABLAR de inmediato
/// (PCM en streaming, sin esperar el mp3 completo) con barge-in: si el usuario
/// habla mientras Edecán responde, se corta la voz y entra su turno.
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

    private(set) var estado: Estado = .inactivo
    /// Texto en vivo del turno del usuario (interim `transcript.partial`).
    private(set) var textoParcial: String?
    private(set) var ultimaTranscripcion: String?
    private(set) var ultimaRespuesta: String?
    var errorMensaje: String?
    /// RMS (0…1) del último chunk de micrófono, para la onda de la pantalla.
    private(set) var nivelEntrada: Float = 0

    // Configuración de VAD (s16le normalizado: RMS relativo a escala completa).
    private let umbralHabla: Float = 0.02
    private let silencioMaxMs = 800
    private let maxSegundosTurno: TimeInterval = 30

    // Transporte y estado interno de la llamada.
    private var realtime: RealtimeVoiceClient?
    private var sttLive = false
    private var ttsPCM = false
    private var sttConectado = false
    private var streamAudio: AsyncStream<ChunkAudioPCM16>?

    private var tareaPrincipal: Task<Void, Never>?
    private var tareaAudio: Task<Void, Never>?
    private var tareaRecepcion: Task<Void, Never>?
    private var tareaTurno: Task<Void, Never>?
    private var continuacionFinal: CheckedContinuation<String, Error>?
    private var tareaTimeoutFinal: Task<Void, Never>?
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
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        estado = .inicializando
        errorMensaje = nil
        textoParcial = nil
        ultimaTranscripcion = nil
        ultimaRespuesta = nil
        tareaPrincipal = Task { [weak self] in
            await self?.arrancar(client: client)
        }
    }

    /// Corta TODO: micrófono continuo, players, WS y tareas en vuelo.
    func terminarLlamada() {
        tareaPrincipal?.cancel()
        tareaPrincipal = nil
        tareaAudio?.cancel()
        tareaAudio = nil
        tareaRecepcion?.cancel()
        tareaRecepcion = nil
        tareaTurno?.cancel()
        tareaTurno = nil
        tareaTimeoutFinal?.cancel()
        tareaTimeoutFinal = nil
        if let continuacion = continuacionFinal {
            continuacionFinal = nil
            continuacion.resume(throwing: CancellationError())
        }
        recorderContinuo.detener()
        reproductorPCM.detener()
        reproductorMPEG.detener()
        if let realtime {
            realtime.close()
        }
        realtime = nil
        streamAudio = nil
        textoParcial = nil
        estado = .inactivo
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
        sttConectado = credenciales?.voiceStt != nil

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
        sttLive = sttLiveDetectado
        ttsPCM = ttsPCMDetectado

        do {
            let stream = try recorderContinuo.iniciar()
            streamAudio = stream
        } catch {
            estado = .inactivo
            errorMensaje = error.localizedDescription
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
            await self?.bucleDeEscucha(client: client)
        }
    }

    /// Consume el stream del micrófono continuo: mide RMS, detecta voz/silencio,
    /// manda los chunks por WS cuando hay STT live, y dispara el `commit` en
    /// cuanto hay silencio o se agota el turno máximo.
    private func bucleDeEscucha(client: APIClient) async {
        guard let stream = streamAudio else { return }
        var turnoActivo = false
        var inicioTurno = Date()
        var silencioMs = 0
        var acumuladoPCM = Data()

        for await chunk in stream {
            if Task.isCancelled { break }
            nivelEntrada = chunk.rms
            let hayVoz = chunk.rms > umbralHabla

            if hayVoz {
                if estado == .hablando {
                    bargeIn()
                }
                if !turnoActivo {
                    turnoActivo = true
                    inicioTurno = Date()
                    acumuladoPCM = Data()
                    textoParcial = nil
                }
                silencioMs = 0
                acumuladoPCM.append(chunk.datos)
                if sttLive, let realtime {
                    try? await realtime.sendPCM16(chunk.datos)
                }
                if Date().timeIntervalSince(inicioTurno) >= maxSegundosTurno {
                    await lanzarTurno(client: client, acumulado: acumuladoPCM)
                    turnoActivo = false
                    acumuladoPCM = Data()
                    silencioMs = 0
                }
            } else if turnoActivo {
                silencioMs += 100
                if silencioMs >= silencioMaxMs {
                    await lanzarTurno(client: client, acumulado: acumuladoPCM)
                    turnoActivo = false
                    acumuladoPCM = Data()
                    silencioMs = 0
                }
            }
        }
    }

    /// Cierra el turno actual: con STT live manda `commit` (el `transcript`
    /// final llega por el loop de recepción); sin STT live transcribe la toma
    /// acumulada. En ambos casos corre el agente y el TTS en `tareaTurno`.
    private func lanzarTurno(client: APIClient, acumulado: Data) async {
        tareaTurno?.cancel()
        tareaTurno = nil
        reproductorPCM.detener()
        reproductorMPEG.detener()

        if sttLive, realtime != nil {
            try? await realtime?.commit()
            tareaTurno = Task { [weak self] in
                await self?.procesarTurnoLive(client: client)
            }
        } else {
            tareaTurno = Task { [weak self] in
                await self?.procesarTurnoWAV(client: client, acumulado: acumulado)
            }
        }
    }

    // MARK: - Turno: transcripción → agente → TTS

    private func procesarTurnoLive(client: APIClient) async {
        do {
            let texto = try await esperarTranscripcionFinal()
            await procesarTextoFinal(texto, client: client)
        } catch is CancellationError {
            return
        } catch {
            guard estado == .escuchando || estado == .pensando || estado == .hablando else { return }
            errorMensaje = error.localizedDescription
            estado = .escuchando
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
            guard estado == .escuchando || estado == .pensando || estado == .hablando else { return }
            errorMensaje = error.localizedDescription
            estado = .escuchando
        }
    }

    /// Con el texto final del turno: corre el chat completo (tools incluidas)
    /// y manda la respuesta a hablar por el transporte que toque.
    private func procesarTextoFinal(_ texto: String, client: APIClient) async {
        let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoLimpio.isEmpty else {
            ultimaTranscripcion = nil
            textoParcial = nil
            estado = .escuchando
            return
        }
        ultimaTranscripcion = textoLimpio
        ultimaRespuesta = nil
        textoParcial = nil
        estado = .pensando

        let enviado = await chat?.enviar(texto: textoLimpio, client: client) ?? false
        guard enviado, let respuesta = chat?.ultimaRespuestaDelAsistente,
              !respuesta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            if !Task.isCancelled { estado = .escuchando }
            return
        }
        ultimaRespuesta = respuesta
        await hablarRespuesta(respuesta, client: client)
    }

    /// Sintetiza y reproduce la respuesta. Con `tts_pcm` usa el WS en streaming
    /// (chunks pcm24 → ``ReproductorPCMStream``, habla de inmediato); sin él
    /// cae a `hablarStream` + ``ReproductorMPEGStream``.
    private func hablarRespuesta(_ respuesta: String, client: APIClient) async {
        let textoHablar = SpeechTags.ocultar(respuesta).trimmingCharacters(in: .whitespacesAndNewlines)
        guard !textoHablar.isEmpty else {
            estado = .escuchando
            return
        }
        estado = .hablando
        if ttsPCM, let realtime {
            // El loop de recepción enruta los chunks pcm24 al reproductor y el
            // `done` llama `finalizar()`, que dispara `alTerminar` al drenar.
            reproductorPCM.alTerminar = { [weak self] in
                self?.terminarHabla()
            }
            do {
                try await realtime.speak(text: textoHablar, voiceId: vozId, pcm: true)
            } catch is CancellationError {
                return
            } catch {
                errorMensaje = error.localizedDescription
                estado = .escuchando
            }
        } else {
            do {
                let stream = try await client.hablarStream(
                    texto: textoHablar, voiceId: vozId, modelId: "eleven_turbo_v2_5"
                )
                _ = try await reproductorMPEG.reproducir(stream: stream)
                if !Task.isCancelled { estado = .escuchando }
            } catch is CancellationError {
                return
            } catch {
                errorMensaje = error.localizedDescription
                estado = .escuchando
            }
        }
    }

    /// Aviso del reproductor PCM cuando drena todo lo encolado: vuelve a
    /// escuchar.
    private func terminarHabla() {
        if estado == .hablando {
            estado = .escuchando
        }
    }

    /// Barge-in: corta la voz de Edecán y deja paso al turno del usuario.
    private func bargeIn() {
        tareaTurno?.cancel()
        tareaTurno = nil
        reproductorPCM.detener()
        reproductorMPEG.detener()
        // Avisa al servidor para que pare el TTS viejo y no mande un `done`
        // huérfano que el loop confunda con el fin del próximo turno.
        if let realtime {
            Task { try? await realtime.interrupt() }
        }
        ultimaRespuesta = nil
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
            if let continuacion = continuacionFinal {
                continuacionFinal = nil
                tareaTimeoutFinal?.cancel()
                tareaTimeoutFinal = nil
                continuacion.resume(throwing: ErrorLlamada.conexionCerrada)
            }
            if estado != .inactivo {
                errorMensaje = "Se perdió la conexión de voz."
            }
        }
    }

    private func manejarEvento(_ evento: RealtimeVoiceEvent) {
        switch evento.type {
        case "transcript.partial":
            if let texto = evento.text { textoParcial = texto }
        case "transcript":
            if let texto = evento.text {
                textoParcial = nil
                ultimaTranscripcion = texto
                if let continuacion = continuacionFinal {
                    continuacionFinal = nil
                    tareaTimeoutFinal?.cancel()
                    tareaTimeoutFinal = nil
                    continuacion.resume(returning: texto)
                }
            }
        case "audio" where evento.mime == "audio/pcm24":
            if estado == .hablando, let audio = evento.audio {
                reproductorPCM.agregar(audio)
            }
        case "done":
            if estado == .hablando {
                reproductorPCM.finalizar()
            }
        case "error":
            if let continuacion = continuacionFinal {
                continuacionFinal = nil
                tareaTimeoutFinal?.cancel()
                tareaTimeoutFinal = nil
                continuacion.resume(throwing: ErrorLlamada.transcripcionFallida)
            } else {
                errorMensaje = "La conexión de voz falló."
            }
        default:
            break
        }
    }

    /// Espera (con timeout) el `transcript` final tras el `commit`.
    private func esperarTranscripcionFinal() async throws -> String {
        try await withCheckedThrowingContinuation { continuation in
            continuacionFinal = continuation
            tareaTimeoutFinal?.cancel()
            tareaTimeoutFinal = Task { [weak self] in
                try? await Task.sleep(for: .seconds(20))
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