import Foundation
import Observation
import EdecanKit

/// Estado de ``AutomatizacionesView``/su detalle (`ARCHITECTURE.md` §11
/// `ROADMAP_V2.md` §7.4/§7.6/§7.10).
@MainActor
@Observable
final class AutomatizacionesViewModel {
    private(set) var automatizaciones: [AutomationOut] = []
    private(set) var cargando = false
    var errorLista: String?

    private(set) var runs: [AutomationRunOut] = []
    private(set) var cargandoRuns = false
    var errorRuns: String?

    private(set) var creando = false
    var errorCreacion: String?

    /// `id` de la automatización cuyo toggle está en vuelo — deshabilita
    /// SOLO esa fila mientras se confirma (o revierte) con el servidor.
    private(set) var idEnToggle: String?

    var sinDatosTodavia: Bool { automatizaciones.isEmpty && cargando }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorLista = "No hay sesión activa."
            return
        }
        cargando = automatizaciones.isEmpty
        errorLista = nil
        defer { cargando = false }
        do {
            automatizaciones = try await client.listAutomations()
        } catch {
            errorLista = error.localizedDescription
        }
    }

    /// Optimista: refleja el nuevo `enabled` en la lista YA MISMO (mejor
    /// respuesta percibida al tocar el `Toggle`), y si el servidor lo
    /// rechaza (p. ej. `403` por el tope de automatizaciones activas del
    /// plan, `automations.py::_check_limit`) revierte la fila a su valor
    /// anterior y muestra el error.
    func alternar(id: String, enabled: Bool, client: APIClient?) async {
        guard let client, idEnToggle == nil else { return }
        guard let indice = automatizaciones.firstIndex(where: { $0.id == id }) else { return }
        let valorAnterior = automatizaciones[indice].enabled
        automatizaciones[indice].enabled = enabled
        idEnToggle = id
        errorLista = nil
        defer { idEnToggle = nil }
        do {
            let actualizada = try await client.toggleAutomation(id: id, enabled: enabled)
            if let i = automatizaciones.firstIndex(where: { $0.id == id }) {
                automatizaciones[i] = actualizada
            }
        } catch {
            if let i = automatizaciones.firstIndex(where: { $0.id == id }) {
                automatizaciones[i].enabled = valorAnterior
            }
            errorLista = error.localizedDescription
        }
    }

    /// "Alta simple": siempre un disparador de agenda (`rrule`) — ver
    /// `APIClient.createAutomation`.
    @discardableResult
    func crear(
        nombre: String, descripcion: String, rrule: String, instruccion: String, client: APIClient?
    ) async -> AutomationOut? {
        guard let client else {
            errorCreacion = "No hay sesión activa."
            return nil
        }
        let nombreLimpio = nombre.trimmingCharacters(in: .whitespacesAndNewlines)
        let instruccionLimpia = instruccion.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !nombreLimpio.isEmpty, !instruccionLimpia.isEmpty, !rrule.trimmingCharacters(in: .whitespaces).isEmpty,
              !creando
        else { return nil }
        creando = true
        errorCreacion = nil
        defer { creando = false }
        do {
            let creada = try await client.createAutomation(
                nombre: nombreLimpio, descripcion: descripcion, rrule: rrule, instruccion: instruccionLimpia
            )
            automatizaciones.insert(creada, at: 0)
            return creada
        } catch {
            errorCreacion = error.localizedDescription
            return nil
        }
    }

    func cargarRuns(id: String, client: APIClient?) async {
        guard let client else {
            errorRuns = "No hay sesión activa."
            return
        }
        cargandoRuns = true
        errorRuns = nil
        defer { cargandoRuns = false }
        do {
            runs = try await client.listAutomationRuns(id: id)
        } catch {
            errorRuns = error.localizedDescription
        }
    }
}
