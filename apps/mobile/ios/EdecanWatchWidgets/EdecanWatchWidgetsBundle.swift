import WidgetKit
import SwiftUI

@main
struct EdecanWatchWidgetsBundle: WidgetBundle {
    var body: some Widget {
        EdecanWatchComplication()
    }
}

struct EdecanComplicationEntry: TimelineEntry {
    let date: Date
    let pasos: Int
    let metaPasos: Int
    let ml: Int
    let aviso: String?
}

struct EdecanComplicationProvider: TimelineProvider {
    func placeholder(in context: Context) -> EdecanComplicationEntry {
        EdecanComplicationEntry(date: .now, pasos: 0, metaPasos: 8000, ml: 0, aviso: nil)
    }

    func getSnapshot(in context: Context, completion: @escaping (EdecanComplicationEntry) -> Void) {
        completion(leer())
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<EdecanComplicationEntry>) -> Void) {
        completion(Timeline(entries: [leer()], policy: .after(.now.addingTimeInterval(180))))
    }

    private func leer() -> EdecanComplicationEntry {
        let d = UserDefaults(suiteName: "group.cc.edecan.app") ?? .standard
        let meta = d.integer(forKey: "cc.edecan.watch.snap.pasosMeta")
        return EdecanComplicationEntry(
            date: .now,
            pasos: d.integer(forKey: "cc.edecan.watch.snap.pasos"),
            metaPasos: meta == 0 ? 8000 : meta,
            ml: d.integer(forKey: "cc.edecan.watch.snap.ml"),
            aviso: d.string(forKey: "cc.edecan.watch.snap.aviso")
        )
    }
}

struct EdecanWatchComplication: Widget {
    let kind = "EdecanWatchComplication"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: kind, provider: EdecanComplicationProvider()) { entry in
            EdecanComplicationView(entry: entry)
        }
        .configurationDisplayName("Edecán")
        .description("Pasos de hoy, agua y el próximo aviso.")
        .supportedFamilies(EdecanWatchComplication.familiasSoportadas())
    }

    private static func familiasSoportadas() -> [WidgetFamily] {
        #if os(watchOS)
        return [.accessoryCorner, .accessoryCircular, .accessoryRectangular]
        #else
        return [.accessoryCircular, .accessoryRectangular]
        #endif
    }
}

struct EdecanComplicationView: View {
    @Environment(\.widgetFamily) private var family
    var entry: EdecanComplicationProvider.Entry

    var body: some View {
        switch family {
        #if os(watchOS)
        case .accessoryCorner:
            Text(entry.pasos.formatted())
                .widgetLabel { Text(String(format: "%.1f L", Double(entry.ml) / 1000)) }
        #endif
        case .accessoryCircular:
            Gauge(value: min(1, Double(entry.pasos) / Double(max(entry.metaPasos, 1)))) {
                Image(systemName: "figure.walk")
            } currentValueLabel: {
                Text(String(format: "%.1f", Double(entry.ml) / 1000))
            }
            #if os(watchOS)
            .gaugeStyle(.accessoryCircularCapacity)
            #else
            .gaugeStyle(.accessoryCircular)
            #endif
        default:
            VStack(alignment: .leading, spacing: 2) {
                Text("\(entry.pasos.formatted()) pasos")
                    .font(.headline)
                if let aviso = entry.aviso {
                    Text(aviso).font(.caption2).lineLimit(2)
                } else {
                    Text(String(format: "%.1f L", Double(entry.ml) / 1000))
                        .font(.caption2)
                }
            }
        }
    }
}
