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

@Test func notificationDestinoAbreElChatAunqueLaRutaSeaActividad() {
    // `work_failed` de LinkedIn/Acme viaja con `route: "activity"` y
    // `chat_id` a la conversación donde quedó la explicación. El tap tiene
    // que abrir ESE chat, no la grilla vacía de Actividad.
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "work_failed",
        "chat_id": "abc-123",
        "deeplink": "edecan://chat/abc-123",
    ])
    #expect(destino.route == .assistant)
    #expect(destino.conversationId == "abc-123")
    #expect(destino.missionId == nil)
}

@Test func notificationDestinoSinChatIdMantieneElComportamientoActual() {
    let destino = NotificationDestino.parse(userInfo: ["route": "assistant"])
    #expect(destino.conversationId == nil)
}

@Test func notificationDestinoDescartaChatIdVacioOEnBlanco() {
    #expect(NotificationDestino.parse(userInfo: ["route": "assistant", "chat_id": ""]).conversationId == nil)
    #expect(NotificationDestino.parse(userInfo: ["route": "assistant", "chat_id": "   "]).conversationId == nil)
}

// Frente 6 (deeplink de llamada): tocar el push de una llamada ENTRANTE
// debe traer consigo el id de esa llamada para abrir la vista en vivo.
@Test func notificationDestinoLeeCallIdEnLlamadaEntrante() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "phone_call_incoming",
        "resource_id": "call-123",
        "deeplink": "edecan://activity/call-123",
    ])
    #expect(destino.route == .activity)
    #expect(destino.callId == "call-123")
}

@Test func notificationDestinoLaLlamadaGanaSobreUnChatIdResidual() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "phone_call_incoming",
        "chat_id": "abc-123",
        "resource_id": "call-123",
    ])
    #expect(destino.callId == "call-123")
    #expect(destino.conversationId == nil)
}

@Test func notificationDestinoUsaElDeeplinkSiFaltaResourceId() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "phone_call_incoming",
        "deeplink": "edecan://activity/call-456",
    ])
    #expect(destino.callId == "call-456")
}

// El resumen post-llamada comparte la misma forma (`route: "activity"` +
// `resource_id`) pero sin `event`; no debe interpretarse como una llamada
// en vivo -- la llamada ya terminó.
@Test func notificationDestinoIgnoraResourceIdSinEventoDeLlamada() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "kind": "call",
        "resource_id": "call-789",
    ])
    #expect(destino.callId == nil)
}

// Automatizaciones y recordatorios también viven en `route: "activity"` +
// `resource_id` con su propio `event`; ese `resource_id` no es una llamada.
@Test func notificationDestinoIgnoraResourceIdDeOtrosEventosDeActividad() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "automation_completed",
        "resource_id": "automation-1",
        "deeplink": "edecan://activity/automation-1",
    ])
    #expect(destino.callId == nil)
    #expect(destino.missionId == nil)
}

@Test func notificationDestinoLeeMisionEnWorkFailed() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "work_failed",
        "resource_id": "mission-123",
        "deeplink": "edecan://activity/mission-123",
    ])
    #expect(destino.route == .activity)
    #expect(destino.missionId == "mission-123")
    #expect(destino.callId == nil)
    #expect(destino.conversationId == nil)
}

@Test func notificationDestinoLeeChatIdDelDeeplinkSiFaltaElCampo() {
    let destino = NotificationDestino.parse(userInfo: [
        "route": "activity",
        "event": "work_failed",
        "deeplink": "edecan://chat/conv-789",
    ])
    #expect(destino.route == .assistant)
    #expect(destino.conversationId == "conv-789")
}
