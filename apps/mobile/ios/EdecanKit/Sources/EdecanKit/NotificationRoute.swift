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

/// Destino completo de una notificación: la pestaña (``NotificationRoute``)
/// más, cuando aplica, la conversación exacta a abrir dentro de ella.
///
/// Separado de ``NotificationRoute`` a propósito: esa allowlist de rutas no
/// cambia de forma, y esto solo añade el dato opcional de "a qué
/// conversación" que manda el worker en `chat_id`
/// (`edecan_core.notifications.ImportantNotificationEvent.push_data`).
public struct NotificationDestino: Sendable, Equatable {
    public let route: NotificationRoute
    public let conversationId: String?

    public init(route: NotificationRoute, conversationId: String?) {
        self.route = route
        self.conversationId = conversationId
    }

    public static func parse(userInfo: [AnyHashable: Any]) -> NotificationDestino {
        let route = NotificationRoute.parse(userInfo: userInfo)
        // `chat_id` solo tiene sentido para la pestaña del asistente. Un
        // evento futuro que combine `route: "activity"` con un `chat_id`
        // residual no debe intentar abrir una conversación ahí.
        guard route == .assistant else {
            return NotificationDestino(route: route, conversationId: nil)
        }
        let bruto = (userInfo["chat_id"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        let conversationId = (bruto?.isEmpty ?? true) ? nil : bruto
        return NotificationDestino(route: route, conversationId: conversationId)
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
