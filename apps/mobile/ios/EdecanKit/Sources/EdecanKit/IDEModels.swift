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

    enum CodingKeys: String, CodingKey {
        case id, kind, status, command, provider, title
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

    public var id: Int { cursor }
}

public struct IDESessionReadOut: Codable, Sendable, Equatable {
    public let session: IDESession
    public let events: [IDESessionEvent]
    public let nextCursor: Int

    enum CodingKeys: String, CodingKey {
        case session, events
        case nextCursor = "next_cursor"
    }
}

public enum IDEAgentProvider: String, Codable, CaseIterable, Sendable, Identifiable {
    case auto, codex, claude

    public var id: String { rawValue }

    public var label: String {
        switch self {
        case .auto: "Automático"
        case .codex: "Codex"
        case .claude: "Claude"
        }
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
