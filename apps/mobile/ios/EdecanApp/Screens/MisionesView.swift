import SwiftUI
import EdecanKit

/// "Misiones" — el Orchestrator multi-agente (`ARCHITECTURE.md` §11
/// `ROADMAP_V2.md` §7.4/§7.9): lista con *polling* + alta con un campo
/// `objetivo`, detalle con la línea de tiempo de `agent_steps` y la tarjeta
/// de aprobación reutilizada de Chat cuando un paso queda
/// `waiting_confirmation`. Alcanzable desde el acceso directo "Misiones" en
/// ``InicioView`` — no es una pestaña propia (`RootTabView` no cambia).
struct MisionesView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = MisionesViewModel()
    @State private var nuevoObjetivo = ""

    var body: some View {
        Group {
            if viewModel.cargandoLista && viewModel.misiones.isEmpty {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                contenido
            }
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Misiones")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.cargarMisiones(client: session.client)
            viewModel.iniciarPollingLista(client: session.client)
        }
        .onDisappear { viewModel.detenerPollingLista() }
        .refreshable { await viewModel.cargarMisiones(client: session.client) }
        .navigationDestination(for: String.self) { id in
            MisionDetalleView(missionId: id, viewModel: viewModel)
        }
    }

    private var contenido: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                tarjetaCrear
                if let error = viewModel.errorLista {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                if viewModel.misiones.isEmpty {
                    EmptyStateView(
                        icono: "point.3.filled.connected.trianglepath.dotted",
                        titulo: "Sin misiones todavía",
                        descripcion: "Dale un objetivo arriba, o pídeselo a Edecán en el chat."
                    )
                    .padding(.top, 12)
                } else {
                    listaDeMisiones
                }
            }
            .padding()
        }
    }

    private var tarjetaCrear: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Nueva misión").font(.headline)
            TextField(
                "Ej: Investiga a mis 3 competidores y resume sus precios",
                text: $nuevoObjetivo, axis: .vertical
            )
            .lineLimit(2...4)
            .textFieldStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 12, style: .continuous))

            if let error = viewModel.errorCreacion {
                Text(error).font(.caption).foregroundStyle(.red)
            }

            Button {
                Task { await crear() }
            } label: {
                if viewModel.creando {
                    ProgressView().frame(maxWidth: .infinity)
                } else {
                    Label("Crear misión", systemImage: "plus.circle.fill").frame(maxWidth: .infinity)
                }
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(viewModel.creando || nuevoObjetivo.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        }
        .padding(16)
        .tarjetaVidrio(esquina: 18)
    }

    private var listaDeMisiones: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Tus misiones").font(.headline)
            VStack(spacing: 10) {
                ForEach(viewModel.misiones) { mision in
                    NavigationLink(value: mision.id) {
                        FilaMision(mision: mision)
                    }
                    .buttonStyle(.plain)
                }
            }
        }
    }

    private func crear() async {
        guard await viewModel.crear(objetivo: nuevoObjetivo, client: session.client) != nil else { return }
        nuevoObjetivo = ""
    }
}

private struct FilaMision: View {
    let mision: MissionOut

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 4) {
                Text(mision.objetivo)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                    .lineLimit(2)
                Text(mision.createdAt.formatted(date: .abbreviated, time: .shortened))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            EtiquetaEstadoMision(status: mision.status)
            Image(systemName: "chevron.right").font(.caption2).foregroundStyle(.tertiary)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 14)
    }
}

/// Detalle de una misión: pasos (`agent_steps`) en orden con su resultado, y
/// si algún paso quedó `waiting_confirmation`, la tarjeta de Aprobar/Rechazar
/// (``TarjetaConfirmacion``, la misma que usa Chat/Voz).
private struct MisionDetalleView: View {
    let missionId: String
    let viewModel: MisionesViewModel
    @Environment(SessionStore.self) private var session

    var body: some View {
        Group {
            if viewModel.cargandoDetalle && viewModel.detalle == nil {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let detalle = viewModel.detalle {
                contenido(detalle)
            } else {
                EmptyStateView(
                    icono: "exclamationmark.triangle.fill",
                    titulo: "No se pudo cargar",
                    descripcion: viewModel.errorDetalle ?? "Vuelve a intentarlo."
                )
            }
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Misión")
        .navigationBarTitleDisplayMode(.inline)
        .task {
            await viewModel.cargarDetalle(id: missionId, client: session.client)
            viewModel.iniciarPollingDetalle(id: missionId, client: session.client)
        }
        .onDisappear {
            viewModel.detenerPollingDetalle()
            viewModel.cerrarDetalle()
        }
    }

    private func contenido(_ detalle: MissionDetailOut) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                HStack {
                    EtiquetaEstadoMision(status: detalle.mission.status)
                    Spacer()
                    if !detalle.mission.esTerminal {
                        Button("Cancelar misión", role: .destructive) {
                            Task { await viewModel.cancelar(client: session.client) }
                        }
                        .font(.caption)
                        .disabled(viewModel.accionEnCurso)
                    }
                }
                Text(detalle.mission.objetivo).font(.title3.weight(.semibold))

                if let error = viewModel.errorDetalle {
                    Text(error).font(.footnote).foregroundStyle(.red)
                }
                if let error = detalle.mission.error {
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.red)
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.red.opacity(0.1), in: RoundedRectangle(cornerRadius: 10))
                }
                if let resultado = detalle.mission.resultado {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("Resultado").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                        Text(resultado).font(.subheadline)
                    }
                    .padding(12)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .tarjetaVidrio(esquina: 14)
                }

                Text("Pasos").font(.headline)
                if detalle.steps.isEmpty {
                    Text("Todavía no hay pasos planificados.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 12) {
                        ForEach(detalle.steps) { paso in
                            FilaPaso(paso: paso)
                            if paso.status == "waiting_confirmation", let pendiente = paso.pendingToolCall {
                                TarjetaConfirmacion(
                                    // `indiceMensaje` es un detalle interno de `ChatViewModel`
                                    // (índice en su lista de mensajes) sin equivalente aquí —
                                    // esta pantalla no tiene mensajes, así que se deja en 0: la
                                    // vista ``TarjetaConfirmacion`` nunca lo lee, solo lo carga
                                    // `ChatViewModel` para saber a qué burbuja apendear texto.
                                    confirmacion: .init(
                                        toolCallId: pendiente.id,
                                        nombre: pendiente.name,
                                        args: pendiente.args,
                                        indiceMensaje: 0
                                    ),
                                    deshabilitada: viewModel.accionEnCurso
                                ) { aprobado in
                                    Task { await viewModel.confirmar(aprobado: aprobado, client: session.client) }
                                }
                            }
                        }
                    }
                }
            }
            .padding()
        }
    }
}

private struct FilaPaso: View {
    let paso: MissionStepOut

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            puntoDeEstado
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text("#\(paso.seq)").font(.caption2.weight(.semibold)).foregroundStyle(.secondary)
                    Text(nombreAgente).font(.subheadline.weight(.medium))
                    EtiquetaEstadoPaso(status: paso.status)
                }
                Text(paso.instruccion).font(.footnote).foregroundStyle(.secondary)
                if let resultado = paso.resultado, !resultado.isEmpty {
                    Text(resultado)
                        .font(.caption)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                }
            }
        }
    }

    private var puntoDeEstado: some View {
        Group {
            if paso.status == "running" {
                ProgressView().controlSize(.mini)
            } else {
                Circle().fill(colorDePunto).frame(width: 9, height: 9)
            }
        }
        .frame(width: 16, height: 16)
        .padding(.top, 3)
    }

    private var colorDePunto: Color {
        switch paso.status {
        case "done": return .green
        case "waiting_confirmation": return .orange
        case "error": return .red
        default: return .secondary.opacity(0.35)
        }
    }

    private var nombreAgente: String {
        paso.agente
            .split(separator: "_")
            .map { $0.prefix(1).uppercased() + $0.dropFirst() }
            .joined(separator: " ")
    }
}

private struct EtiquetaEstadoMision: View {
    let status: String

    var body: some View {
        Text(etiqueta)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(color.opacity(0.18), in: Capsule())
            .foregroundStyle(color)
    }

    private var etiqueta: String {
        switch status {
        case "planning": return "Planificando"
        case "running": return "En curso"
        case "waiting_confirmation": return "Esperando confirmación"
        case "done": return "Completada"
        case "error": return "Error"
        case "cancelled": return "Cancelada"
        default: return status.capitalized
        }
    }

    private var color: Color {
        switch status {
        case "running": return EdecanTheme.azul
        case "waiting_confirmation": return .orange
        case "done": return .green
        case "error": return .red
        default: return .secondary
        }
    }
}

private struct EtiquetaEstadoPaso: View {
    let status: String

    var body: some View {
        Text(etiqueta)
            .font(.caption2.weight(.medium))
            .foregroundStyle(color)
    }

    private var etiqueta: String {
        switch status {
        case "pending": return "Pendiente"
        case "running": return "Ejecutando"
        case "waiting_confirmation": return "Esperando confirmación"
        case "done": return "Hecho"
        case "error": return "Error"
        case "skipped": return "Omitido"
        default: return status.capitalized
        }
    }

    private var color: Color {
        switch status {
        case "running": return EdecanTheme.azul
        case "waiting_confirmation": return .orange
        case "done": return .green
        case "error": return .red
        default: return .secondary
        }
    }
}
