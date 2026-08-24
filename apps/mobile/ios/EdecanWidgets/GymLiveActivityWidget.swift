import ActivityKit
import EdecanKit
import SwiftUI
import WidgetKit

/// Live Activity del gimnasio. Renderiza el estado de
/// ``GymActivityAttributes`` (en EdecanKit, compartido con la app): un
/// cronómetro `Text(timerInterval:)`, el ejercicio actual y "Serie X/Y".
///
/// Solo pinta el estado que ya le dio la app: este target NO consulta red ni
/// Keychain, así que no necesita App Group (mismo criterio que el placeholder
/// de ``EdecanEstadoWidget``).
struct GymLiveActivityWidget: Widget {
    var body: some WidgetConfiguration {
        ActivityConfiguration(for: GymActivityAttributes.self) { context in
            VistaLiveActivity(context: context)
        } dynamicIsland: { context in
            DynamicIsland {
                DynamicIslandExpandedRegion(.leading) {
                    HStack(spacing: 4) {
                        Image(systemName: "figure.strengthtraining.traditional")
                        Text("Gym")
                    }
                }
                DynamicIslandExpandedRegion(.center) {
                    Text(context.state.ejercicio)
                        .lineLimit(1)
                }
                DynamicIslandExpandedRegion(.trailing) {
                    Text("\(context.state.seriesHechas)/\(context.state.seriesTotales)")
                        .monospacedDigit()
                }
            } compactLeading: {
                Image(systemName: "figure.strengthtraining.traditional")
            } compactTrailing: {
                Text("\(context.state.seriesHechas)/\(context.state.seriesTotales)")
                    .monospacedDigit()
            } minimal: {
                Image(systemName: "figure.strengthtraining.traditional")
            }
            .keylineTint(Color(red: 0.51, green: 0.36, blue: 0.96))
        }
        .configurationDisplayName("Entrenamiento")
        .description("Muestra la sesión de gimnasio en curso.")
    }
}

private struct VistaLiveActivity: View {
    let context: ActivityViewContext<GymActivityAttributes>

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 5) {
                Image(systemName: "figure.strengthtraining.traditional")
                Text("Edecán Gym")
                    .font(.caption.weight(.semibold))
            }
            Text(context.state.ejercicio)
                .font(.headline)
                .lineLimit(1)
            Text(timerInterval: context.state.startedAt...Date(), countsDown: false)
                .font(.title2.monospacedDigit().weight(.semibold))
            Text("Serie \(context.state.seriesHechas)/\(context.state.seriesTotales)")
                .font(.subheadline)
        }
        .padding()
        .activityBackgroundTint(Color.black.opacity(0.4))
        .activitySystemActionForegroundColor(.white)
    }
}