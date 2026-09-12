import EdecanKit
import Foundation
import Speech

/// Transcripción compartida por el dictado del chat y la llamada por turnos.
/// Con credencial de voz conectada usa el transporte realtime del backend
/// (WS) y cae al endpoint HTTP si un proxy viejo no soporta WS; sin credencial
/// usa reconocimiento del dispositivo (`SFSpeechRecognizer`, nunca fabrica
/// texto).
@MainActor
enum TranscripcionVoz {
    static func transcribir(
        audio: Data, client: APIClient, sttConectado: Bool, reconocedorLocal: ReconocedorVozLocal
    ) async throws -> String {
        if sttConectado {
            // Una toma ya cerrada no necesita abrir otro WS, especialmente
            // cuando llegamos aqui porque se perdio el transporte en vivo.
            return try await client.transcribir(audioData: audio, mimeType: "audio/wav", language: nil)
        }
        return try await reconocedorLocal.transcribir(wav: audio)
    }

    private static func transcribirRealtime(audio: Data, client: APIClient) async throws -> String {
        let realtime = try await client.abrirVozRealtime()
        defer { realtime.close() }
        let ready = try await realtime.connect()
        guard ready.type == "ready" else { throw ErrorTranscripcion.realtimeNoDisponible }
        try await realtime.sendAudio(audio, mime: "audio/wav")
        try await realtime.commitAudio()
        while true {
            let evento = try await realtime.receive()
            if evento.type == "transcript", let text = evento.text { return text }
            if evento.type == "error" { throw ErrorTranscripcion.realtimeNoDisponible }
        }
    }
}

enum ErrorTranscripcion: LocalizedError {
    case realtimeNoDisponible

    var errorDescription: String? {
        switch self {
        case .realtimeNoDisponible:
            return "La conexión de voz realtime no está disponible."
        }
    }
}

@MainActor
final class ReconocedorVozLocal {
    func transcribir(wav: Data) async throws -> String {
        let autorizacion = await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status)
            }
        }
        guard autorizacion == .authorized else { throw ErrorReconocimiento.permisoDenegado }
        guard let recognizer = SFSpeechRecognizer(locale: Locale.current),
              recognizer.isAvailable,
              recognizer.supportsOnDeviceRecognition
        else { throw ErrorReconocimiento.noDisponible }

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

enum ErrorReconocimiento: LocalizedError {
    case permisoDenegado
    case noDisponible

    var errorDescription: String? {
        switch self {
        case .permisoDenegado:
            return "Activa Reconocimiento de voz para Edecan en Ajustes y vuelve a intentarlo."
        case .noDisponible:
            return "El reconocimiento de voz del dispositivo no está disponible ahora."
        }
    }
}

final class SesionReconocimiento: @unchecked Sendable {
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
