import SwiftUI

struct AguaView: View {
    @Environment(AguaStore.self) private var agua

    var body: some View {
        ScrollView {
            VStack(spacing: 10) {
                Text(String(format: "%.2f L", agua.litros))
                    .font(.system(.title, design: .rounded).weight(.bold))
                Text("de \(agua.metaMl / 1000) L")
                    .font(.caption)
                    .foregroundStyle(.secondary)

                ProgressView(value: agua.progreso)
                    .tint(agua.completo ? .green : .cyan)

                HStack {
                    Button {
                        agua.registrar(250)
                    } label: {
                        Label("250", systemImage: "drop.fill")
                    }
                    Button {
                        agua.registrar(500)
                    } label: {
                        Label("500", systemImage: "drop.fill")
                    }
                }
                .tint(.cyan)

                Button("100 ml") { agua.registrar(100) }
                    .tint(.cyan)
                    .controlSize(.small)
                Button("Deshacer último") { agua.deshacerUltimo() }
                    .font(.caption2)

                Toggle("Recordarme", isOn: Binding(
                    get: { agua.avisosActivos },
                    set: { agua.cambiarAvisos($0) }
                ))
                .font(.caption)

                Text("Avisos a las 9, 11, 13, 15, 17, 19 y 21.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)

                if agua.completo {
                    Label("Meta del día", systemImage: "checkmark.circle.fill")
                        .font(.caption)
                        .foregroundStyle(.green)
                }
            }
            .padding(.horizontal, 4)
        }
        .navigationTitle("Agua")
    }
}
