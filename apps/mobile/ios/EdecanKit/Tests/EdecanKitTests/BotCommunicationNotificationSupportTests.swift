import Testing
@testable import EdecanKit

@Test func botCommunicationCategoryEsEstable() {
    #expect(BotCommunicationNotificationSupport.categoryIdentifier == "EDECAN_BOT_MESSAGE")
    #expect(BotCommunicationNotificationSupport.eventKind == "agent_bot_message")
}

@Test func esMensajeDeBotSoloConEventKindCorrecto() {
    #expect(BotCommunicationNotificationSupport.esMensajeDeBot(userInfo: ["event": "agent_bot_message"]))
    #expect(!BotCommunicationNotificationSupport.esMensajeDeBot(userInfo: ["event": "agent_message"]))
    #expect(!BotCommunicationNotificationSupport.esMensajeDeBot(userInfo: [:]))
}

@Test func personalizacionExtraeAvatarYNombre() {
    let userInfo: [AnyHashable: Any] = [
        "event": "agent_bot_message",
        "sender_id": "w-1",
        "sender_display_name": "Astra",
        "avatar_shape": "hexagon",
        "avatar_fill": "#6366f1",
        "avatar_accent": "#22c55e",
        "chat_id": "c-99",
    ]
    let datos = BotCommunicationNotificationSupport.personalizacion(
        userInfo: userInfo,
        fallbackTitle: "Fallback",
        fallbackBody: "Hola bot"
    )
    #expect(datos?.senderId == "w-1")
    #expect(datos?.displayName == "Astra")
    #expect(datos?.shape == "hexagon")
    #expect(datos?.fillHex == "#6366f1")
    #expect(datos?.accentHex == "#22c55e")
    #expect(datos?.conversationId == "c-99")
    #expect(datos?.body == "Hola bot")
    #expect(datos?.avatarFromPayload == true)
}

@Test func personalizacionSinAvatarMarcaFallbackALogo() {
    let userInfo: [AnyHashable: Any] = [
        "event": "agent_bot_message",
        "sender_id": "w-2",
        "sender_display_name": "Bot",
    ]
    let datos = BotCommunicationNotificationSupport.personalizacion(
        userInfo: userInfo,
        fallbackTitle: nil,
        fallbackBody: "Hola"
    )
    #expect(datos?.avatarFromPayload == false)
    #expect(datos?.shape == "circle")
    #expect(datos?.fillHex == "#6366f1")
}

@Test func personalizacionIgnoraEventosQueNoSonDeBot() {
    let userInfo: [AnyHashable: Any] = ["event": "work_completed"]
    let datos = BotCommunicationNotificationSupport.personalizacion(
        userInfo: userInfo,
        fallbackTitle: "T",
        fallbackBody: "C"
    )
    #expect(datos == nil)
}
