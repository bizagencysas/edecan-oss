import ActivityKit
import EdecanKit
import Foundation

/// `ActivityKit.Activity` es una clase NO marcada `Sendable` en el SDK, pero
/// todos sus métodos (`update`, `end`) son `nonisolated` y están documentados
/// para llamarse desde cualquier ejecutor: la concurrencia interna la maneja
/// el propio framework. Este box `@unchecked Sendable` declara ese hecho sin
/// recurrir a una conformancia retroactiva sobre un tipo importado (que el
/// compilador advertiría).
private struct ActividadLive: @unchecked Sendable {
    let raw: Activity<GymActivityAttributes>
}

/// Arranca/actualiza/termina el Live Activity del gimnasio. El tipo
/// ``GymActivityAttributes`` vive en EdecanKit (compartido con el widget), así
/// que este controlador no define ningún formato: solo pasa el estado de la
/// sesión. Todo es best-effort — si el usuario desactivó las Live Activities
/// o el sistema las rechaza, la app sigue funcionando sin ellas.
@MainActor
final class GymLiveActivityController {
    private var actividad: ActividadLive?

    var activa: Bool { actividad != nil }

    /// Crea la actividad. Síncrono (`Activity.request` lanza, no espera).
    func iniciar(ejercicio: String, seriesHechas: Int, seriesTotales: Int, startedAt: Date) {
        guard actividad == nil, ActivityAuthorizationInfo().areActivitiesEnabled else { return }
        let estado = GymActivityAttributes.ContentState(
            ejercicio: ejercicio,
            seriesHechas: seriesHechas,
            seriesTotales: seriesTotales,
            startedAt: startedAt
        )
        do {
            let nueva = try Activity.request(
                attributes: GymActivityAttributes(),
                content: ActivityContent(state: estado, staleDate: nil),
                pushType: nil
            )
            actividad = ActividadLive(raw: nueva)
        } catch {
            // Live Activity no disponible (permisos/estado): sin actividad.
        }
    }

    /// Actualiza el estado en pantalla (ejercicio y series). No toca el
    /// `startedAt`: el cronómetro sigue desde el arranque original.
    func actualizar(ejercicio: String, seriesHechas: Int, seriesTotales: Int) async {
        guard let actividad else { return }
        let estado = GymActivityAttributes.ContentState(
            ejercicio: ejercicio,
            seriesHechas: seriesHechas,
            seriesTotales: seriesTotales,
            startedAt: actividad.raw.content.state.startedAt
        )
        await actividad.raw.update(ActivityContent(state: estado, staleDate: nil))
    }

    /// Termina y despide la actividad (`.immediate` para que desaparezca de la
    /// pantalla de bloqueo en cuanto la sesión se cierra).
    func terminar() async {
        guard let actividad else { return }
        let previo = actividad.raw.content.state
        let estado = GymActivityAttributes.ContentState(
            ejercicio: previo.ejercicio,
            seriesHechas: previo.seriesHechas,
            seriesTotales: previo.seriesTotales,
            startedAt: previo.startedAt
        )
        await actividad.raw.end(ActivityContent(state: estado, staleDate: nil), dismissalPolicy: .immediate)
        self.actividad = nil
    }
}