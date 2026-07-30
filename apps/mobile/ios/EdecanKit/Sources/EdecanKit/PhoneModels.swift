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
    /// Eventos crudos de `phone_call_events` (turnos, susurros…). Solo viene
    /// poblado en el detalle (`APIClient.obtenerLlamada(id:)`, frente 6c) — el
    /// listado (`listarLlamadas()`) no lo trae, así que aquí siempre es `nil`.
    public let events: [PhoneCallEventOut]?

    enum CodingKeys: String, CodingKey {
        case id, direction, goal, agent, status, error, summary, events
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

/// Un evento crudo de `phone_call_events` tal como lo expone `GET
/// /v1/phone/calls/{id}` (`_out(row, events=events)` en `phone.py`). El
/// backend persiste ahí tanto los turnos de transcripción
/// (`event_type == "transcript"`, `payload = {role, text}`) como los
/// susurros del dueño (`event_type == "susurro"`, `payload = {text}`) y
/// otros tipos internos (`cancelled`, `susurro_consumido`…) que esta pantalla
/// ignora. `role`/`text` quedan `nil` cuando el tipo de evento no los trae.
public struct PhoneCallEventOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let eventType: String
    public let occurredAt: Date?
    public let role: String?
    public let text: String?

    public init(id: String, eventType: String, occurredAt: Date?, role: String?, text: String?) {
        self.id = id
        self.eventType = eventType
        self.occurredAt = occurredAt
        self.role = role
        self.text = text
    }

    enum CodingKeys: String, CodingKey {
        case id, payload
        case eventType = "event_type"
        case occurredAt = "occurred_at"
    }

    private enum PayloadKeys: String, CodingKey {
        case role, text
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        id = try values.decode(String.self, forKey: .id)
        eventType = try values.decode(String.self, forKey: .eventType)
        occurredAt = try values.decodeIfPresent(Date.self, forKey: .occurredAt)
        let payload = try? values.nestedContainer(keyedBy: PayloadKeys.self, forKey: .payload)
        role = try payload?.decodeIfPresent(String.self, forKey: .role)
        text = try payload?.decodeIfPresent(String.self, forKey: .text)
    }

    /// Nunca se manda de vuelta al servidor — solo existe para que
    /// `PhoneCallOut` (que sí necesita `Encodable` por síntesis, al tener
    /// este tipo como campo) siga compilando.
    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(eventType, forKey: .eventType)
        try container.encodeIfPresent(occurredAt, forKey: .occurredAt)
        var payloadContainer = container.nestedContainer(keyedBy: PayloadKeys.self, forKey: .payload)
        try payloadContainer.encodeIfPresent(role, forKey: .role)
        try payloadContainer.encodeIfPresent(text, forKey: .text)
    }
}

/// Respuesta de `POST /v1/phone/calls/{id}/susurro` (frente 6a):
/// confirmación de que el texto quedó encolado, con el mismo `id` que va a
/// tener el evento `susurro` cuando aparezca en `PhoneCallOut.events` — así
/// la pantalla en vivo puede mostrarlo optimista sin duplicarlo al llegar el
/// siguiente sondeo.
public struct PhoneCallWhisperOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let callId: String
    public let text: String
    public let queuedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, text
        case callId = "call_id"
        case queuedAt = "queued_at"
    }
}
