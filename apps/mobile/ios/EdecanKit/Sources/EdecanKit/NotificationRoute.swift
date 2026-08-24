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
/// más, cuando aplica, la conversación exacta, la llamada en vivo o la
/// misión a abrir dentro de ella.
///
/// Separado de ``NotificationRoute`` a propósito: esa allowlist de rutas no
/// cambia de forma, y esto solo añade los datos opcionales de "a qué
/// conversación" (`chat_id`), "a qué llamada" o "a qué misión"
/// (`resource_id`) que manda el worker
/// (`edecan_core.notifications.ImportantNotificationEvent.push_data`).
public struct NotificationDestino: Sendable, Equatable {
    public let route: NotificationRoute
    public let conversationId: String?
    /// Frente 6 (deeplink de llamada): id de la llamada ENTRANTE a abrir en
    /// vivo. Solo se rellena para `event == "phone_call_incoming"` — ver
    /// ``parseCallId(userInfo:)``.
    public let callId: String?
    /// Misión puntual de `work_failed` / `work_completed`. El `route` del
    /// worker sigue siendo `activity`, pero Actividad es solo una grilla de
    /// atajos: sin este id el tap no muestra el trabajo.
    public let missionId: String?

    public init(
        route: NotificationRoute,
        conversationId: String?,
        callId: String? = nil,
        missionId: String? = nil
    ) {
        self.route = route
        self.conversationId = conversationId
        self.callId = callId
        self.missionId = missionId
    }

    public static func parse(userInfo: [AnyHashable: Any]) -> NotificationDestino {
        let route = NotificationRoute.parse(userInfo: userInfo)

        // Una llamada ENTRANTE gana sobre cualquier `chat_id` residual: hay
        // que abrirla en vivo, no el chat.
        if let callId = parseCallId(userInfo: userInfo) {
            return NotificationDestino(
                route: .activity, conversationId: nil, callId: callId, missionId: nil
            )
        }

        // `create_linkedin_post` / `create_organization_linkedin_post` mandan
        // `kind=work_failed` con `route: "activity"` Y `chat_id` a propósito:
        // el texto vive en esa conversación. Ignorar `chat_id` porque la
        // ruta no es `assistant` dejaba a la persona en Actividad vacía.
        if let conversationId = parseNonEmpty(userInfo["chat_id"])
            ?? parseDeeplinkId(userInfo["deeplink"], host: "chat") {
            return NotificationDestino(
                route: .assistant, conversationId: conversationId, callId: nil, missionId: nil
            )
        }

        let event = userInfo["event"] as? String
        if event == "work_failed" || event == "work_completed",
           let missionId = parseNonEmpty(userInfo["resource_id"])
            ?? parseDeeplinkId(userInfo["deeplink"], host: "activity") {
            return NotificationDestino(
                route: .activity, conversationId: nil, callId: nil, missionId: missionId
            )
        }

        return NotificationDestino(route: route, conversationId: nil, callId: nil, missionId: nil)
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
        if let resourceId = parseNonEmpty(userInfo["resource_id"]) {
            return resourceId
        }
        return parseDeeplinkId(userInfo["deeplink"], host: "activity")
    }

    private static func parseNonEmpty(_ raw: Any?) -> String? {
        guard let bruto = (raw as? String)?.trimmingCharacters(in: .whitespacesAndNewlines),
              !bruto.isEmpty else { return nil }
        return bruto
    }

    private static func parseDeeplinkId(_ raw: Any?, host: String) -> String? {
        guard let deeplink = raw as? String,
              let url = URL(string: deeplink),
              url.scheme == "edecan", url.host == host
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
