import UIKit

/// Envoltura mínima para la retroalimentación háptica de Edecán. Centraliza
/// los generadores de UIKit para que las llamadas desde vistas y view models
/// sean una sola línea y mantengan `prepare()` antes de `impactOccurred`/
/// `notificationOccurred` (patrón ya usado en `BarraAccionesMensaje`).
///
/// Todas las llamadas tocan UIKit y deben correr en el hilo principal; los
/// puntos de uso actuales (botones SwiftUI, `@MainActor` ChatViewModel) ya lo
/// hacen, así que no se envuelve en `DispatchQueue.main` para no añadir
/// latencia ni reordenar efectos.
enum Haptico {
    static func ligero() {
        let g = UIImpactFeedbackGenerator(style: .light)
        g.prepare()
        g.impactOccurred()
    }

    static func medio() {
        let g = UIImpactFeedbackGenerator(style: .medium)
        g.prepare()
        g.impactOccurred()
    }

    static func exito() {
        let g = UINotificationFeedbackGenerator()
        g.prepare()
        g.notificationOccurred(.success)
    }

    static func advertencia() {
        let g = UINotificationFeedbackGenerator()
        g.prepare()
        g.notificationOccurred(.warning)
    }

    static func error() {
        let g = UINotificationFeedbackGenerator()
        g.prepare()
        g.notificationOccurred(.error)
    }
}
