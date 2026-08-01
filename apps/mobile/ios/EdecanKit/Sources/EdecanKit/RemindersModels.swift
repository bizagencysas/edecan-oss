import Foundation

// MARK: - `/v1/reminders` (`ARCHITECTURE.md` §10.3/§10.12,
// `apps/api/edecan_api/routers/reminders.py`)

/// Fila pública de `reminders` — sin un `BaseModel` de Pydantic pinned (el
/// router usa `Repo`/`RETURNING *` sobre la tabla completa, ver
/// `apps/api/edecan_api/repo.py::create_reminder`/`list_reminders`); forma
/// verificada campo por campo contra esas dos funciones.
public struct ReminderOut: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let tenantId: String
    public let userId: String
    public let dueAt: Date
    public let rrule: String?
    public let message: String
    public let channel: String
    /// `pending|sent|cancelled` (`ARCHITECTURE.md` §10.3). `String` crudo,
    /// mismo criterio que `MissionOut.status`/`Factura.status`.
    public let status: String
    public let createdAt: Date
    public let updatedAt: Date

    enum CodingKeys: String, CodingKey {
        case id, rrule, message, channel, status
        case tenantId = "tenant_id"
        case userId = "user_id"
        case dueAt = "due_at"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    /// La app móvil trata cualquier status distinto de `"pending"` como
    /// "completado" en la lista (``RecordatoriosView``) — el backend no
    /// tiene un status `"done"` propio (solo `pending|sent|cancelled`,
    /// `ARCHITECTURE.md` §10.3). `APIClient.completeReminder` reutiliza
    /// `"sent"` (el mismo valor que pone `send_reminder_scan` cuando el
    /// recordatorio vence solo) para "lo marqué como hecho a mano" — ver su
    /// docstring. `"cancelled"` (hoy solo lo pone el panel web) también
    /// cuenta como ya resuelto.
    public var completado: Bool { status != "pending" }
}
