import Foundation

/// Evento público del transporte `WS /v1/voice/realtime`.
public struct RealtimeVoiceEvent: Sendable, Equatable {
    public let type: String
    public let turnId: Int?
    public let sequence: Int?
    public let mime: String?
    public let audio: Data?
    public let text: String?
    public let state: String?

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
        state = json["state"] as? String
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

    public func speak(text: String) async throws {
        try await send(["type": "speak", "text": text])
    }

    public func sendAudio(_ data: Data, mime: String = "audio/wav") async throws {
        try await send([
            "type": "audio",
            "mime": mime,
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
