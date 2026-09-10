import EdecanKit
import Intents
import UserNotifications

/// Enriquece pushes de **Edecán Bots** con `INSendMessageIntent` y avatar
/// renderizado (Communication Notifications). No toca categorías del chat
/// principal ni gym/aprobación.
final class NotificationService: UNNotificationServiceExtension {
    private var contentHandler: ((UNNotificationContent) -> Void)?
    private var bestAttemptContent: UNMutableNotificationContent?

    override func didReceive(
        _ request: UNNotificationRequest,
        withContentHandler contentHandler: @escaping (UNNotificationContent) -> Void
    ) {
        self.contentHandler = contentHandler
        guard let content = request.content.mutableCopy() as? UNMutableNotificationContent else {
            contentHandler(request.content)
            return
        }
        bestAttemptContent = content

        guard BotCommunicationNotificationSupport.esMensajeDeBot(userInfo: content.userInfo) else {
            contentHandler(content)
            return
        }

        let titulo = content.title
        let cuerpo = content.body
        guard let datos = BotCommunicationNotificationSupport.personalizacion(
            userInfo: content.userInfo,
            fallbackTitle: titulo.isEmpty ? nil : titulo,
            fallbackBody: cuerpo
        ) else {
            contentHandler(content)
            return
        }

        // Síncrono a propósito: no hay await real y evita los data-race de
        // Swift 6 con `contentHandler`/`content` dentro de un Task.
        let enriched = enriquecer(content, datos: datos)
        contentHandler(enriched)
    }

    override func serviceExtensionTimeWillExpire() {
        if let contentHandler, let bestAttemptContent {
            contentHandler(bestAttemptContent)
        }
    }

    private func enriquecer(
        _ content: UNMutableNotificationContent,
        datos: BotCommunicationNotificationSupport.Personalizacion
    ) -> UNNotificationContent {
        let avatarPNG = BotPushAvatarRenderer.senderPNGData(
            avatarFromPayload: datos.avatarFromPayload,
            shape: datos.shape,
            fillHex: datos.fillHex,
            accentHex: datos.accentHex,
            displayName: datos.displayName,
            leftEye: datos.leftEye,
            rightEye: datos.rightEye
        )
        let avatarImage = avatarPNG.flatMap { INImage(imageData: $0) }

        let handle = INPersonHandle(value: datos.senderId, type: .unknown)
        let sender = INPerson(
            personHandle: handle,
            nameComponents: nil,
            displayName: datos.displayName,
            image: avatarImage,
            contactIdentifier: nil,
            customIdentifier: datos.senderId
        )

        let intent = INSendMessageIntent(
            recipients: nil,
            outgoingMessageType: .outgoingMessageText,
            content: datos.body,
            speakableGroupName: nil,
            conversationIdentifier: datos.conversationId,
            serviceName: BotPushAvatarRenderer.serviceDisplayName,
            sender: sender,
            attachments: nil
        )
        intent.setImage(avatarImage, forParameterNamed: \.sender)

        do {
            let updated = try content.updating(from: intent)
            return updated
        } catch {
            return content
        }
    }
}
