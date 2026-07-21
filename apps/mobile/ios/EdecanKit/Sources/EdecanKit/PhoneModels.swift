import Foundation

/// Resumen tenant-scoped de una llamada real. Los estados quedan abiertos
/// porque Twilio y el backend pueden sumar estados sin romper clientes viejos.
public struct PhoneCallOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let conversationId: String
    public let direction: String
    public let fromE164: String
    public let toE164: String
    public let goal: String
    public let status: String
    public let confirmedAt: Date?
    public let startedAt: Date?
    public let endedAt: Date?
    public let durationSeconds: Int?
    public let error: String?
    public let createdAt: Date?
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, direction, goal, status, error
        case conversationId = "conversation_id"
        case fromE164 = "from_e164"
        case toE164 = "to_e164"
        case confirmedAt = "confirmed_at"
        case startedAt = "started_at"
        case endedAt = "ended_at"
        case durationSeconds = "duration_seconds"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}
