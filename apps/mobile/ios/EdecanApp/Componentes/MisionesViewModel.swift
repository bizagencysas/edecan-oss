import Foundation
import Observation
import EdecanKit

/// Estado y *polling* de ``MisionesView``/su detalle — el Orchestrator
/// multi-agente corre asíncrono en el worker (`ARCHITECTURE.md` §11
/// `ROADMAP_V2.md` §7.9), así que no hay nada que esperar en la respuesta
/// HTTP: esta pantalla vuelve a pedir la lista/el detalle cada
/// ``intervaloPolling`` mientras algo siga activo — mismo intervalo (2s) que
/// `apps/web/src/app/(app)/app/misiones/page.tsx`.
@MainActor
@Observable
final class MisionesViewModel {
    private(set) var misiones: [MissionOut] = []
    private(set) var cargandoLista = false
    var errorLista: String?

    private(set) var detalle: MissionDetailOut?
    private(set) var cargandoDetalle = false
    var errorDetalle: String?

    private(set) var creando = false
    var errorCreacion: String?

    /// Aprobar/rechazar/cancelar en curso — deshabilita los botones de la
    /// tarjeta de confirmación y de "Cancelar misión" mientras dura.
    private(set) var accionEnCurso = false

    private let intervaloPolling: Duration = .seconds(2)
    private var tareaPollingLista: Task<Void, Never>?
    private var tareaPollingDetalle: Task<Void, Never>?

    // MARK: - Lista

    func cargarMisiones(client: APIClient?) async {
        guard let client else {
            errorLista = "No hay sesión activa."
            return
        }
        cargandoLista = misiones.isEmpty
        errorLista = nil
        defer { cargandoLista = false }
        do {
            misiones = try await client.listMissions()
        } catch {
            errorLista = error.localizedDescription
        }
    }

    /// Crea la misión y la antepone a ``misiones`` de una (sin esperar al
    /// próximo *poll*) para que aparezca al instante en la lista.
    @discardableResult
    func crear(objetivo: String, client: APIClient?) async -> MissionOut? {
        guard let client else {
            errorCreacion = "No hay sesión activa."
            return nil
        }
        let limpio = objetivo.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty, !creando else { return nil }
        creando = true
        errorCreacion = nil
        defer { creando = false }
        do {
            let mission = try await client.createMission(objetivo: limpio)
            misiones.insert(mission, at: 0)
            return mission
        } catch {
            errorCreacion = error.localizedDescription
            return nil
        }
    }

    /// Repite ``cargarMisiones(client:)`` cada ``intervaloPolling`` mientras
    /// alguna misión de la lista siga activa (`MissionOut.estaActiva`) — deja
    /// de pedir sola (sin cancelarse) en cuanto ninguna lo está, así que
    /// basta con volver a llamar esto tras crear una misión nueva.
    func iniciarPollingLista(client: APIClient?) {
        detenerPollingLista()
        guard let client else { return }
        tareaPollingLista = Task { [intervaloPolling] in
            while !Task.isCancelled {
                try? await Task.sleep(for: intervaloPolling)
                guard !Task.isCancelled else { return }
                guard self.misiones.contains(where: { $0.estaActiva }) else { continue }
                await self.cargarMisiones(client: client)
            }
        }
    }

    func detenerPollingLista() {
        tareaPollingLista?.cancel()
        tareaPollingLista = nil
    }

    // MARK: - Detalle

    func cargarDetalle(id: String, client: APIClient?) async {
        guard let client else {
            errorDetalle = "No hay sesión activa."
            return
        }
        cargandoDetalle = detalle == nil
        errorDetalle = nil
        defer { cargandoDetalle = false }
        do {
            detalle = try await client.getMission(id: id)
        } catch {
            errorDetalle = error.localizedDescription
        }
    }

    /// Repite ``cargarDetalle(id:client:)`` cada ``intervaloPolling``
    /// mientras la misión seleccionada siga activa.
    func iniciarPollingDetalle(id: String, client: APIClient?) {
        detenerPollingDetalle()
        guard let client else { return }
        tareaPollingDetalle = Task { [intervaloPolling] in
            while !Task.isCancelled {
                try? await Task.sleep(for: intervaloPolling)
                guard !Task.isCancelled else { return }
                guard self.detalle?.mission.estaActiva ?? false else { continue }
                await self.cargarDetalle(id: id, client: client)
            }
        }
    }

    func detenerPollingDetalle() {
        tareaPollingDetalle?.cancel()
        tareaPollingDetalle = nil
    }

    /// Limpia el detalle al salir de esa pantalla — así la próxima misión
    /// que se abra no muestra por un instante los datos de la anterior.
    func cerrarDetalle() {
        detenerPollingDetalle()
        detalle = nil
        errorDetalle = nil
    }

    func confirmar(aprobado: Bool, client: APIClient?) async {
        guard let client, let id = detalle?.mission.id, !accionEnCurso else { return }
        accionEnCurso = true
        errorDetalle = nil
        defer { accionEnCurso = false }
        do {
            _ = try await client.confirmMission(id: id, approve: aprobado)
            await cargarDetalle(id: id, client: client)
            await cargarMisiones(client: client)
        } catch {
            errorDetalle = error.localizedDescription
        }
    }

    func cancelar(client: APIClient?) async {
        guard let client, let id = detalle?.mission.id, !accionEnCurso else { return }
        accionEnCurso = true
        errorDetalle = nil
        defer { accionEnCurso = false }
        do {
            _ = try await client.cancelMission(id: id)
            await cargarDetalle(id: id, client: client)
            await cargarMisiones(client: client)
        } catch {
            errorDetalle = error.localizedDescription
        }
    }
}
