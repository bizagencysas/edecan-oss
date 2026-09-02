import EdecanKit
import Foundation
import SwiftUI

/// Reporte semanal del gimnasio: resumen con IA de la última semana
/// (progreso, tendencias, recomendaciones de deload/objetivo). Best-effort:
/// si el backend no tiene suficientes sesiones, degrada con un mensaje
/// honesto en vez de inventar nada.
struct GymReporteSemanalView: View {
    @Environment(SessionStore.self) private var session
    @State private var reporte: String?
    @State private var cargando = false
    @State private var errorMensaje: String?

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                if let errorMensaje {
                    Text(errorMensaje)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(12)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .tarjetaVidrio(esquina: 14)
                }

                if cargando && reporte == nil {
                    ProgressView("Generando tu reporte…")
                        .frame(maxWidth: .infinity, minHeight: 180)
                } else if let reporte {
                    tarjetaReporte(reporte)
                } else if errorMensaje == nil {
                    Text("Todavía no hay suficientes sesiones esta semana para un reporte.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                        .padding(16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .tarjetaVidrio(esquina: 16)
                }
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.12).ignoresSafeArea())
        .navigationTitle("Reporte semanal")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await cargar()
        }
        .refreshable {
            await cargar()
        }
    }

    // MARK: - Tarjeta

    private func tarjetaReporte(_ texto: String) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 10) {
                Image(systemName: "chart.line.uptrend.xyaxis")
                    .font(.title2)
                    .foregroundStyle(EdecanTheme.morado)
                Text("Tu semana en resumen")
                    .font(.headline)
            }
            Text(texto)
                .font(.body)
                .foregroundStyle(.secondary)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Carga

    private func cargar() async {
        cargando = true
        defer { cargando = false }
        guard let client = session.client else {
            errorMensaje = "Sin conexión con el servidor."
            return
        }
        do {
            let out = try await client.gymReporteSemanal()
            let texto = out.reporte.trimmingCharacters(in: .whitespacesAndNewlines)
            reporte = texto.isEmpty ? nil : texto
            errorMensaje = nil
        } catch {
            errorMensaje = error.localizedDescription
        }
    }
}