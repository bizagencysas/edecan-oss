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
                    NavigationLink {
                        DetalleLlamadaView(
                            llamada: llamada,
                            nombreEstado: nombreEstado(llamada.status),
                            colorEstado: color(llamada.status)
                        )
                    } label: {
                        fila(llamada)
                    }
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

/// El historial sigue siendo una sola tarjeta ligera en Actividad. El
/// detalle aparece solo al tocar una llamada y traduce el JSON del backend a
/// decisiones y pendientes que una persona puede entender de un vistazo.
private struct DetalleLlamadaView: View {
    let llamada: PhoneCallOut
    let nombreEstado: String
    let colorEstado: Color

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Label(
                            llamada.direction == "incoming" ? "Entrante" : "Saliente",
                            systemImage: llamada.direction == "incoming"
                                ? "phone.arrow.down.left.fill"
                                : "phone.arrow.up.right.fill"
                        )
                        Spacer()
                        Text(nombreEstado)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(colorEstado)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 5)
                            .background(colorEstado.opacity(0.12), in: Capsule())
                    }
                    Text(numeroContacto)
                        .font(.title3.weight(.semibold))
                    Text(llamada.goal)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)

                if let agente = nombreAgente {
                    LabeledContent("Agente", value: agente)
                }
                if let duracion = llamada.durationSeconds ?? llamada.summary?.durationSeconds {
                    LabeledContent(
                        "Duración",
                        value: Duration.seconds(duracion).formatted(.time(pattern: .minuteSecond))
                    )
                }
                if let fecha = llamada.startedAt ?? llamada.createdAt {
                    LabeledContent {
                        Text(fecha, format: .dateTime.day().month().year().hour().minute())
                    } label: {
                        Text("Fecha")
                    }
                }
                if let error = llamada.error, !error.isEmpty {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(.red)
                }
            }

            if let summary = llamada.summary {
                resumen(summary.keyPoints, titulo: "Puntos clave", vacio: "No se registraron puntos clave.")
                resumen(summary.commitments, titulo: "Compromisos", vacio: "No quedaron compromisos.")
                resumen(summary.nextSteps, titulo: "Próximos pasos", vacio: "No quedaron pasos pendientes.")

                Section("Transcripción") {
                    if summary.transcript.available {
                        Label(
                            textoTranscripcion(summary.transcript),
                            systemImage: "checkmark.circle.fill"
                        )
                        .foregroundStyle(.green)
                    } else {
                        Label(
                            textoTranscripcion(summary.transcript),
                            systemImage: "xmark.circle"
                        )
                        .foregroundStyle(.secondary)
                    }
                }
            } else {
                Section("Resumen") {
                    Label(
                        resumenPendiente,
                        systemImage: llamadaTerminada ? "doc.text.magnifyingglass" : "clock"
                    )
                    .foregroundStyle(.secondary)
                }
            }
        }
        .navigationTitle("Detalle de llamada")
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder
    private func resumen(_ elementos: [String], titulo: String, vacio: String) -> some View {
        Section(titulo) {
            if elementos.isEmpty {
                Text(vacio).foregroundStyle(.secondary)
            } else {
                ForEach(Array(elementos.enumerated()), id: \.offset) { _, elemento in
                    Label(elemento, systemImage: "checkmark")
                }
            }
        }
    }

    private var numeroContacto: String {
        llamada.direction == "incoming" ? llamada.fromE164 : llamada.toE164
    }

    private var nombreAgente: String? {
        let nombre = llamada.agent?.name?.trimmingCharacters(in: .whitespacesAndNewlines)
        let plantilla = llamada.agent?.templateName?.trimmingCharacters(in: .whitespacesAndNewlines)
        if let nombre, !nombre.isEmpty, let plantilla, !plantilla.isEmpty {
            return "\(nombre) · \(plantilla)"
        }
        if let nombre, !nombre.isEmpty { return nombre }
        if let plantilla, !plantilla.isEmpty { return plantilla }
        return nil
    }

    private var llamadaTerminada: Bool {
        ["completed", "failed", "busy", "no_answer", "cancelled"].contains(llamada.status)
    }

    private var resumenPendiente: String {
        llamadaTerminada
            ? "Esta llamada terminó sin un resumen disponible."
            : "El resumen aparecerá aquí cuando termine la llamada."
    }

    private func textoTranscripcion(_ transcript: PhoneCallTranscriptOut) -> String {
        guard transcript.available else { return "No hay transcripción disponible" }
        let turnos = transcript.turnCount
        return turnos == 1
            ? "Transcripción disponible · 1 intervención"
            : "Transcripción disponible · \(turnos) intervenciones"
    }
}
