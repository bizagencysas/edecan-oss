import AVFoundation
import EdecanKit
import Foundation
import Observation
import Speech

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
    private var delegadoSintesis: DelegadoSintesis?
    private var proveedorSTTConectado = false
    private var proveedorTTSConectado = false
    private var pushToTalk = PushToTalkGate()
    private var tareaPermiso: Task<Void, Never>?

    private(set) var estado: Estado = .inactivo
    private(set) var ultimaTranscripcion: String?
    private(set) var usandoVozDelDispositivo = false
    var errorMensaje: String?

    var errorParaMostrar: String? { errorMensaje ?? chat.errorMensaje }

    init(chat: ChatViewModel) {
        self.chat = chat
    }

    /// Sincrono a proposito: el gesto registra la presion antes de crear
    /// cualquier Task, de modo que una liberacion inmediata nunca pueda
    /// adelantarse al estado local aunque el scheduler invierta las tareas.
    func alPresionar() {
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
                texto = try await client.transcribir(audioData: audio, mimeType: "audio/wav", language: nil)
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

    func cancelarGrabacion() {
        pushToTalk.cancel()
        tareaPermiso?.cancel()
        tareaPermiso = nil
        recorder.cancelar()
        if estado == .preparando || estado == .grabando {
            estado = .inactivo
        }
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
        do {
            if proveedorTTSConectado {
                let resultado = try await client.hablar(texto: respuesta, voiceId: nil)
                let player = try AVAudioPlayer(data: resultado.audio)
                reproductor = player
                player.play()
                while player.isPlaying {
                    try await Task.sleep(for: .milliseconds(150))
                }
                reproductor = nil
            } else {
                try await hablarLocalmente(respuesta)
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
        estado = .inactivo
    }

    private func hablarLocalmente(_ texto: String) async throws {
        sintetizador.stopSpeaking(at: .immediate)
        let utterance = AVSpeechUtterance(string: texto)
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.preferredLanguages.first ?? "es-ES")
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate

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

    var errorDescription: String? {
        switch self {
        case .permisoDenegado:
            return "Activa Reconocimiento de voz para Edecan en Ajustes y vuelve a intentarlo."
        case .noDisponible:
            return "El reconocimiento de voz del dispositivo no está disponible ahora."
        case .sinTexto:
            return "No se entendió nada. Intenta de nuevo, más cerca del micrófono."
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
            let values = (task, continuation)
            task = nil
            continuation = nil
            return values
        }
        task?.cancel()
        continuation?.resume(throwing: CancellationError())
    }
}

private final class DelegadoSintesis: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<Void, Error>?

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
