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
}
