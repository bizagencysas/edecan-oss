import AppIntents
import Foundation

/// Acciones que Siri ejecuta de verdad (API / puente Watch), sin abrir el composer.
struct RegistrarAguaEdecanIntent: AppIntent {
    static let title: LocalizedStringResource = "Registrar agua en Edecán"
    static let description = IntentDescription("Suma un trago de agua en Salud y en el reloj.")
    static let openAppWhenRun = false

    @Parameter(title: "Mililitros", default: 250)
    var mililitros: Int

    static var parameterSummary: some ParameterSummary {
        Summary("Registrar \(\.$mililitros) ml de agua")
    }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let ml = min(max(mililitros, 50), 1000)
        let ok = await WatchCompanion.compartido.accionAgua(ml)
        return .result(dialog: ok
            ? "Listo, \(ml) mililitros."
            : "No pude guardar el agua en Salud. Revisa el permiso.")
    }
}

struct RegistrarSerieEdecanIntent: AppIntent {
    static let title: LocalizedStringResource = "Registrar serie de gym"
    static let description = IntentDescription("Marca la serie en curso del entrenamiento.")
    static let openAppWhenRun = false

    func perform() async throws -> some IntentResult & ProvidesDialog {
        let ok = await WatchCompanion.compartido.accionSerie()
        return .result(dialog: ok ? "Serie registrada." : "No hay entrenamiento activo.")
    }
}

/// Pide Face ID / código y confirma el nombre de la tool antes de aprobar.
/// No es un «sí» genérico: el invariante de Edecán es ver qué se ejecuta.
struct AprobarPendienteEdecanIntent: AppIntent {
    static let title: LocalizedStringResource = "Aprobar lo pendiente en Edecán"
    static let description = IntentDescription("Muestra la herramienta que espera tu sí y, si confirmas, la aprueba.")
    static let openAppWhenRun = false
    static var authenticationPolicy: IntentAuthenticationPolicy { .requiresAuthentication }

    func perform() async throws -> some IntentResult & ProvidesDialog {
        guard let preview = await WatchCompanion.compartido.pendienteParaAprobar() else {
            return .result(dialog: "No hay nada pendiente.")
        }
        let detalle = preview.detalle.isEmpty ? preview.nombre : "\(preview.nombre). \(preview.detalle)"
        try await requestConfirmation(
            actionName: .`continue`,
            dialog: IntentDialog(stringLiteral: "¿Apruebo \(detalle)?")
        )
        let ok = await WatchCompanion.compartido.accionAprobarPendiente(id: preview.id)
        return .result(dialog: ok ? "Aprobado: \(preview.nombre)." : "No pude aprobarlo.")
    }
}
