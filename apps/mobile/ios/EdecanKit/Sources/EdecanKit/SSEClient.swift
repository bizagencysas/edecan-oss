import Foundation

/// Cliente de Server-Sent Events para el turno del agente
/// (`POST /v1/conversations/{id}/messages` y `POST .../confirm`, ambos
/// `text/event-stream` — `docs/api.md` §"Conversaciones y chat (SSE)").
///
/// Usa `URLSession.bytes(for:)` para leer el stream línea por línea
/// (`AsyncSequence.lines`, disponible desde iOS 15) en vez de esperar la
/// respuesta completa — así `ChatView` puede ir mostrando el texto a
/// medida que llega, no todo de golpe al final.
///
/// Este cliente SOLO entiende el framing de SSE (`event:` / `data:` /
/// líneas en blanco que cierran un bloque). No sabe nada de autenticación
/// ni de la forma de la URL — quien llama arma la `URLRequest` completa
/// (método, cuerpo, cabecera `Authorization`; ver `APIClient.tokenDeAccesoValido()`
/// y `APIClient.urlCompleta(_:)`).
public struct SSEClient: Sendable {
    public enum SSEError: Error, LocalizedError, Sendable, Equatable {
        case respuestaInvalida
        case servidor(status: Int)
        case eventoInvalido(detalle: String)
        case conexion(detalle: String)

        public var errorDescription: String? {
            switch self {
            case .respuestaInvalida:
                return "El servidor envió una respuesta que no se pudo interpretar."
            case .servidor(let status):
                return "El servidor rechazó la conexión de chat (\(status))."
            case .eventoInvalido(let detalle):
                return "Edecán envió un evento de chat que no se pudo leer: \(detalle)"
            case .conexion(let detalle):
                return "Se perdió la conexión con Edecán: \(detalle)"
            }
        }
    }

    private let urlSession: URLSession

    public init(urlSession: URLSession = .shared) {
        self.urlSession = urlSession
    }

    /// Abre la conexión SSE de `request` y va emitiendo un ``ChatEvent`` por
    /// cada bloque `data:` completo (línea en blanco = fin de bloque). El
    /// stream termina normalmente cuando el servidor cierra la conexión
    /// (después de `message.done`) o con un error si la conexión falla o un
    /// evento no se puede decodificar.
    public func stream(_ request: URLRequest) -> AsyncThrowingStream<ChatEvent, Error> {
        AsyncThrowingStream { continuation in
            let tarea = Task {
                do {
                    var request = request
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")

                    let (bytes, response) = try await urlSession.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw SSEError.respuestaInvalida
                    }
                    guard (200..<300).contains(http.statusCode) else {
                        throw SSEError.servidor(status: http.statusCode)
                    }

                    var nombreEvento: String?
                    var lineasDeDatos: [String] = []
                    let decoder = JSONDecoder()

                    func despacharBloque() throws {
                        guard !lineasDeDatos.isEmpty else { return }
                        let payload = lineasDeDatos.joined(separator: "\n")
                        lineasDeDatos.removeAll()
                        let evento = nombreEvento
                        nombreEvento = nil
                        guard let jsonData = payload.data(using: .utf8) else { return }
                        do {
                            let chatEvent = try decoder.decode(ChatEvent.self, from: jsonData)
                            continuation.yield(chatEvent)
                        } catch {
                            throw SSEError.eventoInvalido(detalle: "evento '\(evento ?? "?")': \(error.localizedDescription)")
                        }
                    }

                    for try await linea in bytes.lines {
                        if linea.isEmpty {
                            try despacharBloque()
                            continue
                        }
                        if linea.hasPrefix(":") {
                            // Comentario/keep-alive de SSE — se ignora.
                            continue
                        }
                        guard let indiceDosPuntos = linea.firstIndex(of: ":") else { continue }
                        let campo = String(linea[linea.startIndex..<indiceDosPuntos])
                        var valor = String(linea[linea.index(after: indiceDosPuntos)...])
                        if valor.hasPrefix(" ") { valor.removeFirst() }
                        switch campo {
                        case "event":
                            nombreEvento = valor
                        case "data":
                            lineasDeDatos.append(valor)
                        default:
                            break // "id"/"retry"/campos que este cliente no necesita.
                        }
                    }
                    // Por si el servidor cierra el stream sin una última línea
                    // en blanco tras el bloque final.
                    try despacharBloque()
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: Self.normalizar(error))
                }
            }
            continuation.onTermination = { _ in tarea.cancel() }
        }
    }

    private static func normalizar(_ error: Error) -> Error {
        if error is SSEError || error is CancellationError {
            return error
        }
        return SSEError.conexion(detalle: error.localizedDescription)
    }
}
