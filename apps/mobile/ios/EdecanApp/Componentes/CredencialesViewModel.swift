import Foundation
import Observation
import EdecanKit

/// Estado de capacidades conectadas. La inferencia de LLM es administrada
/// por Edecán: no hay proveedor, API key ni modelo seleccionables por persona.
@MainActor
@Observable
final class CredencialesViewModel {
    private(set) var credenciales: CredentialsOut?
    private(set) var setup: SetupStatus?
    private(set) var cargando = false
    var errorMensaje: String?

    var inteligenciaDisponible: Bool {
        credenciales?.llm != nil || setup?.llmConfigured == true
    }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorMensaje = "No hay sesión activa."
            return
        }
        cargando = true
        errorMensaje = nil
        defer { cargando = false }

        do {
            async let credencialesTarea = client.credenciales()
            async let setupTarea = client.setupStatus()
            let (credencialesResultado, setupResultado) =
                try await (credencialesTarea, setupTarea)
            credenciales = credencialesResultado
            setup = setupResultado
        } catch is CancellationError {
            return
        } catch {
            errorMensaje = error.localizedDescription
        }
    }
}
