import Foundation

// MARK: - `/v1/ide/*` — estudio remoto sobre el Edecán de escritorio.

/// `GET /v1/ide/status`.
public struct IDEStatusOut: Codable, Sendable, Equatable {
    public let connected: Bool
}

/// Una entrada del árbol de archivos (`edecan_companion.actions._list_tree`).
/// `children` es `nil` tanto para archivos como para carpetas que llegaron al
/// tope de profundidad/tamaño del companion (`ARCHITECTURE.md`/`docs/ide.md`)
/// — en ambos casos se muestra como hoja.
public struct IDEEntry: Codable, Sendable, Equatable, Identifiable {
    public let name: String
    public let isDir: Bool
    public let sizeBytes: Int?
    public let children: [IDEEntry]?

    public var id: String { name }

    enum CodingKeys: String, CodingKey {
        case name, children
        case isDir = "is_dir"
        case sizeBytes = "size_bytes"
    }
}

/// `GET /v1/ide/tree?path=&max_depth=&max_entries=` — árbol recursivo del
/// sandbox del companion emparejado. `truncated` es `true` si el companion
/// recortó el árbol por profundidad/tamaño (nunca lanza error por pedir uno
/// enorme, ver docstring del companion).
public struct IDETree: Codable, Sendable, Equatable {
    public let path: String
    public let entries: [IDEEntry]
    public let truncated: Bool
}

/// `GET /v1/ide/file?path=`. `encoding` es `"utf-8"` para texto normal o
/// `"base64"` si el companion no pudo decodificar el archivo como UTF-8 (se
/// asume binario) — la vista de este cliente solo intenta mostrar
/// `"utf-8"` como texto, y avisa en vez de mostrar binario para el resto.
public struct IDEFileOut: Codable, Sendable, Equatable {
    public let path: String
    public let content: String
    public let encoding: String
    public let sizeBytes: Int

    enum CodingKeys: String, CodingKey {
        case path, content, encoding
        case sizeBytes = "size_bytes"
    }
}

public struct IDERunOut: Codable, Sendable, Equatable {
    public let stdout: String
    public let stderr: String
    public let exitCode: Int
    public let truncated: Bool

    enum CodingKeys: String, CodingKey {
        case stdout, stderr, truncated
        case exitCode = "exit_code"
    }
}

// MARK: - Proyectos autorizados

public struct IDEWorkspace: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let path: String
    public let active: Bool
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, name, path, active
        case createdAt = "created_at"
    }

    public init(
        id: String,
        name: String,
        path: String,
        active: Bool,
        createdAt: Date
    ) {
        self.id = id
        self.name = name
        self.path = path
        self.active = active
        self.createdAt = createdAt
    }
}

public struct IDEWorkspacesOut: Codable, Sendable, Equatable {
    public let workspaces: [IDEWorkspace]
}

// MARK: - Sesiones persistentes de Terminal y Agente

public struct IDESession: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let kind: String
    public let workspaceId: String
    public let workspaceName: String
    public let status: String
    public let startedAt: Date
    public let endedAt: Date?
    public let exitCode: Int?
    public let command: [String]?
    public let provider: String?
    public let title: String?
    public let conversationId: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, status, command, provider, title
        case conversationId = "conversation_id"
        case workspaceId = "workspace_id"
        case workspaceName = "workspace_name"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case exitCode = "exit_code"
    }

    public var isActive: Bool {
        endedAt == nil
            && !["completed", "failed", "closed", "cancelled", "interrupted"].contains(status)
    }
}

public struct IDESessionsOut: Codable, Sendable, Equatable {
    public let sessions: [IDESession]
}

public struct IDESessionEvent: Codable, Sendable, Equatable, Identifiable {
    public let cursor: Int
    public let type: String
    public let text: String
    public let stream: String?
    public let timestamp: Date
    /// Canal deliberado de UI: bloques tipados del IDE (``IDEBlock``) que el
    /// agente acuñó con `mostrar_tabla`/`mostrar_grafica`. Vacío en casi todos
    /// los eventos; `text` siempre trae el equivalente en texto, así que un
    /// bloque descartado nunca implica perder el mensaje.
    public let presentation: [IDEBlock]

    public var id: Int { cursor }

    enum CodingKeys: String, CodingKey {
        case cursor, type, text, stream, timestamp, presentation
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        cursor = try container.decode(Int.self, forKey: .cursor)
        type = try container.decode(String.self, forKey: .type)
        text = try container.decode(String.self, forKey: .text)
        stream = try container.decodeIfPresent(String.self, forKey: .stream)
        timestamp = try container.decode(Date.self, forKey: .timestamp)
        // `decode` y no `decodeIfPresent`: la clave falta en casi todos los
        // eventos, y `try?` cubre ese caso y el de una lista malformada con la
        // misma rama.
        let tolerantes = (try? container.decode([IDEBlockTolerante].self, forKey: .presentation)) ?? []
        presentation = Array(
            tolerantes.compactMap(\.bloque).prefix(IDEBlockLimites.maxBloquesPorEvento)
        )
    }

    /// `presentation` no se vuelve a escribir a propósito: nada en la app
    /// serializa eventos (el índice local guarda conversaciones, nunca eventos
    /// — ver ``IDEConversationLocalStore``), y ``IDEBlock`` es solo de lectura.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(cursor, forKey: .cursor)
        try container.encode(type, forKey: .type)
        try container.encode(text, forKey: .text)
        try container.encodeIfPresent(stream, forKey: .stream)
        try container.encode(timestamp, forKey: .timestamp)
    }
}

public struct IDESessionReadOut: Codable, Sendable, Equatable {
    public let session: IDESession
    public let events: [IDESessionEvent]
    public let nextCursor: Int
    public let hasMore: Bool?

    enum CodingKeys: String, CodingKey {
        case session, events
        case nextCursor = "next_cursor"
        case hasMore = "has_more"
    }
}

/// Imagen que se envía directamente al contexto multimodal del agente IDE.
/// Los bytes viajan únicamente al Edecán emparejado y nunca se escriben en el
/// índice local de conversaciones del teléfono.
public struct IDEAgentAttachment: Encodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let name: String
    public let mediaType: String
    public let data: String

    public init(
        id: String = UUID().uuidString,
        name: String,
        mediaType: String,
        data: String
    ) {
        self.id = id
        self.name = name
        self.mediaType = mediaType
        self.data = data
    }

    enum CodingKeys: String, CodingKey {
        case name, data
        case mediaType = "media_type"
    }
}

public enum IDEAgentProvider: String, Codable, CaseIterable, Sendable, Identifiable {
    case auto
    case workersAI = "workers_ai"

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .auto: "Automático"
        case .workersAI: "Workers AI"
        }
    }
}

// MARK: - Conversaciones remotas del IDE

/// Índice local ligero de una conversación del IDE.
///
/// Una conversación puede contener varias sesiones remotas de agente porque
/// el companion ejecuta cada solicitud como un proceso independiente. El
/// iPhone conserva únicamente los IDs opacos, el título resumido y el
/// workspace. Código, salida completa y credenciales siguen viviendo en la
/// computadora y se reconstruyen desde `/v1/ide/agents/{id}`.
public struct IDEConversationReference: Codable, Sendable, Equatable, Hashable, Identifiable {
    public let id: String
    public var title: String
    public let workspaceId: String
    public var workspaceName: String
    public var sessionIds: [String]
    public let createdAt: Date
    public var updatedAt: Date

    public init(
        id: String = UUID().uuidString,
        title: String,
        workspaceId: String,
        workspaceName: String,
        sessionIds: [String],
        createdAt: Date = Date(),
        updatedAt: Date = Date()
    ) {
        self.id = id
        self.title = title
        self.workspaceId = workspaceId
        self.workspaceName = workspaceName
        self.sessionIds = sessionIds
        self.createdAt = createdAt
        self.updatedAt = updatedAt
    }

    /// Convierte una solicitud extensa en un título útil para el historial.
    /// Es determinista y local: no retrasa la creación esperando otro LLM.
    public static func compactTitle(from prompt: String) -> String {
        let normalized = prompt
            .replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return "Nueva sesión" }

        let lower = normalized.lowercased()
        if lower.contains("api key") || lower.contains("apikey") {
            let providers = [
                "OpenAI", "Anthropic", "ElevenLabs", "Google", "Gemini",
                "Twilio", "LinkedIn", "Meta", "X", "Alpaca"
            ]
            let provider = providers.first {
                lower.contains($0.lowercased())
            }
            return provider.map { "Configurar API Key · \($0)" } ?? "Configurar API Key"
        }

        let firstSentence = normalized
            .split(whereSeparator: { ".?!\n".contains($0) })
            .first
            .map(String.init) ?? normalized
        let words = firstSentence.split(separator: " ")
        let compact = words.prefix(8).joined(separator: " ")
        guard words.count > 8 || compact.count > 64 else { return compact }
        return String(compact.prefix(61)).trimmingCharacters(in: .whitespaces) + "…"
    }
}

/// Persistencia del índice de conversaciones. Nunca almacena eventos, salida
/// del terminal, contenido de archivos ni prompts completos.
public struct IDEConversationLocalStore {
    public static let storageKey = "\(IDELocalStateStore.storagePrefix)conversations.v1"

    private let defaults: UserDefaults
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
    }

    public func load() -> [IDEConversationReference] {
        guard let data = defaults.data(forKey: Self.storageKey),
              let decoded = try? decoder.decode([IDEConversationReference].self, from: data)
        else { return [] }
        return decoded.sorted { $0.updatedAt > $1.updatedAt }
    }

    /// Incorpora sesiones remotas que todavía no pertenecen a una
    /// conversación. No elimina referencias si la computadora está
    /// temporalmente desconectada o una sesión antigua deja de listarse.
    @discardableResult
    public func reconcile(
        remoteSessions: [IDESession]
    ) -> [IDEConversationReference] {
        var conversations = load()
        var claimed = Set(conversations.flatMap(\.sessionIds))

        for session in remoteSessions
            .filter({ $0.kind == "agent" })
            .sorted(by: { $0.startedAt < $1.startedAt })
        where !claimed.contains(session.id) {
            if let conversationId = session.conversationId,
               let index = conversations.firstIndex(where: { $0.id == conversationId }) {
                conversations[index].sessionIds.append(session.id)
                conversations[index].workspaceName = session.workspaceName
                conversations[index].updatedAt = session.endedAt ?? session.startedAt
                claimed.insert(session.id)
                continue
            }
            let title = session.title
                ?? IDEConversationReference.compactTitle(
                    from: "Sesión de \(session.workspaceName)"
                )
            conversations.append(
                IDEConversationReference(
                    id: session.conversationId ?? UUID().uuidString,
                    title: title,
                    workspaceId: session.workspaceId,
                    workspaceName: session.workspaceName,
                    sessionIds: [session.id],
                    createdAt: session.startedAt,
                    updatedAt: session.endedAt ?? session.startedAt
                )
            )
            claimed.insert(session.id)
        }

        save(conversations)
        return conversations.sorted { $0.updatedAt > $1.updatedAt }
    }

    /// Añade una nueva ejecución a una conversación existente o crea una.
    @discardableResult
    public func append(
        session: IDESession,
        prompt: String,
        to conversationId: String? = nil
    ) -> IDEConversationReference {
        var conversations = load()
        let now = Date()

        if let conversationId,
           let index = conversations.firstIndex(where: { $0.id == conversationId }) {
            if !conversations[index].sessionIds.contains(session.id) {
                conversations[index].sessionIds.append(session.id)
            }
            conversations[index].workspaceName = session.workspaceName
            conversations[index].updatedAt = now
            save(conversations)
            return conversations[index]
        }

        let conversation = IDEConversationReference(
            id: session.conversationId ?? UUID().uuidString,
            title: session.title ?? IDEConversationReference.compactTitle(from: prompt),
            workspaceId: session.workspaceId,
            workspaceName: session.workspaceName,
            sessionIds: [session.id],
            createdAt: session.startedAt,
            updatedAt: now
        )
        conversations.append(conversation)
        save(conversations)
        return conversation
    }

    public func rename(id: String, title: String) {
        let clean = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        var conversations = load()
        guard let index = conversations.firstIndex(where: { $0.id == id }) else { return }
        conversations[index].title = String(clean.prefix(80))
        conversations[index].updatedAt = Date()
        save(conversations)
    }

    public func remove(id: String) {
        save(load().filter { $0.id != id })
    }

    private func save(_ conversations: [IDEConversationReference]) {
        guard let data = try? encoder.encode(conversations) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}

// MARK: - Git

public struct IDEGitFile: Codable, Sendable, Equatable, Identifiable {
    public let path: String
    public let indexStatus: String
    public let worktreeStatus: String
    public let originalPath: String?

    public var id: String { "\(originalPath ?? ""):\(path)" }

    enum CodingKeys: String, CodingKey {
        case path
        case indexStatus = "index_status"
        case worktreeStatus = "worktree_status"
        case originalPath = "original_path"
    }

    public var isStaged: Bool { indexStatus != " " && indexStatus != "?" }
}

public struct IDEGitStatus: Codable, Sendable, Equatable {
    public let branch: String?
    public let upstream: String?
    public let ahead: Int
    public let behind: Int
    public let files: [IDEGitFile]
}

public struct IDEGitDiff: Codable, Sendable, Equatable {
    public let text: String
    public let truncated: Bool
}

public struct IDEGitCommit: Codable, Sendable, Equatable, Identifiable {
    public let hash: String
    public let shortHash: String
    public let author: String
    public let email: String
    public let timestamp: Date
    public let subject: String

    public var id: String { hash }

    enum CodingKeys: String, CodingKey {
        case hash, author, email, timestamp, subject
        case shortHash = "short_hash"
    }
}

public struct IDEGitLog: Codable, Sendable, Equatable {
    public let commits: [IDEGitCommit]
}

// MARK: - Estado local no sensible

/// Conserva únicamente IDs opacos. El contenido del código, del terminal y
/// de los prompts permanece en la computadora y se reconstruye por cursor.
public struct IDELocalStateStore {
    public static let storagePrefix = "edecan.ide."

    private let defaults: UserDefaults

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var selectedWorkspaceId: String? {
        get { nonEmpty(defaults.string(forKey: "\(Self.storagePrefix)workspace")) }
        nonmutating set { set(newValue, key: "\(Self.storagePrefix)workspace") }
    }

    public func selectedSessionId(kind: String, workspaceId: String) -> String? {
        nonEmpty(defaults.string(forKey: sessionKey(kind: kind, workspaceId: workspaceId)))
    }

    public func setSelectedSessionId(_ value: String?, kind: String, workspaceId: String) {
        set(value, key: sessionKey(kind: kind, workspaceId: workspaceId))
    }

    public func clearAll() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.storagePrefix) {
            defaults.removeObject(forKey: key)
        }
    }

    private func sessionKey(kind: String, workspaceId: String) -> String {
        "\(Self.storagePrefix)session.\(kind).\(workspaceId)"
    }

    private func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }

    private func set(_ value: String?, key: String) {
        if let value, !value.isEmpty { defaults.set(value, forKey: key) }
        else { defaults.removeObject(forKey: key) }
    }
}
