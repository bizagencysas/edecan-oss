import Testing
@testable import EdecanKit

@Test func notificationRouteUsaAllowlist() {
    #expect(NotificationRoute.parse(userInfo: ["route": "activity"]) == .activity)
    #expect(NotificationRoute.parse(userInfo: ["screen": "settings"]) == .settings)
    #expect(NotificationRoute.parse(userInfo: ["route": "https://evil.test"]) == .assistant)
    #expect(NotificationRoute.parse(userInfo: [:]) == .assistant)
}

// Frente 3 (deeplink): tocar el push de una conversación debe abrir esa
// conversación, no solo la pestaña del asistente.
@Test func notificationDestinoLeeChatIdCuandoLaRutaEsAsistente() {
    let destino = NotificationDestino.parse(userInfo: ["route": "assistant", "chat_id": "abc-123"])
    #expect(destino.route == .assistant)
    #expect(destino.conversationId == "abc-123")
}

@Test func notificationDestinoIgnoraChatIdFueraDelAsistente() {
    // Un evento de actividad (misión, llamada) puede traer `chat_id`
    // residual de un futuro cruce con el frente 5 ("chat principal"); no
    // debe intentar abrir una conversación fuera de la pestaña del chat.
    let destino = NotificationDestino.parse(userInfo: ["route": "activity", "chat_id": "abc-123"])
    #expect(destino.route == .activity)
    #expect(destino.conversationId == nil)
}

@Test func notificationDestinoSinChatIdMantieneElComportamientoActual() {
    let destino = NotificationDestino.parse(userInfo: ["route": "assistant"])
    #expect(destino.conversationId == nil)
}

@Test func notificationDestinoDescartaChatIdVacioOEnBlanco() {
    #expect(NotificationDestino.parse(userInfo: ["route": "assistant", "chat_id": ""]).conversationId == nil)
    #expect(NotificationDestino.parse(userInfo: ["route": "assistant", "chat_id": "   "]).conversationId == nil)
}
