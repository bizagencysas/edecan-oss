import WidgetKit
import SwiftUI

/// Extensión de widgets de Edecán — target mínimo placeholder para este WP
/// (esqueleto compilable). El widget real (próxima conversación,
/// recordatorio más cercano, atajo directo al chat) es trabajo futuro: hoy
/// no lee `EdecanKit.Keychain` porque este target no comparte App Group con
/// `EdecanApp` todavía (ver el docstring de `Keychain` en EdecanKit y "Qué
/// queda pendiente" en `docs/movil-ios.md`).
@main
struct EdecanWidgetsBundle: WidgetBundle {
    var body: some Widget {
        EdecanEstadoWidget()
    }
}

struct EdecanEstadoEntry: TimelineEntry {
    let date: Date
}

struct EdecanEstadoProvider: TimelineProvider {
    func placeholder(in context: Context) -> EdecanEstadoEntry {
        EdecanEstadoEntry(date: .now)
    }

    func getSnapshot(in context: Context, completion: @escaping (EdecanEstadoEntry) -> Void) {
        completion(EdecanEstadoEntry(date: .now))
    }

    /// `.never`: sin App Group compartido, este widget no tiene ningún dato
    /// del tenant que refrescar en segundo plano todavía — una sola
    /// entrada estática. Se reemplaza por una política real
    /// (`.after(_:)`) cuando el widget lea datos reales.
    func getTimeline(in context: Context, completion: @escaping (Timeline<EdecanEstadoEntry>) -> Void) {
        completion(Timeline(entries: [EdecanEstadoEntry(date: .now)], policy: .never))
    }
}

struct EdecanEstadoWidget: Widget {
    let kind = "EdecanEstadoWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: EdecanEstadoProvider()) { entry in
            EdecanEstadoWidgetView(entry: entry)
        }
        .configurationDisplayName("Edecán")
        .description("Placeholder — el widget real (próxima conversación, recordatorio más cercano) llega en una siguiente iteración. Ver docs/movil-ios.md.")
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
            Text("Próximamente")
                .font(.caption)
                .foregroundStyle(.white.opacity(0.8))
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
    }
}
