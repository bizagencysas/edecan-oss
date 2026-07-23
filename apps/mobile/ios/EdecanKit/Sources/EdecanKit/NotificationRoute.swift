import Foundation

/// Destinos cerrados que una notificación puede abrir. Payloads nuevos o
/// manipulados caen al asistente en vez de convertirse en rutas arbitrarias.
public enum NotificationRoute: String, Sendable, Equatable, CaseIterable {
    case assistant
    case activity
    case settings
    case create
    case remote

    public static func parse(userInfo: [AnyHashable: Any]) -> NotificationRoute {
        let raw = (userInfo["route"] as? String)
            ?? (userInfo["screen"] as? String)
            ?? "assistant"
        return NotificationRoute(rawValue: raw.lowercased()) ?? .assistant
    }
}

public struct PushStatus: Codable, Sendable, Equatable {
    public let apns: Bool
    public let fcm: Bool
    public let devicesWithToken: Int

    enum CodingKeys: String, CodingKey {
        case apns, fcm
        case devicesWithToken = "devices_con_token"
    }
}
