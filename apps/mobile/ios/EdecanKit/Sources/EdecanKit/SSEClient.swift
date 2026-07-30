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
        case servidor(status: Int, retryAfter: TimeInterval?)
        case eventoInvalido(detalle: String)
        case conexion(detalle: String)

        public var errorDescription: String? {
            switch self {
            case .respuestaInvalida:
                return "El servidor envió una respuesta que no se pudo interpretar."
            case .servidor(let status, _):
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
                    // Un chat SSE válido siempre responde 200. `202` se usa
                    // deliberadamente por el endpoint de reanudación para
                    // indicar que el mismo turno continúa en el servidor.
                    guard http.statusCode == 200 else {
                        let retryAfter = http.value(forHTTPHeaderField: "Retry-After")
                            .flatMap(TimeInterval.init)
                        throw SSEError.servidor(
                            status: http.statusCode,
                            retryAfter: retryAfter
                        )
                    }

                    var nombreEvento: String?
                    var lineasDeDatos: [String] = []
                    let decoder = JSONDecoder()
                    var terminal = SSETerminalState()

                    func despacharBloque() throws {
                        guard !lineasDeDatos.isEmpty else { return }
                        let payload = lineasDeDatos.joined(separator: "\n")
                        lineasDeDatos.removeAll()
                        let evento = nombreEvento
                        nombreEvento = nil
                        let decodificado = try Self.decodificarEvento(
                            nombre: evento,
                            payload: payload,
                            decoder: decoder
                        )
                        guard terminal.aceptar(decodificado) else { return }
                        continuation.yield(decodificado)
                    }

                    for try await lineaCruda in bytes.lines {
                        if terminal.finalizado { break }
                        // Algunos proxies/clientes conservan el CR de una
                        // respuesta con separadores CRLF. Para SSE, una línea
                        // que contiene solo ese CR sigue siendo el separador
                        // vacío entre eventos.
                        let linea = lineaCruda.last == "\r"
                            ? String(lineaCruda.dropLast())
                            : lineaCruda
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
                            // Defensa ante un relay que pierda la línea vacía:
                            // un nuevo campo `event:` cierra inequívocamente el
                            // bloque de datos anterior.
                            if !lineasDeDatos.isEmpty {
                                try despacharBloque()
                            }
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
                    try terminal.validarCierre()
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

    /// Decodifica el payload de un bloque ya enmarcado. `message.done` es un
    /// marcador terminal: su `usage` es telemetría opcional y nunca debe
    /// convertir una respuesta ya recibida y persistida en un fallo visible.
    /// Algunos proveedores/relays usan `[DONE]` o un cuerpo vacío para ese
    /// marcador; ambos se aceptan de forma compatible.
    static func decodificarEvento(
        nombre: String?,
        payload: String,
        decoder: JSONDecoder = JSONDecoder()
    ) throws -> ChatEvent {
        let nombreNormalizado = nombre?.trimmingCharacters(in: .whitespacesAndNewlines)
        if payload.trimmingCharacters(in: .whitespacesAndNewlines) == "[DONE]" {
            return .done(usage: nil)
        }
        guard let jsonData = payload.data(using: .utf8) else {
            if nombreNormalizado == "message.done" { return .done(usage: nil) }
            throw SSEError.eventoInvalido(
                detalle: "evento '\(nombreNormalizado ?? "?")': texto no válido"
            )
        }
        do {
            return try decoder.decode(ChatEvent.self, from: jsonData)
        } catch {
            if nombreNormalizado == "message.done" {
                return .done(usage: nil)
            }
            throw SSEError.eventoInvalido(
                detalle: "evento '\(nombreNormalizado ?? "?")': \(error.localizedDescription)"
            )
        }
    }
}

/// Acepta exactamente un cierre terminal por turno. Además de ignorar
/// eventos que un relay defectuoso mande después de `message.done` o
/// `agent.error`, permite distinguir un EOF limpio de una respuesta truncada:
/// cerrar el socket no equivale a haber terminado el trabajo.
struct SSETerminalState {
    private(set) var finalizado = false

    mutating func aceptar(_ evento: ChatEvent) -> Bool {
        guard !finalizado else { return false }
        switch evento {
        case .done, .error:
            finalizado = true
        default:
            break
        }
        return true
    }

    func validarCierre() throws {
        guard finalizado else {
            throw SSEClient.SSEError.conexion(
                detalle: "la respuesta terminó sin confirmación final"
            )
        }
    }
}
