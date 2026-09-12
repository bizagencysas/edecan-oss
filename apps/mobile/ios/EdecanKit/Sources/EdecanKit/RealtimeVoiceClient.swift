import Foundation

/// Evento público del transporte `WS /v1/voice/realtime`.
///
/// Cubre tanto el protocolo TTS realtime original (`ready` → `speak` →
/// `audio`* → `done`) como la llamada continua: `ready` ahora trae las
/// capacidades `stt_live`/`tts_pcm`, el cliente envía frames `audio/pcm16`
/// y recibe interims `transcript.partial` antes del `transcript` final, y el
/// `speak` con `"pcm": true` responde chunks `audio/pcm24` (s16le 24 kHz).
public struct RealtimeVoiceEvent: Sendable, Equatable {
    public let type: String
    public let turnId: Int?
    public let sequence: Int?
    public let mime: String?
    public let audio: Data?
    public let text: String?
    public let message: String?
    public let state: String?
    /// `stt_live` del `ready`: el servidor admite STT en vivo (frames PCM16
    /// con parciales). `false`/`nil` → el cliente captura por turnos.
    public let sttLive: Bool?
    /// `tts_pcm` del `ready`: el servidor devuelve `audio/pcm24` en streaming.
    public let ttsPCM: Bool?
    /// `sample_rate` de los chunks `audio/pcm24` (típicamente 24000).
    public let sampleRate: Int?

    init(json: [String: Any]) {
        type = json["type"] as? String ?? "unknown"
        turnId = json["turn_id"] as? Int
        sequence = json["sequence"] as? Int
        mime = json["mime"] as? String
        if let encoded = json["data"] as? String {
            audio = Data(base64Encoded: encoded)
        } else {
            audio = nil
        }
        text = json["text"] as? String
        message = json["message"] as? String
        state = json["state"] as? String
        sttLive = json["stt_live"] as? Bool
        ttsPCM = json["tts_pcm"] as? Bool
        sampleRate = json["sample_rate"] as? Int
    }
}

/// Cliente mínimo y autenticado para el transporte realtime de voz.
///
/// El token se envía en el primer frame, nunca en la URL. La clase no decide
/// cuándo hablar ni cómo reproducir audio: la UI controla `speak`, `interrupt`
/// y consume los eventos/bytes según su propio ciclo de vida.
public final class RealtimeVoiceClient: @unchecked Sendable {
    public enum ClientError: Error, LocalizedError, Sendable, Equatable {
        case invalidURL
        case invalidMessage
        case serverClosed(Int)
        case unsupportedFrame

        public var errorDescription: String? {
            switch self {
            case .invalidURL: "La URL realtime no es válida."
            case .invalidMessage: "El servidor envió un evento realtime inválido."
            case .serverClosed(let code): "La sesión realtime se cerró (\(code))."
            case .unsupportedFrame: "El servidor envió un frame realtime no compatible."
            }
        }
    }

    private let task: URLSessionWebSocketTask
    private let token: String
    private let conversationId: String?

    public init(
        url: URL,
        token: String,
        conversationId: String? = nil,
        urlSession: URLSession = .shared
    ) {
        task = urlSession.webSocketTask(with: url)
        self.token = token
        self.conversationId = conversationId
    }

    public func connect() async throws -> RealtimeVoiceEvent {
        task.resume()
        var message: [String: Any] = ["type": "authenticate", "token": token]
        if let conversationId { message["conversation_id"] = conversationId }
        try await send(message)
        return try await receive()
    }

    public func speak(
        text: String,
        voiceId: String? = nil,
        modelId: String? = nil,
        pcm: Bool = false
    ) async throws {
        var mensaje: [String: Any] = ["type": "speak", "text": text]
        if let voiceId { mensaje["voice_id"] = voiceId }
        if let modelId { mensaje["model_id"] = modelId }
        if pcm { mensaje["pcm"] = true }
        try await send(mensaje)
    }

    /// `speak` en PCM streaming: el servidor responde chunks `audio/pcm24`
    /// (s16le 24 kHz mono) y cierra con el `done` que ya usa el transporte.
    ///
    /// Devuelve un `AsyncThrowingStream` de los bytes de audio crudos: el
    /// llamador los encola en su reproductor a medida que llegan (habla de
    /// inmediato, sin esperar el mp3 completo). Cancelar la iteración corta
    /// el loop interno, pero NO manda `interrupt`: para barge-in el llamador
    /// debe invocar ``interrupt()`` por su cuenta antes de empezar un turno
    /// nuevo (el servidor dejaría de emitir chunks del TTS viejo).
    public func speakPCM(text: String, voiceId: String? = nil) async throws -> AsyncThrowingStream<Data, Error> {
        var mensaje: [String: Any] = ["type": "speak", "text": text, "pcm": true]
        if let voiceId { mensaje["voice_id"] = voiceId }
        try await send(mensaje)
        return AsyncThrowingStream { continuation in
            let tarea = Task { [weak self] in
                do {
                    while !Task.isCancelled {
                        guard let self else { throw CancellationError() }
                        let evento = try await self.receive()
                        if evento.type == "audio",
                           evento.mime == "audio/pcm24",
                           let audio = evento.audio {
                            continuation.yield(audio)
                        } else if evento.type == "done" {
                            continuation.finish()
                            return
                        } else if evento.type == "error" {
                            continuation.finish(throwing: ClientError.serverClosed(-1))
                            return
                        }
                    }
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in
                tarea.cancel()
            }
        }
    }

    public func sendAudio(_ data: Data, mime: String = "audio/wav") async throws {
        try await send([
            "type": "audio",
            "mime": mime,
            "data": data.base64EncodedString()
        ])
    }

    /// Envía un frame de audio en vivo como PCM crudo s16le 16 kHz mono
    /// (`audio/pcm16`). Es el transporte de la llamada continua: muchos
    /// frames pequeños durante el turno, en vez de un único WAV.
    public func sendPCM16(_ data: Data) async throws {
        try await send([
            "type": "audio",
            "mime": "audio/pcm16",
            "data": data.base64EncodedString()
        ])
    }

    public func sendImage(_ data: Data, mime: String = "image/jpeg") async throws {
        try await send([
            "type": "image",
            "mime": mime,
            "data": data.base64EncodedString()
        ])
    }

    public func commitAudio() async throws {
        try await send(["type": "commit"])
    }

    /// Alias explícito de ``commitAudio()`` para el flujo de llamada continua:
    /// cierra el turno de audio en vivo y dispara el `transcript` final.
    public func commit() async throws {
        try await send(["type": "commit"])
    }

    public func interrupt() async throws {
        try await send(["type": "interrupt"])
    }

    public func receive() async throws -> RealtimeVoiceEvent {
        do {
            let message = try await task.receive()
            switch message {
            case .string(let value):
                guard let data = value.data(using: .utf8),
                      let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
                else { throw ClientError.invalidMessage }
                return RealtimeVoiceEvent(json: json)
            case .data:
                throw ClientError.unsupportedFrame
            @unknown default:
                throw ClientError.unsupportedFrame
            }
        } catch let error as ClientError {
            throw error
        } catch {
            throw error
        }
    }

    public func close() {
        task.cancel(with: .normalClosure, reason: nil)
    }

    private func send(_ message: [String: Any]) async throws {
        guard JSONSerialization.isValidJSONObject(message) else {
            throw ClientError.invalidMessage
        }
        let data = try JSONSerialization.data(withJSONObject: message)
        guard let text = String(data: data, encoding: .utf8) else {
            throw ClientError.invalidMessage
        }
        try await task.send(.string(text))
    }
}
