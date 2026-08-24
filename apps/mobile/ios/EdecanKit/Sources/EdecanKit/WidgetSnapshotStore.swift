import Foundation

/// Resumen no sensible que la app principal deja en el App Group para el
/// widget. El widget nunca recibe JWT, URLs de API ni contenido de mensajes.
public struct EdecanWidgetSnapshot: Codable, Sendable, Equatable {
    public let activeMissionCount: Int
    public let pendingReminderCount: Int
    public let nextReminderAt: Date?
    public let updatedAt: Date

    public init(
        activeMissionCount: Int = 0,
        pendingReminderCount: Int = 0,
        nextReminderAt: Date? = nil,
        updatedAt: Date = .now
    ) {
        self.activeMissionCount = max(0, activeMissionCount)
        self.pendingReminderCount = max(0, pendingReminderCount)
        self.nextReminderAt = nextReminderAt
        self.updatedAt = updatedAt
    }
}

public enum WidgetSnapshotStore {
    private static let key = "cc.edecan.widget.snapshot.v1"

    public static func read() -> EdecanWidgetSnapshot {
        guard let defaults = UserDefaults(suiteName: SharePayloadStore.appGroup),
              let data = defaults.data(forKey: key),
              let snapshot = try? JSONDecoder().decode(EdecanWidgetSnapshot.self, from: data)
        else { return EdecanWidgetSnapshot() }
        return snapshot
    }

    public static func save(_ snapshot: EdecanWidgetSnapshot) {
        guard let defaults = UserDefaults(suiteName: SharePayloadStore.appGroup),
              let data = try? JSONEncoder().encode(snapshot)
        else { return }
        defaults.set(data, forKey: key)
    }

    /// La app escribe solo conteos y el próximo vencimiento; las extensiones
    /// no realizan requests autenticados ni necesitan conocer el servidor.
    public static func refresh(client: APIClient) async {
        do {
            async let missions = client.listMissions()
            async let reminders = client.listReminders()
            let (missionRows, reminderRows) = try await (missions, reminders)
            let activeMissions = missionRows.filter {
                ["planning", "running", "waiting_confirmation", "paused"].contains($0.status)
            }.count
            let pending = reminderRows.filter { $0.status == "pending" }
            save(
                EdecanWidgetSnapshot(
                    activeMissionCount: activeMissions,
                    pendingReminderCount: pending.count,
                    nextReminderAt: pending.map(\.dueAt).min()
                )
            )
        } catch {
            // El último snapshot permanece visible si el backend está offline.
        }
    }
}

