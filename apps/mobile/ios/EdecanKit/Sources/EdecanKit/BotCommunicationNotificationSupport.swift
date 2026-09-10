import Foundation

/// Identificadores APNs compartidos entre la app y la extensión de servicio
/// de notificaciones para mensajes de **Edecán Bots** (estilo Communication).
public enum BotCommunicationNotificationSupport {
    public static let categoryIdentifier = "EDECAN_BOT_MESSAGE"
    public static let eventKind = "agent_bot_message"

    public static func esMensajeDeBot(userInfo: [AnyHashable: Any]) -> Bool {
        (userInfo["event"] as? String) == eventKind
    }

    public struct Personalizacion: Equatable, Sendable {
        public let senderId: String
        public let displayName: String
        public let shape: String
        public let fillHex: String
        public let accentHex: String?
        public let conversationId: String?
        public let body: String
        /// `true` cuando el push trae `avatar_shape` y `avatar_fill` del worker
        /// (misma fuente que `GrokFaceAvatar` / `CaraOrbe`).
        public let avatarFromPayload: Bool
        public let leftEye: PushAvatarEye?
        public let rightEye: PushAvatarEye?

        public init(
            senderId: String,
            displayName: String,
            shape: String,
            fillHex: String,
            accentHex: String?,
            conversationId: String?,
            body: String,
            avatarFromPayload: Bool,
            leftEye: PushAvatarEye? = nil,
            rightEye: PushAvatarEye? = nil
        ) {
            self.senderId = senderId
            self.displayName = displayName
            self.shape = shape
            self.fillHex = fillHex
            self.accentHex = accentHex
            self.conversationId = conversationId
            self.body = body
            self.avatarFromPayload = avatarFromPayload
            self.leftEye = leftEye
            self.rightEye = rightEye
        }
    }

    /// Extrae los campos de personalización del `userInfo` del push de bot.
    public static func personalizacion(
        userInfo: [AnyHashable: Any],
        fallbackTitle: String?,
        fallbackBody: String
    ) -> Personalizacion? {
        guard esMensajeDeBot(userInfo: userInfo) else { return nil }
        let senderId = (userInfo["sender_id"] as? String)
            ?? (userInfo["worker_id"] as? String)
            ?? "bot"
        let displayName = (userInfo["sender_display_name"] as? String)
            ?? fallbackTitle
            ?? "Bot"
        let shape = (userInfo["avatar_shape"] as? String) ?? "circle"
        let fill = (userInfo["avatar_fill"] as? String) ?? "#6366f1"
        let accent = userInfo["avatar_accent"] as? String
        let conversationId = userInfo["chat_id"] as? String
        let avatarFromPayload = userInfo["avatar_shape"] != nil && userInfo["avatar_fill"] != nil
        let ojos = ojosDesdePayload(userInfo: userInfo)
        return Personalizacion(
            senderId: senderId,
            displayName: String(displayName.prefix(80)),
            shape: shape,
            fillHex: fill,
            accentHex: accent,
            conversationId: conversationId,
            body: fallbackBody,
            avatarFromPayload: avatarFromPayload,
            leftEye: ojos.left,
            rightEye: ojos.right
        )
    }

    /// Parsea `avatar_eyes` del push (dict o JSON) cuando el worker lo incluya.
    static func ojosDesdePayload(userInfo: [AnyHashable: Any]) -> (left: PushAvatarEye?, right: PushAvatarEye?) {
        let eyesRoot: [String: Any]?
        if let dict = userInfo["avatar_eyes"] as? [String: Any] {
            eyesRoot = dict
        } else if let json = userInfo["avatar_eyes"] as? String,
                  let data = json.data(using: .utf8),
                  let dict = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            eyesRoot = dict
        } else {
            eyesRoot = nil
        }
        guard let eyesRoot else { return (nil, nil) }
        return (ojoDesde(dict: eyesRoot["left"] as? [String: Any]), ojoDesde(dict: eyesRoot["right"] as? [String: Any]))
    }

    private static func ojoDesde(dict: [String: Any]?) -> PushAvatarEye? {
        guard let dict else { return nil }
        guard let x = numero(dict["x"]), let y = numero(dict["y"]),
              let rx = numero(dict["rx"]), let ry = numero(dict["ry"]) else {
            return nil
        }
        let rotation = numero(dict["rotation"]) ?? 0
        return PushAvatarEye(x: x, y: y, rx: rx, ry: ry, rotation: rotation)
    }

    private static func numero(_ valor: Any?) -> CGFloat? {
        if let n = valor as? Double { return CGFloat(n) }
        if let n = valor as? Int { return CGFloat(n) }
        if let n = valor as? NSNumber { return CGFloat(truncating: n) }
        if let s = valor as? String, let n = Double(s) { return CGFloat(n) }
        return nil
    }
}
