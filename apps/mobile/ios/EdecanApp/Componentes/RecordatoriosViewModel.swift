import Foundation
import Observation
import EdecanKit

/// Estado de ``RecordatoriosView`` (`ARCHITECTURE.md` §10.3/§10.12).
@MainActor
@Observable
final class RecordatoriosViewModel {
    private(set) var recordatorios: [ReminderOut] = []
    private(set) var cargando = false
    var errorLista: String?

    private(set) var creando = false
    var errorCreacion: String?

    /// `id` del recordatorio que se está completando ahora mismo (swipe).
    private(set) var idCompletando: String?

    var pendientes: [ReminderOut] { recordatorios.filter { !$0.completado } }
    var completados: [ReminderOut] { recordatorios.filter(\.completado) }
    var sinDatosTodavia: Bool { recordatorios.isEmpty && cargando }

    func cargar(client: APIClient?) async {
        guard let client else {
            errorLista = "No hay sesión activa."
            return
        }
        cargando = recordatorios.isEmpty
        errorLista = nil
        defer { cargando = false }
        do {
            recordatorios = try await client.listReminders()
        } catch {
            errorLista = error.localizedDescription
        }
    }

    @discardableResult
    func crear(texto: String, fecha: Date, client: APIClient?) async -> ReminderOut? {
        guard let client else {
            errorCreacion = "No hay sesión activa."
            return nil
        }
        let limpio = texto.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !limpio.isEmpty, !creando else { return nil }
        creando = true
        errorCreacion = nil
        defer { creando = false }
        do {
            // `canal` default "web" — nunca "mobile" todavía, ver el
            // docstring de `APIClient.createReminder`.
            let creado = try await client.createReminder(texto: limpio, fecha: fecha)
            recordatorios.append(creado)
            recordatorios.sort { $0.dueAt < $1.dueAt }
            return creado
        } catch {
            errorCreacion = error.localizedDescription
            return nil
        }
    }

    func completar(id: String, client: APIClient?) async {
        guard let client, idCompletando == nil else { return }
        idCompletando = id
        errorLista = nil
        defer { idCompletando = nil }
        do {
            let actualizado = try await client.completeReminder(id: id)
            if let indice = recordatorios.firstIndex(where: { $0.id == id }) {
                recordatorios[indice] = actualizado
            }
        } catch {
            errorLista = error.localizedDescription
        }
    }
}
