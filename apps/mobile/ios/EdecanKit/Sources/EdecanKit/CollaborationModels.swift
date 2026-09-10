import Foundation

// MARK: - Equipos y espacios (`/v1/teams`, `/v1/workspaces`) — contrato en
// paralelo. Se decodifica todo con `decodeIfPresent`/`??` a propósito: estos
// routers están aterrizando en el backend y una respuesta parcial no debe
// tumbar la lista entera. El cliente degrada con "Próximamente" cuando la
// ruta todavía no existe (directiva §153: nunca fingir éxito).

/// Referencia a un worker persistente dentro de un equipo o espacio. Mismo
/// vocabulario que `persistent_agents` (`agent_id`, `name`, `role_title`).
public struct AgentRef: Codable, Sendable, Equatable, Identifiable {
    public let agentId: String
    public let name: String?
    public let roleTitle: String?

    public var id: String { agentId }

    /// Nombre legible para una fila: `name` si existe, si no el id crudo.
    public var nombreVisible: String {
        let candidato = name?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let candidato, !candidato.isEmpty { return candidato }
        return agentId
    }

    enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case name
        case roleTitle = "role_title"
    }
}

/// `GET/POST /v1/teams` — un equipo de compañeros que conversan en un hilo.
public struct Team: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let description: String?
    public let members: [AgentRef]
    public let conversationId: String?
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, description, members
        case conversationId = "conversation_id"
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        description = try container.decodeIfPresent(String.self, forKey: .description)
        members = (try? container.decode([AgentRef].self, forKey: .members)) ?? []
        conversationId = try container.decodeIfPresent(String.self, forKey: .conversationId)
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
    }
}

/// Delegación de trabajo entre agentes dentro de un turno de equipo.
public struct TeamDelegation: Codable, Sendable, Equatable {
    public let agentId: String?
    public let agentName: String?
    public let action: String?
    public let detail: String?

    enum CodingKeys: String, CodingKey {
        case agentId = "agent_id"
        case agentName = "agent_name"
        case action, detail
    }
}

/// Mensaje persistido de `GET /v1/teams/{id}/messages`. `kind` distingue un
/// mensaje normal (`nil`/`"message"`) de un evento de delegación; `senderName`
/// vacío o `"user"`/`"owner"` se dibuja como la persona dueña.
public struct TeamMessage: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let senderId: String?
    public let senderName: String?
    public let text: String
    public let kind: String?
    public let delegation: TeamDelegation?
    public let createdAt: Date?
    /// Evento de narración entre bots («Escribió a X», «Mensaje de X»).
    public let evento: String?
    public let de: String?
    public let goal: String?
    /// Cara del otro bot (snapshot del backend) para pintar el evento.
    public let cara: CaraSnapshot?
    /// Imágenes/archivos adjuntos (ids ya subidos a /v1/files).
    public let adjuntos: [AdjuntoMensaje]?
    /// Historial de tools del mensaje (harness P0): trae `blocks` de
    /// `preguntar_al_usuario` para que los widgets sobrevivan al reabrir.
    public let toolCalls: [ChatEvent]
    /// Auto-assign (`kind=evento`, `evento=asignacion`): id estable del bot.
    public let assignedWorkerId: String?
    /// Nombre visible del bot asignado; fallback legacy `de`.
    public let assignedWorkerName: String?
    /// BotBeta caec108d: `worker_id` canónico en metadata de turno/mensaje.
    public let workerId: String?
    /// Nombre técnico del worker en metadata (`name`).
    public let workerName: String?
    /// Display del worker en metadata (`display_name`).
    public let workerDisplayName: String?

    enum CodingKeys: String, CodingKey {
        case id, text, kind, delegation, evento, de, goal, cara, adjuntos, name
        case senderId = "sender_id"
        case senderName = "sender_name"
        case createdAt = "created_at"
        case toolCalls = "tool_calls"
        case assignedWorkerId = "assigned_worker_id"
        case assignedWorkerName = "assigned_worker_name"
        case workerId = "worker_id"
        case workerDisplayName = "display_name"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        senderId = try container.decodeIfPresent(String.self, forKey: .senderId)
        senderName = try container.decodeIfPresent(String.self, forKey: .senderName)
        text = (try? container.decode(String.self, forKey: .text)) ?? ""
        kind = try container.decodeIfPresent(String.self, forKey: .kind)
        delegation = try container.decodeIfPresent(TeamDelegation.self, forKey: .delegation)
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
        evento = try container.decodeIfPresent(String.self, forKey: .evento)
        de = try container.decodeIfPresent(String.self, forKey: .de)
        goal = try container.decodeIfPresent(String.self, forKey: .goal)
        cara = try container.decodeIfPresent(CaraSnapshot.self, forKey: .cara)
        adjuntos = try container.decodeIfPresent([AdjuntoMensaje].self, forKey: .adjuntos)
        toolCalls = (try? container.decode([ChatEvent].self, forKey: .toolCalls)) ?? []
        assignedWorkerId = try container.decodeIfPresent(String.self, forKey: .assignedWorkerId)
        assignedWorkerName = try container.decodeIfPresent(String.self, forKey: .assignedWorkerName)
        workerId = try container.decodeIfPresent(String.self, forKey: .workerId)
        workerName = try container.decodeIfPresent(String.self, forKey: .name)
        workerDisplayName = try container.decodeIfPresent(String.self, forKey: .workerDisplayName)
    }

    /// Bloques ricos (preguntas, media, links) reconstruidos desde tool_calls.
    public var bloquesDesdeToolCalls: [ChatBlock] {
        var bloques: [ChatBlock] = []
        for evento in toolCalls {
            guard case .toolEnd(_, _, _, _, let version, let eventBlocks, _) = evento,
                  version == 1
            else { continue }
            for bloque in eventBlocks where !bloques.contains(bloque) {
                bloques.append(bloque)
            }
        }
        return bloques
    }

    public var artefactosDesdeToolCalls: [ArtifactRef] {
        var arts: [ArtifactRef] = []
        var vistos = Set<String>()
        for evento in toolCalls {
            guard case .toolEnd(_, _, _, let artifacts, _, let eventBlocks, _) = evento else { continue }
            for a in artifacts where vistos.insert(a.fileId).inserted {
                arts.append(a)
            }
            for bloque in eventBlocks {
                if case .media(let media) = bloque, vistos.insert(media.artifact.fileId).inserted {
                    arts.append(media.artifact)
                }
            }
        }
        return arts
    }

    /// `true` si este mensaje lo escribió la persona dueña (no un agente).
    public var esDelDueno: Bool {
        guard let senderId else { return false }
        return senderId == "user" || senderId == "owner" || senderId == "human"
    }

    public var esDelegacion: Bool {
        kind == "delegation" || delegation != nil
    }

    /// Routing automático: fila persistida antes del turno (`persist_team_assignment_event`).
    public var esEventoAsignacion: Bool {
        kind == "evento" && evento == "asignacion"
    }

    /// Resuelve `worker_id` + nombre para el chip «Asignado a …» (BotBeta caec108d).
    /// Prioridad id: `assigned_worker_id` → `worker_id` → `sender_id` (mensaje del bot).
    /// Prioridad nombre: `assigned_worker_name` → `display_name` → `name` → `de` → cache.
    /// Nunca handoff ni IDs de handoff entre workers.
    public func resolverAsignacionAuto(workers: [PersistentWorker]) -> (id: String, nombre: String, cara: CaraSnapshot?) {
        let wid = nonEmpty(assignedWorkerId)
            ?? nonEmpty(workerId)
            ?? nonEmpty(senderId)
            ?? UUID().uuidString
        let cached = workers.first(where: { $0.id == wid })
        let nombre = nonEmpty(assignedWorkerName)
            ?? nonEmpty(workerDisplayName)
            ?? nonEmpty(workerName)
            ?? nonEmpty(de)
            ?? nonEmpty(senderName)
            ?? cached?.nombreVisible
            ?? "Bot"
        return (wid, nombre, cara)
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let trimmed = value?.trimmingCharacters(in: .whitespacesAndNewlines), !trimmed.isEmpty else {
            return nil
        }
        return trimmed
    }
}

/// Eventos del stream de `POST /v1/teams/{id}/message`. Tolerante a variantes
/// del contrato en paralelo: `text_delta`/`delta`, `delegation` y `done`.
public enum TeamStreamEvent: Sendable, Equatable {
    case textDelta(String)
    case delegation(TeamDelegation)
    case done
    case unknown

    /// Decodifica un bloque SSE ya enmarcado (`event:` + `data:`) sin exigir
    /// una forma única: acepta `{"type": "text_delta", "text": ...}`,
    /// `{"delta": ...}` y `{"type": "delegation", ...}`. Lo que no se reconoce
    /// se descarta (`.unknown`), nunca tumba el stream.
    static func decodificar(evento: String?, payload: String) -> TeamStreamEvent {
        let nombre = (evento ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        let cuerpo = payload.trimmingCharacters(in: .whitespacesAndNewlines)
        if cuerpo == "[DONE]" || nombre == "message.done" || nombre == "done" {
            return .done
        }
        guard let data = cuerpo.data(using: .utf8),
              let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else {
            return (nombre == "message.done" || nombre == "done") ? .done : .unknown
        }
        let type = (object["type"] as? String) ?? nombre
        switch type {
        case "text_delta", "delta", "message.delta", "text":
            let text = (object["text"] as? String) ?? (object["delta"] as? String) ?? ""
            return text.isEmpty ? .unknown : .textDelta(text)
        case "delegation", "agent.delegation", "delegate":
            return .delegation(TeamDelegation(
                agentId: object["agent_id"] as? String,
                agentName: (object["agent_name"] as? String) ?? (object["agent"] as? String),
                action: object["action"] as? String,
                detail: (object["detail"] as? String) ?? (object["message"] as? String)
            ))
        case "done", "message.done":
            return .done
        default:
            if let text = object["text"] as? String, !text.isEmpty { return .textDelta(text) }
            return .unknown
        }
    }
}

/// Lector SSE para el turno de equipo (`POST /v1/teams/{id}/message`), espejo
/// mínimo de ``SSEClient``: solo framing de `event:`/`data:` y devolución de
/// ``TeamStreamEvent``. Quien llama arma la `URLRequest` completa (método,
/// cuerpo, cabecera `Authorization`).
public struct TeamMessageStreamClient: Sendable {
    public enum StreamError: Error, LocalizedError, Sendable {
        case servidor(status: Int)
        case conexion(detalle: String)

        public var errorDescription: String? {
            switch self {
            case .servidor(let status):
                return "El servidor rechazó la conversación de equipo (\(status))."
            case .conexion(let detalle):
                return "Se perdió la conexión con Edecán: \(detalle)"
            }
        }
    }

    private let urlSession: URLSession

    public init(urlSession: URLSession = .shared) {
        self.urlSession = urlSession
    }

    public func stream(_ request: URLRequest) -> AsyncThrowingStream<TeamStreamEvent, Error> {
        AsyncThrowingStream { continuation in
            let tarea = Task {
                do {
                    var request = request
                    request.setValue("text/event-stream", forHTTPHeaderField: "Accept")
                    let (bytes, response) = try await urlSession.bytes(for: request)
                    guard let http = response as? HTTPURLResponse else {
                        throw StreamError.conexion(detalle: "respuesta inválida")
                    }
                    guard http.statusCode == 200 else {
                        throw StreamError.servidor(status: http.statusCode)
                    }

                    var nombreEvento: String?
                    var lineas: [String] = []

                    func despachar() {
                        guard !lineas.isEmpty else { return }
                        let payload = lineas.joined(separator: "\n")
                        lineas.removeAll()
                        let nombre = nombreEvento
                        nombreEvento = nil
                        continuation.yield(TeamStreamEvent.decodificar(evento: nombre, payload: payload))
                    }

                    for try await lineaCruda in bytes.lines {
                        let linea = lineaCruda.last == "\r" ? String(lineaCruda.dropLast()) : lineaCruda
                        if linea.isEmpty { despachar(); continue }
                        if linea.hasPrefix(":") { continue }
                        guard let indice = linea.firstIndex(of: ":") else { continue }
                        let campo = String(linea[linea.startIndex..<indice])
                        var valor = String(linea[linea.index(after: indice)...])
                        if valor.hasPrefix(" ") { valor.removeFirst() }
                        switch campo {
                        case "event":
                            if !lineas.isEmpty { despachar() }
                            nombreEvento = valor
                        case "data":
                            lineas.append(valor)
                        default:
                            break
                        }
                    }
                    despachar()
                    continuation.finish()
                } catch {
                    if error is CancellationError {
                        continuation.finish(throwing: CancellationError())
                    } else {
                        continuation.finish(
                            throwing: StreamError.conexion(detalle: error.localizedDescription)
                        )
                    }
                }
            }
            continuation.onTermination = { _ in tarea.cancel() }
        }
    }
}

// MARK: - Hilos (`/v1/messages/{id}/thread`)

/// Mensaje de un hilo (`GET /v1/messages/{id}/thread`). Mismo espíritu que
/// ``ConversationMessage`` pero mínimo: `id`, `role`, `text` y `created_at`.
public struct ThreadMessage: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let role: String
    public let text: String
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, role, text
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        role = (try? container.decode(String.self, forKey: .role)) ?? "assistant"
        text = (try? container.decode(String.self, forKey: .text)) ?? ""
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
    }

    public var esDelDueno: Bool { role == "user" || role == "owner" }
}

// MARK: - Espacios de trabajo (`/v1/workspaces`)

/// `GET/POST /v1/workspaces` — agrupa agentes alrededor de un contexto de
/// trabajo compartido.
public struct Workspace: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let description: String?
    public let agents: [AgentRef]
    public let createdAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, name, description, agents
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        name = (try? container.decode(String.self, forKey: .name)) ?? ""
        description = try container.decodeIfPresent(String.self, forKey: .description)
        agents = (try? container.decode([AgentRef].self, forKey: .agents)) ?? []
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
    }
}

// MARK: - Plano de control de la computadora (`/v1/computer`)

/// Fila de `computer_sessions` (`apps/api/edecan_api/routers/computer.py`),
/// espejo exacto de `_COLUMNS`. `mode` (`agent`|`user`|`paused`) es quien
/// mueve la superficie AHORA; `status` (`active`|`paused`|`ended`) es el ciclo
/// de vida. `agent_id`/`workspace_scope` pueden venir `null`/`{}`.
public struct ComputerSession: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let tenantId: String?
    public let userId: String?
    public let agentId: String?
    public let kind: String
    public let mode: String
    public let ephemeral: Bool
    public let status: String
    public let workspaceScope: [String: JSONValue]?
    public let createdAt: Date?
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, kind, mode, ephemeral, status
        case tenantId = "tenant_id"
        case userId = "user_id"
        case agentId = "agent_id"
        case workspaceScope = "workspace_scope"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        tenantId = try container.decodeIfPresent(String.self, forKey: .tenantId)
        userId = try container.decodeIfPresent(String.self, forKey: .userId)
        agentId = try container.decodeIfPresent(String.self, forKey: .agentId)
        kind = (try? container.decode(String.self, forKey: .kind)) ?? "desktop"
        mode = (try? container.decode(String.self, forKey: .mode)) ?? "agent"
        ephemeral = (try? container.decode(Bool.self, forKey: .ephemeral)) ?? false
        status = (try? container.decode(String.self, forKey: .status)) ?? "active"
        workspaceScope = try container.decodeIfPresent([String: JSONValue].self, forKey: .workspaceScope)
        createdAt = try? container.decode(Date.self, forKey: .createdAt)
        updatedAt = try? container.decode(Date.self, forKey: .updatedAt)
    }

    public var esTerminal: Bool { status == "ended" }

    /// Etiqueta legible de la superficie (browser/desktop/terminal/files).
    public var etiquetaKind: String {
        switch kind {
        case "browser": return "Navegador"
        case "desktop": return "Escritorio"
        case "terminal": return "Terminal"
        case "files": return "Archivos"
        default: return kind.capitalized
        }
    }
}
/// Snapshot de la cara de un bot (lo mínimo para pintarla sin traer el
/// worker completo): forma, colores y ojos en proporciones 0-1.
public struct CaraSnapshot: Codable, Sendable, Equatable {
    public let shape: String?
    public let fill: String?
    public let accent: String?
    public let eyes: OjosSnapshot?

    enum CodingKeys: String, CodingKey { case shape, fill, accent, eyes }

    public struct OjosSnapshot: Codable, Sendable, Equatable {
        public let left: OjoSnapshot?
        public let right: OjoSnapshot?
        enum CodingKeys: String, CodingKey { case left, right }
    }

    public struct OjoSnapshot: Codable, Sendable, Equatable {
        public let x: Double?
        public let y: Double?
        public let rx: Double?
        public let ry: Double?
        public let rotation: Double?
        enum CodingKeys: String, CodingKey { case x, y, rx, ry, rotation }
    }
}

/// Adjunto (imagen/archivo) dentro de un mensaje de bot: el archivo ya vive
/// en /v1/files; el chat lo pinta autenticado y con zoom.
public struct AdjuntoMensaje: Codable, Sendable, Equatable {
    public let fileId: String
    public let filename: String?
    public let mime: String?

    enum CodingKeys: String, CodingKey {
        case fileId = "file_id"
        case filename
        case mime
    }
}
