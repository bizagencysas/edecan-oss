import AVFoundation
import EdecanKit
import Foundation
import Observation
import Speech
import UIKit

/// Voz usa el mismo chat que la superficie principal. Si el tenant conecto
/// STT/TTS se respetan sus proveedores; sin claves, Speech y AVSpeechSynthesizer
/// dan una experiencia real en el dispositivo (nunca texto o audio ficticio).
@MainActor
@Observable
final class VozViewModel {
    enum Estado: Equatable {
        case inactivo
        case preparando
        case grabando
        case transcribiendo
        case procesando
        case reproduciendo
    }

    let chat: ChatViewModel

    private let recorder = VozRecorder()
    private let reconocedorLocal = ReconocedorVozLocal()
    private let sintetizador = AVSpeechSynthesizer()
    private var reproductor: AVAudioPlayer?
    private var reproductorDelegado: ReproductorDelegado?
    private var delegadoSintesis: DelegadoSintesis?
    private var realtimeClient: RealtimeVoiceClient?
    private let streamPlayer = ReproductorMPEGStream()
    private var proveedorSTTConectado = false
    private var proveedorTTSConectado = false
    private var pushToTalk = PushToTalkGate()
    private var tareaPermiso: Task<Void, Never>?
    nonisolated(unsafe) private var audioNotificationTokens: [NSObjectProtocol] = []

    private(set) var estado: Estado = .inactivo
    private(set) var ultimaTranscripcion: String?
    private(set) var usandoVozDelDispositivo = false
    var errorMensaje: String?
    private(set) var ultimoFrameCamara: Data?

    /// Velocidad de reproducción de la voz (0.75× … 2.0×). La fija `VozView`
    /// desde su `@AppStorage`; el default 1× también cubre el caso en que la
    /// vista todavía no sincronizó la preferencia guardada.
    var velocidad: Float = 1.0

    var errorParaMostrar: String? { errorMensaje ?? chat.errorMensaje }

    init(chat: ChatViewModel) {
        self.chat = chat
        observarInterrupcionesDeAudio()
    }

    deinit {
        for token in audioNotificationTokens {
            NotificationCenter.default.removeObserver(token)
        }
    }

    private func observarInterrupcionesDeAudio() {
        let center = NotificationCenter.default
        let interruption = center.addObserver(
            forName: AVAudioSession.interruptionNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            let rawType = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
            Task { @MainActor [weak self] in
                self?.manejarInterrupcionDeAudio(rawType)
            }
        }
        let route = center.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: AVAudioSession.sharedInstance(),
            queue: .main
        ) { [weak self] notification in
            let rawReason = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt
            Task { @MainActor [weak self] in
                self?.manejarCambioDeRuta(rawReason)
            }
        }
        let background = center.addObserver(
            forName: UIApplication.didEnterBackgroundNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor [weak self] in
                self?.detenerVozPorCambioDeAplicacion()
            }
        }
        audioNotificationTokens = [interruption, route, background]
    }

    private func manejarInterrupcionDeAudio(_ rawType: UInt?) {
        guard rawType == AVAudioSession.InterruptionType.began.rawValue else { return }
        detenerVozPorCambioDeAplicacion()
    }

    private func manejarCambioDeRuta(_ rawReason: UInt?) {
        guard rawReason == AVAudioSession.RouteChangeReason.oldDeviceUnavailable.rawValue else {
            return
        }
        detenerVozPorCambioDeAplicacion()
    }

    private func detenerVozPorCambioDeAplicacion() {
        tareaPermiso?.cancel()
        tareaPermiso = nil
        recorder.cancelar()
        if estado == .reproduciendo {
            interrumpirRespuesta()
        } else if estado != .inactivo {
            estado = .inactivo
        }
    }

    /// Sincrono a proposito: el gesto registra la presion antes de crear
    /// cualquier Task, de modo que una liberacion inmediata nunca pueda
    /// adelantarse al estado local aunque el scheduler invierta las tareas.
    func alPresionar() {
        if estado == .reproduciendo {
            interrumpirRespuesta()
        }
        guard estado == .inactivo else { return }
        let token = pushToTalk.press()
        estado = .preparando
        errorMensaje = nil
        tareaPermiso?.cancel()
        tareaPermiso = Task { [weak self] in
            await self?.completarInicio(token: token)
        }
    }

    private func completarInicio(token: UInt64) async {
        let permitido = await recorder.solicitarPermiso()
        guard pushToTalk.accepts(token) else {
            // Si esta era la presión recién soltada (y no una anterior que
            // terminó después de una nueva), deja la UI en reposo.
            if pushToTalk.isCurrent(token), estado == .preparando {
                estado = .inactivo
            }
            return
        }
        tareaPermiso = nil
        guard permitido else {
            pushToTalk.release()
            estado = .inactivo
            errorMensaje = VozRecorder.RecorderError.permisoDenegado.errorDescription
            return
        }
        do {
            try recorder.iniciar()
            estado = .grabando
        } catch {
            pushToTalk.release()
            estado = .inactivo
            errorMensaje = error.localizedDescription
        }
    }

    /// Tambien sincrono hasta detener el motor. El procesamiento posterior
    /// corre aparte, pero el microfono deja de grabar en el mismo evento de
    /// release del dedo.
    func alSoltar(client: APIClient?) {
        pushToTalk.release()
        if estado == .preparando {
            // El permiso todavía está en vuelo. Su token ya no será aceptado.
            tareaPermiso?.cancel()
            tareaPermiso = nil
            estado = .inactivo
            return
        }
        guard estado == .grabando else { return }
        guard let client else {
            recorder.cancelar()
            estado = .inactivo
            errorMensaje = "No hay sesión activa."
            return
        }

        do {
            let audio = try recorder.detener()
            estado = .transcribiendo
            Task { [weak self] in
                await self?.procesar(audio: audio, client: client)
            }
        } catch {
            errorMensaje = error.localizedDescription
            estado = .inactivo
        }
    }

    private func procesar(audio: Data, client: APIClient) async {
        do {
            let credenciales = try? await client.credenciales()
            proveedorSTTConectado = credenciales?.voiceStt != nil
            proveedorTTSConectado = credenciales?.voiceTts != nil
            usandoVozDelDispositivo = !proveedorSTTConectado || !proveedorTTSConectado

            let texto: String
            if proveedorSTTConectado {
                do {
                    texto = try await transcribirRealtime(audio: audio, client: client)
                } catch {
                    // El endpoint HTTP sigue siendo un fallback operativo si
                    // un proxy o una versión vieja del backend no soporta WS.
                    texto = try await client.transcribir(
                        audioData: audio, mimeType: "audio/wav", language: nil
                    )
                }
            } else {
                texto = try await reconocedorLocal.transcribir(wav: audio)
            }
            let textoLimpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !textoLimpio.isEmpty else {
                throw ErrorVozLocal.sinTexto
            }
            ultimaTranscripcion = textoLimpio

            estado = .procesando
            let enviado = await chat.enviar(texto: textoLimpio, client: client)
            guard enviado else {
                estado = .inactivo
                return
            }
            await hablarRespuestaSiHay(client: client)
        } catch {
            errorMensaje = error.localizedDescription
            estado = .inactivo
        }
    }

    private func transcribirRealtime(audio: Data, client: APIClient) async throws -> String {
        let realtime = try await client.abrirVozRealtime()
        defer { realtime.close() }
        let ready = try await realtime.connect()
        guard ready.type == "ready" else { throw ErrorVozLocal.realtimeNoDisponible }
        if let ultimoFrameCamara {
            try await realtime.sendImage(ultimoFrameCamara, mime: "image/jpeg")
        }
        try await realtime.sendAudio(audio, mime: "audio/wav")
        try await realtime.commitAudio()
        while true {
            let evento = try await realtime.receive()
            if evento.type == "transcript", let text = evento.text { return text }
            if evento.type == "error" { throw ErrorVozLocal.realtimeNoDisponible }
        }
    }

    func cancelarGrabacion() {
        pushToTalk.cancel()
        tareaPermiso?.cancel()
        tareaPermiso = nil
        recorder.cancelar()
        if estado == .preparando || estado == .grabando {
            estado = .inactivo
        }
    }

    func actualizarFrameCamara(_ frame: Data?) {
        ultimoFrameCamara = frame
    }

    /// Barge-in local: un nuevo gesto corta la reproducción actual antes de
    /// abrir el micrófono. El audio viejo no puede llamar al delegate y
    /// devolver la UI a un estado incorrecto porque ambos recursos se limpian
    /// aquí, en el mismo hilo MainActor.
    func interrumpirRespuesta() {
        guard estado == .reproduciendo else { return }
        streamPlayer.detener()
        reproductor?.stop()
        reproductor = nil
        reproductorDelegado = nil
        Task { try? await realtimeClient?.interrupt() }
        sintetizador.stopSpeaking(at: .immediate)
        delegadoSintesis = nil
        estado = .inactivo
    }

    func resolverConfirmacion(aprobado: Bool, client: APIClient) async {
        estado = .procesando
        await chat.resolverConfirmacion(aprobado: aprobado, client: client)
        await hablarRespuestaSiHay(client: client)
    }

    private func hablarRespuestaSiHay(client: APIClient) async {
        guard chat.confirmacionPendiente == nil,
              let respuesta = chat.ultimaRespuestaDelAsistente,
              !respuesta.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        else {
            estado = .inactivo
            return
        }

        estado = .reproduciendo
        let textoLimpio = SpeechTags.ocultar(respuesta).trimmingCharacters(in: .whitespacesAndNewlines)
        do {
            if proveedorTTSConectado {
                let stream = try await client.hablarStream(
                    texto: textoLimpio,
                    voiceId: "0uHpKhb0ymsdvmCtPV8y",
                    modelId: "eleven_turbo_v2_5"
                )
                try await streamPlayer.reproducir(stream: stream)
                estado = .inactivo
            } else {
                try await hablarLocalmente(textoLimpio)
                estado = .inactivo
            }
        } catch is CancellationError {
            return
        } catch {
            guard estado == .reproduciendo else { return }
            errorMensaje = error.localizedDescription
            estado = .inactivo
        }
    }

    /// Configura el reproductor y vuelve de inmediato: el final lo marca el
    /// delegate (`audioPlayerDidFinishPlaying`) en lugar de un bucle de sondeo.
    /// `enableRate`/`rate` se fijan ANTES de `prepareToPlay()` porque si no
    /// `AVAudioPlayer` ignora la velocidad.
    private func reproducir(audio: Data) throws {
        let player = try AVAudioPlayer(data: audio)
        player.enableRate = true
        player.rate = velocidad
        let delegado = ReproductorDelegado { [weak self] exitoso in
            Task { @MainActor in
                self?.finalizarReproduccion(exitoso: exitoso)
            }
        }
        reproductorDelegado = delegado
        player.delegate = delegado
        reproductor = player
        player.prepareToPlay()
        guard player.play() else {
            reproductor = nil
            reproductorDelegado = nil
            throw ErrorVozLocal.reproduccionFallida
        }
    }

    private func finalizarReproduccion(exitoso: Bool) {
        guard estado == .reproduciendo else { return }
        reproductor = nil
        reproductorDelegado = nil
        if !exitoso {
            errorMensaje = "No se pudo reproducir la respuesta de voz."
        }
        estado = .inactivo
    }

    private func hablarLocalmente(_ texto: String) async throws {
        sintetizador.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: texto)
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.preferredLanguages.first ?? "es-ES")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * velocidad

        try await withCheckedThrowingContinuation { continuation in
            let delegado = DelegadoSintesis(continuation: continuation)
            delegadoSintesis = delegado
            sintetizador.delegate = delegado
            sintetizador.speak(utterance)
        }
        sintetizador.delegate = nil
        delegadoSintesis = nil
    }
}

private enum ErrorVozLocal: LocalizedError {
    case permisoDenegado
    case noDisponible
    case sinTexto
    case reproduccionFallida
    case realtimeNoDisponible

    var errorDescription: String? {
        switch self {
        case .permisoDenegado:
            return "Activa Reconocimiento de voz para Edecan en Ajustes y vuelve a intentarlo."
        case .noDisponible:
            return "El reconocimiento de voz del dispositivo no está disponible ahora."
        case .sinTexto:
            return "No se entendió nada. Intenta de nuevo, más cerca del micrófono."
        case .reproduccionFallida:
            return "No se pudo reproducir la respuesta de voz."
        case .realtimeNoDisponible:
            return "La conexión de voz realtime no está disponible."
        }
    }
}

@MainActor
private final class ReconocedorVozLocal {
    func transcribir(wav: Data) async throws -> String {
        let autorizacion = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        guard autorizacion == .authorized else { throw ErrorVozLocal.permisoDenegado }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current),
              recognizer.isAvailable,
              recognizer.supportsOnDeviceRecognition
        else { throw ErrorVozLocal.noDisponible }

        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("edecan-voice-\(UUID().uuidString).wav")
        try wav.write(to: url, options: [.atomic, .completeFileProtection])
        defer { try? FileManager.default.removeItem(at: url) }

        let request = SFSpeechURLRecognitionRequest(url: url)
        request.requiresOnDeviceRecognition = true
        request.shouldReportPartialResults = false
        let sesion = SesionReconocimiento()
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                sesion.instalar(continuation)
                let task = recognizer.recognitionTask(with: request) { result, error in
                    if let error {
                        sesion.terminar(.failure(error))
                    } else if let result, result.isFinal {
                        sesion.terminar(.success(result.bestTranscription.formattedString))
                    }
                }
                sesion.guardar(task)
            }
        } onCancel: {
            sesion.cancelar()
        }
    }
}

private final class SesionReconocimiento: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<String, Error>?
    private var task: SFSpeechRecognitionTask?
    private var terminado = false

    func instalar(_ continuation: CheckedContinuation<String, Error>) {
        lock.withLock { self.continuation = continuation }
    }

    func guardar(_ task: SFSpeechRecognitionTask) {
        lock.withLock {
            if terminado { task.cancel() } else { self.task = task }
        }
    }

    func terminar(_ result: Result<String, Error>) {
        let continuation: CheckedContinuation<String, Error>? = lock.withLock {
            guard !terminado else { return nil }
            terminado = true
            task = nil
            defer { self.continuation = nil }
            return self.continuation
        }
        continuation?.resume(with: result)
    }

    func cancelar() {
        let (task, continuation): (SFSpeechRecognitionTask?, CheckedContinuation<String, Error>?) = lock.withLock {
            guard !terminado else { return (nil, nil) }
            terminado = true
            let values = (self.task, self.continuation)
            self.task = nil
            self.continuation = nil
            return values
        }
        task?.cancel()
        continuation?.resume(throwing: CancellationError())
    }
}

private final class ReproductorDelegado: NSObject, AVAudioPlayerDelegate, @unchecked Sendable {
    private let lock = NSLock()
    nonisolated(unsafe) private var alTerminar: ((Bool) -> Void)?

    init(alTerminar: @escaping (Bool) -> Void) {
        self.alTerminar = alTerminar
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        terminar(flag)
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        terminar(false)
    }

    private nonisolated func terminar(_ exitoso: Bool) {
        let callback: ((Bool) -> Void)? = lock.withLock {
            defer { self.alTerminar = nil }
            return self.alTerminar
        }
        callback?(exitoso)
    }
}

private final class DelegadoSintesis: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let lock = NSLock()
    nonisolated(unsafe) private var continuation: CheckedContinuation<Void, Error>?

    init(continuation: CheckedContinuation<Void, Error>) {
        self.continuation = continuation
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        terminar(nil)
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        terminar(CancellationError())
    }

    private nonisolated func terminar(_ error: Error?) {
        let continuation: CheckedContinuation<Void, Error>? = lock.withLock {
            defer { self.continuation = nil }
            return self.continuation
        }
        if let error { continuation?.resume(throwing: error) }
        else { continuation?.resume() }
    }
}
