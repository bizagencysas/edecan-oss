import SwiftUI

struct AvisosView: View {
    @Environment(WatchSessionManager.self) private var manager
    @Environment(AguaStore.self) private var agua
    @State private var texto = ""

    var body: some View {
        List {
            if manager.avisosPendientes.isEmpty {
                Text("Nada pendiente en el iPhone.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(manager.avisosPendientes) { aviso in
                    VStack(alignment: .leading, spacing: 4) {
                        Text(aviso.mensaje)
                            .font(.caption)
                        Text(aviso.vence, style: .timer)
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                        Button("Hecho") { manager.completarAviso(aviso.id) }
                            .tint(.green)
                    }
                }
            }

            Section("Avísame aquí") {
                Button("15 minutos") { ping(15, "Pausa de 15 minutos") }
                Button("30 minutos") { ping(30, "Ya pasaron 30 minutos") }
                Button("1 hora") { ping(60, "Pasó una hora") }
                Button("Estirar") { ping(45, "Hora de estirar un poco") }
                Button("Pastilla / vitamina") {
                    ping(30, "¿Ya tomaste lo que ibas a tomar?")
                }
                TextField("Aviso propio", text: $texto)
                Button("En 20 min") {
                    let t = texto.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !t.isEmpty else { return }
                    ping(20, t)
                    texto = ""
                }
                .disabled(texto.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
        .navigationTitle("Avisos")
    }

    private func ping(_ minutos: Int, _ mensaje: String) {
        agua.avisarEn(minutos: minutos, mensaje: mensaje)
        manager.crearAvisoEnIPhone(mensaje: mensaje, minutos: minutos)
    }
}
