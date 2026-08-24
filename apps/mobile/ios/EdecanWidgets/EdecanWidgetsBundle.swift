import WidgetKit
import SwiftUI
import EdecanKit

/// Widget de estado: muestra payloads compartidos pendientes y abre Edecán.
@main
struct EdecanWidgetsBundle: WidgetBundle {
    var body: some Widget {
        EdecanEstadoWidget()
        GymLiveActivityWidget()
    }
}

struct EdecanEstadoEntry: TimelineEntry {
    let date: Date
    let pendingShareCount: Int
    let activeMissionCount: Int
    let pendingReminderCount: Int
    let nextReminderAt: Date?
}

struct EdecanEstadoProvider: TimelineProvider {
    func placeholder(in context: Context) -> EdecanEstadoEntry {
        EdecanEstadoEntry(date: .now, pendingShareCount: 0, activeMissionCount: 0, pendingReminderCount: 0, nextReminderAt: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (EdecanEstadoEntry) -> Void) {
        let snapshot = WidgetSnapshotStore.read()
        completion(EdecanEstadoEntry(
            date: .now,
            pendingShareCount: SharePayloadStore.pendingCount(),
            activeMissionCount: snapshot.activeMissionCount,
            pendingReminderCount: snapshot.pendingReminderCount,
            nextReminderAt: snapshot.nextReminderAt
        ))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<EdecanEstadoEntry>) -> Void) {
        let now = Date()
        let snapshot = WidgetSnapshotStore.read()
        let entry = EdecanEstadoEntry(
            date: now,
            pendingShareCount: SharePayloadStore.pendingCount(),
            activeMissionCount: snapshot.activeMissionCount,
            pendingReminderCount: snapshot.pendingReminderCount,
            nextReminderAt: snapshot.nextReminderAt
        )
        completion(Timeline(entries: [entry], policy: .after(now.addingTimeInterval(900))))
    }
}

struct EdecanEstadoWidget: Widget {
    let kind = "EdecanEstadoWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: EdecanEstadoProvider()) { entry in
            EdecanEstadoWidgetView(entry: entry)
        }
        .configurationDisplayName("Edecán")
        .description("Muestra contenido compartido pendiente y abre Edecán para revisarlo.")
        .supportedFamilies([.systemSmall])
    }
}

struct EdecanEstadoWidgetView: View {
    var entry: EdecanEstadoProvider.Entry

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Image(systemName: "sparkles")
                .foregroundStyle(.white)
            Text("Edecán")
                .font(.headline)
                .foregroundStyle(.white)
            if entry.pendingShareCount > 0 {
                Text("\(entry.pendingShareCount) compartido(s) pendiente(s)")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.8))
            }
            if entry.activeMissionCount > 0 {
                Text("\(entry.activeMissionCount) misión(es) activa(s)")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.8))
            }
            if entry.pendingReminderCount > 0 {
                Text("\(entry.pendingReminderCount) recordatorio(s)")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.8))
            }
            if let nextReminderAt = entry.nextReminderAt {
                Text("Próximo: \(nextReminderAt.formatted(date: .omitted, time: .shortened))")
                    .font(.caption2)
                    .foregroundStyle(.white.opacity(0.75))
            }
            if entry.pendingShareCount == 0 && entry.activeMissionCount == 0 && entry.pendingReminderCount == 0 {
                Text("Listo para ayudarte")
                    .font(.caption)
                    .foregroundStyle(.white.opacity(0.8))
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
        .padding()
        .containerBackground(for: .widget) {
            LinearGradient(
                colors: [Color(red: 0.51, green: 0.36, blue: 0.96), Color(red: 0.29, green: 0.49, blue: 0.98)],
                startPoint: .topLeading,
                endPoint: .bottomTrailing
            )
        }
        .widgetURL(URL(string: "edecan://share"))
    }
}
