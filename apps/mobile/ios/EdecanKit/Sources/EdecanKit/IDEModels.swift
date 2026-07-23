import Foundation

// MARK: - `/v1/ide/*` — IDE embebido de solo lectura sobre el companion de
// escritorio (`apps/api/edecan_api/routers/ide.py`, `apps/companion/edecan_companion/actions.py`).
// Este cliente solo consume `status`/`tree`/`file` (lectura); escribir/editar/
// correr comandos queda fuera del alcance de la app móvil v1.

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
