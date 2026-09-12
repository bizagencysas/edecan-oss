import AVFoundation

/// Respuesta hablada real del dispositivo cuando no se conecto un proveedor TTS.
@MainActor
final class VozRespuestaLocal: NSObject, AVSpeechSynthesizerDelegate {
    private let sintetizador = AVSpeechSynthesizer()
    private var espera: CheckedContinuation<Void, Error>?
    private var actual: AVSpeechUtterance?
    private var timeout: Task<Void, Never>?

    override init() {
        super.init()
        sintetizador.delegate = self
    }

    func hablar(_ texto: String) async throws {
        detener()
        try Task.checkCancellation()
        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try session.setActive(true)
        if session.currentRoute.outputs.contains(where: { $0.portType == .builtInReceiver }) {
            try session.overrideOutputAudioPort(.speaker)
        }
        let utterance = AVSpeechUtterance(string: texto)
        utterance.voice = AVSpeechSynthesisVoice(language: Locale.preferredLanguages.first ?? "es")
        actual = utterance
        let identity = ObjectIdentifier(utterance)
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                espera = continuation
                sintetizador.speak(utterance)
                timeout = Task { [weak self] in
                    do { try await Task.sleep(for: .seconds(max(30, Double(texto.count) / 5))) }
                    catch { return }
                    guard let self, let actual = self.actual, ObjectIdentifier(actual) == identity else { return }
                    self.actual = nil
                    let pendiente = self.espera
                    self.espera = nil
                    self.sintetizador.stopSpeaking(at: .immediate)
                    pendiente?.resume(throwing: NSError(domain: "voice.local", code: 1, userInfo: [NSLocalizedDescriptionKey: "La voz del dispositivo no confirmó la reproducción."]))
                }
            }
        } onCancel: {
            Task { @MainActor [weak self] in
                guard let self, let actual = self.actual, ObjectIdentifier(actual) == identity else { return }
                self.detener()
            }
        }
    }

    func detener() {
        timeout?.cancel()
        timeout = nil
        actual = nil
        let pendiente = espera
        espera = nil
        sintetizador.stopSpeaking(at: .immediate)
        pendiente?.resume(throwing: CancellationError())
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        let identity = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, let actual = self.actual, ObjectIdentifier(actual) == identity else { return }
            self.actual = nil
            self.timeout?.cancel()
            let pendiente = self.espera
            self.espera = nil
            pendiente?.resume()
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        let identity = ObjectIdentifier(utterance)
        Task { @MainActor [weak self] in
            guard let self, let actual = self.actual, ObjectIdentifier(actual) == identity else { return }
            self.actual = nil
            self.timeout?.cancel()
            let pendiente = self.espera
            self.espera = nil
            pendiente?.resume(throwing: CancellationError())
        }
    }
}
