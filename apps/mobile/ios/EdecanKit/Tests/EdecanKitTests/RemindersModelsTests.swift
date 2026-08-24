import Testing
import Foundation
@testable import EdecanKit

/// Decodifica el mismo shape que devuelven `Repo.create_reminder`/
/// `list_reminders` de verdad (`RETURNING *`/`SELECT *` sobre la tabla
/// `reminders` completa, `apps/api/edecan_api/repo.py`, verificado contra el
/// código fuente) — mismo criterio que `ModelsTests`.
struct RemindersModelsTests {
    @Test func decodificaRecordatorioPendiente() throws {
        let json = """
        {
          "id": "r1", "tenant_id": "t1", "user_id": "u1", "due_at": "2026-07-09T15:00:00Z",
          "rrule": null, "message": "Llamar al contador", "channel": "web", "status": "pending",
          "created_at": "2026-07-08T10:00:00Z", "updated_at": "2026-07-08T10:00:00Z"
        }
        """
        let recordatorio = try APIClient.crearDecoder().decode(ReminderOut.self, from: Data(json.utf8))
        #expect(recordatorio.message == "Llamar al contador")
        #expect(recordatorio.status == "pending")
        #expect(recordatorio.completado == false)
        #expect(recordatorio.rrule == nil)
    }

    @Test func recordatorioEnviadoCuentaComoCompletado() throws {
        let json = """
        {
          "id": "r2", "tenant_id": "t1", "user_id": "u1", "due_at": "2026-07-08T09:00:00Z",
          "rrule": null, "message": "Reunión de equipo", "channel": "web", "status": "sent",
          "created_at": "2026-07-07T10:00:00Z", "updated_at": "2026-07-08T09:00:01Z"
        }
        """
        let recordatorio = try APIClient.crearDecoder().decode(ReminderOut.self, from: Data(json.utf8))
        #expect(recordatorio.completado == true)
    }

    @Test func recordatorioCanceladoCuentaComoCompletado() throws {
        let json = """
        {
          "id": "r3", "tenant_id": "t1", "user_id": "u1", "due_at": "2026-07-08T09:00:00Z",
          "rrule": "FREQ=DAILY", "message": "m", "channel": "web", "status": "cancelled",
          "created_at": "2026-07-07T10:00:00Z", "updated_at": "2026-07-08T09:00:01Z"
        }
        """
        let recordatorio = try APIClient.crearDecoder().decode(ReminderOut.self, from: Data(json.utf8))
        #expect(recordatorio.completado == true)
        #expect(recordatorio.rrule == "FREQ=DAILY")
    }

    @Test func decodificaListaOrdenadaPorDueAt() throws {
        let json = """
        [
          {"id": "r1", "tenant_id": "t1", "user_id": "u1", "due_at": "2026-07-08T09:00:00Z",
           "rrule": null, "message": "primero", "channel": "web", "status": "pending",
           "created_at": "2026-07-07T10:00:00Z", "updated_at": "2026-07-07T10:00:00Z"},
          {"id": "r2", "tenant_id": "t1", "user_id": "u1", "due_at": "2026-07-09T09:00:00Z",
           "rrule": null, "message": "segundo", "channel": "web", "status": "pending",
           "created_at": "2026-07-07T10:00:00Z", "updated_at": "2026-07-07T10:00:00Z"}
        ]
        """
        let recordatorios = try APIClient.crearDecoder().decode([ReminderOut].self, from: Data(json.utf8))
        #expect(recordatorios.count == 2)
        #expect(recordatorios[0].message == "primero")
    }
}
