import Foundation

/// Identificadores APNs compartidos entre iPhone y Apple Watch para la
/// pregunta «¿Vas a entrenar hoy?». La lógica pura vive aquí para poder
/// probarla en Linux con `swift test` sin simulador.
public enum GymCheckinNotificationSupport {
    public static let categoriaIdentifier = "GYM_CHECKIN"
    public static let accionSi = "GYM_YES"
    public static let accionNo = "GYM_NO"

    /// Devuelve `"si"` / `"no"` para una acción de categoría gym, o `nil`.
    public static func respuestaCheckin(actionIdentifier: String) -> String? {
        switch actionIdentifier {
        case accionSi: "si"
        case accionNo: "no"
        default: nil
        }
    }

    /// Solo «Sí» debe arrancar entrenamiento activo y `HKWorkoutSession`.
    public static func debeIniciarEntrenamiento(actionIdentifier: String) -> Bool {
        actionIdentifier == accionSi
    }

    /// Cuándo mostrar la pantalla viva de entrenamiento (métricas HK + fuerza),
    /// aunque el plan del iPhone todavía no haya llegado por WatchConnectivity.
    public static func mostrarLiveWorkoutUI(
        sesionActiva: Bool,
        pausada: Bool,
        entrenoActivo: Bool,
        descansoRestante: Int? = nil
    ) -> Bool {
        if let restante = descansoRestante, restante > 0 { return true }
        return sesionActiva || pausada || entrenoActivo
    }
}
