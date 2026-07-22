import EdecanKit
import SwiftUI

/// Historial de telefonía real. Iniciar una llamada vuelve al lenguaje
/// natural del chat, donde la confirmación explícita sigue siendo obligatoria.
struct LlamadasView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @State private var llamadas: [PhoneCallOut] = []
    @State private var cargando = false
    @State private var mensajeNoDisponible: String?
    @State private var errorMensaje: String?
    @State private var estadosObservados: [String: String] = [:]

    var body: some View {
        Group {
            if cargando && llamadas.isEmpty {
                ProgressView("Cargando llamadas…")
            } else if let mensajeNoDisponible, llamadas.isEmpty {
                EmptyStateView(
                    icono: "phone.badge.waveform",
                    titulo: "Llamadas no habilitadas",
                    descripcion: mensajeNoDisponible
                )
                .padding()
            } else if llamadas.isEmpty {
                EmptyStateView(
                    icono: "phone",
                    titulo: "Aún no hay llamadas",
                    descripcion: "Pídesela a Edecan con una frase. Antes de llamar, siempre te mostrará el destino y el objetivo para confirmar."
                )
                .padding()
            } else {
                List(llamadas) { llamada in
                    fila(llamada)
                }
                .listStyle(.plain)
            }
        }
        .navigationTitle("Llamadas")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    tabRouter.pedir("Llama a ")
                } label: {
                    Image(systemName: "phone.badge.plus")
                }
                .accessibilityLabel("Pedir una llamada")
            }
        }
        .safeAreaInset(edge: .bottom) {
            if let errorMensaje {
                Text(errorMensaje)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .padding()
                    .frame(maxWidth: .infinity)
                    .background(.bar)
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private func fila(_ llamada: PhoneCallOut) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: llamada.direction == "incoming" ? "phone.arrow.down.left.fill" : "phone.arrow.up.right.fill")
                .foregroundStyle(color(llamada.status))
                .frame(width: 28, height: 28)
            VStack(alignment: .leading, spacing: 5) {
                Text(llamada.direction == "incoming" ? llamada.fromE164 : llamada.toE164)
                    .font(.body.weight(.semibold))
                Text(llamada.goal)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                HStack(spacing: 8) {
                    Text(nombreEstado(llamada.status))
                    if let duracion = llamada.durationSeconds {
                        Text(Duration.seconds(duracion).formatted(.time(pattern: .minuteSecond)))
                    }
                    if let fecha = llamada.startedAt ?? llamada.createdAt {
                        Text(fecha, format: .dateTime.day().month().hour().minute())
                    }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                if let error = llamada.error, !error.isEmpty {
                    Text(error).font(.caption).foregroundStyle(.red)
                }
            }
        }
        .padding(.vertical, 5)
    }

    private func cargar() async {
        guard let client = session.client, !cargando else { return }
        cargando = true
        errorMensaje = nil
        mensajeNoDisponible = nil
        defer { cargando = false }
        do {
            let nuevas = try await client.listarLlamadas()
            if !estadosObservados.isEmpty {
                for llamada in nuevas {
                    let anterior = estadosObservados[llamada.id]
                    let cambio = anterior != llamada.status
                    guard cambio, llamada.status == "ringing" ||
                        ["completed", "failed", "busy", "no_answer"].contains(llamada.status)
                    else { continue }
                    Task {
                        await LocalNotificationScheduler.completed(
                            kind: "call",
                            id: llamada.id,
                            title: llamada.status == "ringing" ? "Llamada entrante" : "Llamada actualizada",
                            body: llamada.goal,
                            route: .activity
                        )
                    }
                }
            }
            llamadas = nuevas
            estadosObservados = Dictionary(uniqueKeysWithValues: nuevas.map { ($0.id, $0.status) })
        } catch APIClient.APIError.servidor(let status, let mensaje) where status == 403 {
            mensajeNoDisponible = mensaje
            llamadas = []
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    private func nombreEstado(_ status: String) -> String {
        switch status {
        case "draft": "Por confirmar"
        case "confirmed": "Confirmada"
        case "queued": "En cola"
        case "ringing": "Sonando"
        case "in_progress": "En curso"
        case "completed": "Completada"
        case "failed": "Fallida"
        case "busy": "Ocupado"
        case "no_answer": "Sin respuesta"
        case "cancelled": "Cancelada"
        default: status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    private func color(_ status: String) -> Color {
        switch status {
        case "completed": .green
        case "failed", "busy", "no_answer": .red
        case "ringing", "in_progress": EdecanTheme.morado
        default: EdecanTheme.azul
        }
    }
}
