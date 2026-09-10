import Foundation

public struct SkillSummary: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let nombre: String
    public let descripcion: String
    public let version: String
    public let enabled: Bool
    public let trustTier: String
    public let capabilities: [String]

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, version, enabled, capabilities
        case trustTier = "trust_tier"
    }
}

public struct SkillsEnvelope: Codable, Sendable, Equatable {
    public let skills: [SkillSummary]
}

public struct MCPServerSummary: Codable, Sendable, Equatable, Identifiable {
    public var id: String { nombre }
    public let nombre: String
    public let transporte: String
    public let url: String?
    public let comando: String?
    public let estado: String
    /// Headers/env configurados en el vault (`autenticacion_configurada`).
    public let autenticacionConfigurada: Bool
    /// Salud: `operational` / `degraded` / `auth_required` / `unavailable` / …
    public let health: String?
    public let latencyMs: Int?
    public let lastError: String?

    enum CodingKeys: String, CodingKey {
        case nombre, transporte, url, comando, estado, health
        case autenticacionConfigurada = "autenticacion_configurada"
        case latencyMs = "latency_ms"
        case lastError = "last_error"
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        nombre = try c.decode(String.self, forKey: .nombre)
        transporte = try c.decode(String.self, forKey: .transporte)
        url = try c.decodeIfPresent(String.self, forKey: .url)
        comando = try c.decodeIfPresent(String.self, forKey: .comando)
        estado = try c.decodeIfPresent(String.self, forKey: .estado) ?? ""
        autenticacionConfigurada = try c.decodeIfPresent(Bool.self, forKey: .autenticacionConfigurada) ?? false
        health = try c.decodeIfPresent(String.self, forKey: .health)
        latencyMs = try c.decodeIfPresent(Int.self, forKey: .latencyMs)
        lastError = try c.decodeIfPresent(String.self, forKey: .lastError)
    }

    public var estaConectado: Bool { estado == "active" }

    public var necesitaAuth: Bool {
        (health ?? "").lowercased() == "auth_required" || (!autenticacionConfigurada && transporte == "http")
    }

    public var etiquetaSalud: String {
        switch (health ?? "").lowercased() {
        case "operational", "healthy": return "OK"
        case "degraded": return "Degradado"
        case "auth_required": return "Auth requerida"
        case "unavailable", "down": return "Caído"
        case "": return estado.isEmpty ? "Sin datos" : estado
        default: return health ?? (estado.isEmpty ? "Sin datos" : estado)
        }
    }

    public var colorSalud: String {
        switch (health ?? "").lowercased() {
        case "operational", "healthy": return "green"
        case "degraded": return "orange"
        case "auth_required": return "purple"
        case "unavailable", "down": return "red"
        default: return "secondary"
        }
    }
}

public struct MCPToolSummary: Codable, Sendable, Equatable, Identifiable {
    public var id: String { name }
    public let name: String
    public let description: String
}

public struct MCPToolsEnvelope: Codable, Sendable, Equatable {
    public let tools: [MCPToolSummary]
}

/// Entrada de salud por servidor en `GET /v1/mcp/health`.
public struct MCPServerHealthEntry: Codable, Sendable, Equatable, Identifiable {
    public var id: String { serverName }
    public let serverName: String
    public let health: String
    public let latencyMs: Int?
    public let lastError: String?
    public let lastCheckedAt: String?

    enum CodingKeys: String, CodingKey {
        case health
        case serverName = "server_name"
        case latencyMs = "latency_ms"
        case lastError = "last_error"
        case lastCheckedAt = "last_checked_at"
    }
}

/// Resumen agregado `GET /v1/mcp/health` (`edecan-mcp-health.v1`).
public struct MCPHealthSummary: Codable, Sendable, Equatable {
    public let format: String
    public let status: String
    public let configured: Int
    public let checked: Int
    public let unchecked: Int
    public let byStatus: [String: Int]
    public let operationalRate: Double?
    public let avgLatencyMs: Double?
    public let maxLatencyMs: Int?
    public let servers: [MCPServerHealthEntry]

    enum CodingKeys: String, CodingKey {
        case format, status, configured, checked, unchecked, servers
        case byStatus = "by_status"
        case operationalRate = "operational_rate"
        case avgLatencyMs = "avg_latency_ms"
        case maxLatencyMs = "max_latency_ms"
    }

    public var etiquetaEstado: String {
        switch status.lowercased() {
        case "operational", "healthy": return "Operativo"
        case "degraded": return "Degradado"
        case "unavailable", "down": return "No disponible"
        case "auth_required": return "Auth requerida"
        default: return status.capitalized
        }
    }
}

/// Cuerpo de `PUT /v1/mcp/servers` (`MCPServerIn` en el backend).
public struct MCPServerInput: Encodable, Sendable {
    public let nombre: String
    public let transporte: String
    public let url: String?
    public let comando: String?
    public let headers: [String: String]?
    public let env: [String: String]?
    public let validate: Bool?

    public init(
        nombre: String,
        transporte: String,
        url: String? = nil,
        comando: String? = nil,
        headers: [String: String]? = nil,
        env: [String: String]? = nil,
        validate: Bool = true
    ) {
        self.nombre = nombre
        self.transporte = transporte
        self.url = url
        self.comando = comando
        self.headers = headers
        self.env = env
        self.validate = validate
    }
}
