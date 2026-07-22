import Foundation

/// Identidad pública del agente que atendió o realizó la llamada. Los
/// campos son opcionales porque las llamadas antiguas pueden no haber sido
/// creadas desde una plantilla.
public struct PhoneCallAgentOut: Codable, Sendable, Equatable {
    public let templateId: String?
    public let templateName: String?
    public let name: String?

    enum CodingKeys: String, CodingKey {
        case name
        case templateId = "template_id"
        case templateName = "template_name"
    }
}

/// Persona que participó en la conversación telefónica. El backend no
/// expone datos privados adicionales: solo rol, nombre legible y teléfono.
public struct PhoneCallParticipantOut: Codable, Sendable, Equatable {
    public let role: String
    public let name: String?
    public let phoneE164: String?

    enum CodingKeys: String, CodingKey {
        case role, name
        case phoneE164 = "phone_e164"
    }
}

public struct PhoneCallTranscriptOut: Codable, Sendable, Equatable {
    public let available: Bool
    public let turnCount: Int

    public init(available: Bool = false, turnCount: Int = 0) {
        self.available = available
        self.turnCount = turnCount
    }

    enum CodingKeys: String, CodingKey {
        case available
        case turnCount = "turn_count"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        available = try values.decodeIfPresent(Bool.self, forKey: .available) ?? false
        turnCount = try values.decodeIfPresent(Int.self, forKey: .turnCount) ?? 0
    }
}

/// Resumen humano que el servidor produce al cerrar una llamada. Las listas
/// tienen defaults tolerantes para que una versión vieja o un resumen
/// parcial nunca impidan abrir todo el historial.
public struct PhoneCallSummaryOut: Codable, Sendable, Equatable {
    public let version: Int
    public let status: String
    public let direction: String
    public let participants: [PhoneCallParticipantOut]
    public let durationSeconds: Int?
    public let keyPoints: [String]
    public let commitments: [String]
    public let nextSteps: [String]
    public let transcript: PhoneCallTranscriptOut

    enum CodingKeys: String, CodingKey {
        case version, status, direction, participants, commitments, transcript
        case durationSeconds = "duration_seconds"
        case keyPoints = "key_points"
        case nextSteps = "next_steps"
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        version = try values.decodeIfPresent(Int.self, forKey: .version) ?? 1
        status = try values.decodeIfPresent(String.self, forKey: .status) ?? ""
        direction = try values.decodeIfPresent(String.self, forKey: .direction) ?? ""
        participants = try values.decodeIfPresent([PhoneCallParticipantOut].self, forKey: .participants) ?? []
        durationSeconds = try values.decodeIfPresent(Int.self, forKey: .durationSeconds)
        keyPoints = try values.decodeIfPresent([String].self, forKey: .keyPoints) ?? []
        commitments = try values.decodeIfPresent([String].self, forKey: .commitments) ?? []
        nextSteps = try values.decodeIfPresent([String].self, forKey: .nextSteps) ?? []
        transcript = try values.decodeIfPresent(PhoneCallTranscriptOut.self, forKey: .transcript) ?? .init()
    }
}

/// Resumen tenant-scoped de una llamada real. Los estados quedan abiertos
/// porque Twilio y el backend pueden sumar estados sin romper clientes viejos.
public struct PhoneCallOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let conversationId: String
    public let direction: String
    public let fromE164: String
    public let toE164: String
    public let goal: String
    public let agent: PhoneCallAgentOut?
    public let status: String
    public let confirmedAt: Date?
    public let startedAt: Date?
    public let endedAt: Date?
    public let durationSeconds: Int?
    public let error: String?
    public let summary: PhoneCallSummaryOut?
    public let summaryGeneratedAt: Date?
    public let createdAt: Date?
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, direction, goal, agent, status, error, summary
        case conversationId = "conversation_id"
        case fromE164 = "from_e164"
        case toE164 = "to_e164"
        case confirmedAt = "confirmed_at"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case durationSeconds = "duration_seconds"
        case summaryGeneratedAt = "summary_generated_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}
