#if canImport(UIKit)
import Testing
import UIKit
@testable import EdecanKit

@Test func senderPNGConCaraNoEsSoloLogoApp() {
    let cara = BotPushAvatarRenderer.senderPNGData(
        avatarFromPayload: true,
        shape: "hexagon",
        fillHex: "#6366f1",
        accentHex: "#22c55e",
        displayName: "Astra",
        size: 64
    )
    let logoSolo = BotPushAvatarRenderer.appLogoPNGData(size: 64)
    #expect(cara != nil)
    if let cara, let logoSolo {
        #expect(cara != logoSolo)
    }
}

@Test func senderPNGConPayloadRenderizaCaraAunqueAvatarFromPayloadFalse() {
    let compuesto = BotPushAvatarRenderer.senderPNGData(
        avatarFromPayload: false,
        shape: "circle",
        fillHex: "#6366f1",
        accentHex: nil,
        displayName: "Bot",
        size: 64
    )
    let logoSolo = BotPushAvatarRenderer.appLogoPNGData(size: 64)
    #expect(compuesto != nil)
    if let compuesto, let logoSolo {
        #expect(compuesto != logoSolo)
    }
}

@Test func compositePNGAgregaBadgeDistintoDeCaraSola() {
    guard let face = BotPushAvatarRenderer.pngData(
        shape: "circle",
        fillHex: "#ff0000",
        accentHex: nil,
        displayName: "Test",
        size: 64
    ).flatMap({ UIImage(data: $0) }) else {
        Issue.record("No se pudo renderizar cara de prueba")
        return
    }
    let compuesto = BotPushAvatarRenderer.compositePNG(face: face, size: 64)
    let caraSola = face.pngData()
    #expect(compuesto != nil)
    if let compuesto, let caraSola {
        #expect(compuesto != caraSola)
        #expect(compuesto.count > caraSola.count)
    }
}

@Test func badgeScaleDentroDeRangoGrok() {
    #expect(BotPushAvatarRenderer.badgeScale >= 0.22)
    #expect(BotPushAvatarRenderer.badgeScale <= 0.28)
}

@Test func ojosDesdePayloadParseaDict() {
    let userInfo: [AnyHashable: Any] = [
        "avatar_eyes": [
            "left": ["x": 0.34, "y": 0.40, "rx": 0.058, "ry": 0.078, "rotation": -12],
            "right": ["x": 0.66, "y": 0.40, "rx": 0.058, "ry": 0.078, "rotation": 12],
        ] as [String: Any],
    ]
    let ojos = BotCommunicationNotificationSupport.ojosDesdePayload(userInfo: userInfo)
    #expect(ojos.left?.x == 0.34)
    #expect(ojos.right?.x == 0.66)
    #expect(ojos.left?.rotation == -12)
}
#endif
