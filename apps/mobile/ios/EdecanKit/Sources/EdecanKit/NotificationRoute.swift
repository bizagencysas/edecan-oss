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
/// más, cuando aplica, la conversación exacta o la llamada en vivo a abrir
/// dentro de ella.
///
/// Separado de ``NotificationRoute`` a propósito: esa allowlist de rutas no
/// cambia de forma, y esto solo añade los datos opcionales de "a qué
/// conversación" (`chat_id`) o "a qué llamada" (`resource_id`) que manda el
/// worker (`edecan_core.notifications.ImportantNotificationEvent.push_data`).
public struct NotificationDestino: Sendable, Equatable {
    public let route: NotificationRoute
    public let conversationId: String?
    /// Frente 6 (deeplink de llamada): id de la llamada ENTRANTE a abrir en
    /// vivo. Solo se rellena para `event == "phone_call_incoming"` — ver
    /// ``parseCallId(userInfo:)``.
    public let callId: String?

    public init(route: NotificationRoute, conversationId: String?, callId: String? = nil) {
        self.route = route
        self.conversationId = conversationId
        self.callId = callId
    }

    public static func parse(userInfo: [AnyHashable: Any]) -> NotificationDestino {
        let route = NotificationRoute.parse(userInfo: userInfo)
        switch route {
        case .assistant:
            // `chat_id` solo tiene sentido para la pestaña del asistente. Un
            // evento futuro que combine `route: "activity"` con un `chat_id`
            // residual no debe intentar abrir una conversación ahí.
            let bruto = (userInfo["chat_id"] as? String)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
            let conversationId = (bruto?.isEmpty ?? true) ? nil : bruto
            return NotificationDestino(route: route, conversationId: conversationId, callId: nil)
        case .activity:
            return NotificationDestino(route: route, conversationId: nil, callId: parseCallId(userInfo: userInfo))
        case .settings, .create, .remote:
            return NotificationDestino(route: route, conversationId: nil, callId: nil)
        }
    }

    /// `route: "activity"` + `resource_id` NO alcanza para saber que es una
    /// llamada: automatizaciones, recordatorios y reparaciones locales
    /// mandan exactamente la misma forma (mismo `deeplink
    /// edecan://activity/{resource_id}`, ver
    /// `ImportantNotificationEvent.push_data` en `edecan_core.notifications`).
    /// Lo único que distingue la llamada ENTRANTE es `event ==
    /// "phone_call_incoming"`. El push del RESUMEN post-llamada
    /// (`notify_phone_call_summary.py`) también trae `resource_id` pero sin
    /// `event`, y a propósito NO debe abrir esta pantalla: la llamada ya
    /// terminó y no hay nada "en vivo" que mostrar, así que cae en Actividad
    /// como cualquier otro aviso.
    private static func parseCallId(userInfo: [AnyHashable: Any]) -> String? {
        guard (userInfo["event"] as? String) == "phone_call_incoming" else { return nil }
        if let resourceId = (userInfo["resource_id"] as? String)?
            .trimmingCharacters(in: .whitespacesAndNewlines), !resourceId.isEmpty {
            return resourceId
        }
        // Red de respaldo: el mismo id vive al final del deeplink
        // `edecan://activity/{call_id}`.
        guard let deeplink = userInfo["deeplink"] as? String,
              let url = URL(string: deeplink),
              url.scheme == "edecan", url.host == "activity"
        else { return nil }
        let id = url.path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        return id.isEmpty ? nil : id
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
