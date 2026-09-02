import Foundation

// MARK: - Actividad (`GET /v1/activity`, contrato en paralelo)

/// Una entrada del registro de actividad reciente del tenant. El contrato en
/// paralelo no fija un `id`, así que la identidad se deriva de los campos que
/// sí trae (`type` + `agent` + `at` + `summary`) — suficiente para pintar una
/// lista estable. Todo se decodifica con tolerancia (`decodeIfPresent`/`??`)
/// para que una fila parcial no tumbe el feed entero (mismo criterio que
/// `CollaborationModels`).
public struct ActivityEvent: Codable, Sendable, Equatable, Identifiable {
    public let type: String
    public let agent: String?
    public let summary: String
    public let at: Date?
    public let status: String?

    enum CodingKeys: String, CodingKey {
        case type, agent, summary, at, status
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        type = (try? container.decode(String.self, forKey: .type)) ?? "evento"
        agent = try container.decodeIfPresent(String.self, forKey: .agent)
        summary = (try? container.decode(String.self, forKey: .summary)) ?? ""
        at = try? container.decode(Date.self, forKey: .at)
        status = try container.decodeIfPresent(String.self, forKey: .status)
    }

    public var id: String {
        "\(type)|\(agent ?? "")|\(at?.timeIntervalSince1970 ?? 0)|\(summary)"
    }
}