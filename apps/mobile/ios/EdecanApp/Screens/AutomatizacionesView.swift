import SwiftUI
import EdecanKit

/// "Automatizaciones" (`ARCHITECTURE.md` §11 `ROADMAP_V2.md`
/// §7.4/§7.6/§7.10) — alcanzable desde el acceso directo en ``InicioView``
/// (no es una pestaña propia). Lista con activar/desactivar optimista, alta
/// con un disparador de agenda simple (presets de `rrule` + uno
/// personalizado, mismo criterio que
/// `apps/web/src/components/automatizaciones/AutomationForm.tsx`) y detalle
/// con sus últimas corridas (`automation_runs`).
///
/// Usa `List` (no `ScrollView`+`VStack` como ``MisionesView``) a propósito:
/// dentro de un `List`, un `Toggle` anidado en la etiqueta de un
/// `NavigationLink` recibe su propio área de toque — tocarlo NO dispara
/// también la navegación (algo que sí pasaría fuera de un `List`).
struct AutomatizacionesView: View {
    @Environment(SessionStore.self) private var session
    @State private var viewModel = AutomatizacionesViewModel()
    @State private var mostrarCrear = false

    var body: some View {
        Group {
            if viewModel.cargando && viewModel.automatizaciones.isEmpty {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if viewModel.automatizaciones.isEmpty {
                EmptyStateView(
                    icono: "bolt.badge.clock.fill",
                    titulo: "Sin automatizaciones todavía",
                    descripcion: viewModel.errorLista
                        ?? "Crea una con el botón + de arriba — una agenda que corre una instrucción del agente sola."
                )
            } else {
                lista
            }
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle("Automatizaciones")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    mostrarCrear = true
                } label: {
                    Image(systemName: "plus")
                }
            }
        }
        .task { await viewModel.cargar(client: session.client) }
        .refreshable { await viewModel.cargar(client: session.client) }
        .sheet(isPresented: $mostrarCrear) {
            NuevaAutomatizacionSheet(viewModel: viewModel)
                .environment(session)
        }
        .navigationDestination(for: String.self) { id in
            AutomatizacionDetalleView(automationId: id, viewModel: viewModel)
        }
    }

    private var lista: some View {
        List {
            if let error = viewModel.errorLista {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
                    .listRowBackground(Color.clear)
            }
            ForEach(viewModel.automatizaciones) { automatizacion in
                NavigationLink(value: automatizacion.id) {
                    HStack(spacing: 12) {
                        InfoAutomatizacion(automatizacion: automatizacion)
                        Spacer()
                        // El `Binding` se arma AQUÍ, con un closure literal
                        // capturando `automatizacion`/`viewModel`/`session`
                        // directo en su sitio de uso (MainActor, el cuerpo de
                        // esta vista) — en vez de reenviar un closure
                        // guardado como propiedad `let` de una vista hija.
                        // `Binding.init(get:set:)` exige `@Sendable` en
                        // Swift 6; un closure LITERAL pasado directo infiere
                        // eso solo, un valor de closure reenviado no.
                        Toggle(
                            "",
                            isOn: Binding(
                                get: { automatizacion.enabled },
                                set: { habilitada in
                                    Task {
                                        await viewModel.alternar(
                                            id: automatizacion.id, enabled: habilitada, client: session.client
                                        )
                                    }
                                }
                            )
                        )
                        .labelsHidden()
                        .disabled(viewModel.idEnToggle == automatizacion.id)
                        .tint(EdecanTheme.morado)
                    }
                    .padding(14)
                    .tarjetaVidrio(esquina: 14)
                    .padding(.vertical, 3)
                }
                .listRowSeparator(.hidden)
                .listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }
}

private struct InfoAutomatizacion: View {
    let automatizacion: AutomationOut

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(automatizacion.nombre).font(.subheadline.weight(.medium)).foregroundStyle(.primary)
            Text(subtitulo).font(.caption).foregroundStyle(.secondary).lineLimit(1)
        }
    }

    private var subtitulo: String {
        switch automatizacion.trigger {
        case .schedule(let rrule): return "Agenda: \(rrule)"
        case .webhook: return "Disparador: webhook entrante"
        }
    }
}

private struct AutomatizacionDetalleView: View {
    let automationId: String
    let viewModel: AutomatizacionesViewModel
    @Environment(SessionStore.self) private var session

    private var automatizacion: AutomationOut? {
        viewModel.automatizaciones.first { $0.id == automationId }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let automatizacion {
                    encabezado(automatizacion)
                }

                Text("Últimas corridas").font(.headline)
                if viewModel.cargandoRuns {
                    ProgressView().frame(maxWidth: .infinity)
                } else if let error = viewModel.errorRuns {
                    Text(error).font(.footnote).foregroundStyle(.red)
                } else if viewModel.runs.isEmpty {
                    Text("Todavía no corrió — espera a su próximo disparo.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    VStack(spacing: 10) {
                        ForEach(viewModel.runs) { corrida in
                            FilaCorrida(corrida: corrida)
                        }
                    }
                }
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
        .navigationTitle(automatizacion?.nombre ?? "Automatización")
        .navigationBarTitleDisplayMode(.inline)
        .task { await viewModel.cargarRuns(id: automationId, client: session.client) }
    }

    private func encabezado(_ automatizacion: AutomationOut) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(automatizacion.enabled ? "Activa" : "Desactivada")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(automatizacion.enabled ? .green : .secondary)
                Spacer()
                if let proxima = automatizacion.nextRunAt {
                    Text("Próxima: \(proxima.formatted(date: .abbreviated, time: .shortened))")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            if !automatizacion.descripcion.isEmpty {
                Text(automatizacion.descripcion).font(.subheadline)
            }
            if let secreto = automatizacion.hookSecret {
                Text("Guarda este secreto ahora, no se vuelve a mostrar: \(secreto)")
                    .font(.caption)
                    .foregroundStyle(.orange)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 14)
    }
}

private struct FilaCorrida: View {
    let corrida: AutomationRunOut

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(color).frame(width: 8, height: 8).padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(etiqueta).font(.caption.weight(.semibold)).foregroundStyle(color)
                    Spacer()
                    if let inicio = corrida.startedAt {
                        Text(inicio.formatted(date: .abbreviated, time: .shortened))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
                if case .string(let resultado)? = corrida.detalle["resultado"] {
                    Text(resultado).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                if case .string(let error)? = corrida.detalle["error"] {
                    Text(error).font(.caption).foregroundStyle(.red).lineLimit(2)
                }
            }
        }
        .padding(10)
        .tarjetaVidrio(esquina: 12)
    }

    private var etiqueta: String {
        switch corrida.status {
        case "running": return "Corriendo"
        case "done": return "Completada"
        case "error": return "Error"
        case "waiting_confirmation": return "Esperando confirmación"
        default: return corrida.status.capitalized
        }
    }

    private var color: Color {
        switch corrida.status {
        case "running": return EdecanTheme.azul
        case "done": return .green
        case "error": return .red
        case "waiting_confirmation": return .orange
        default: return .secondary
        }
    }
}

/// Formulario "alta simple" — mismo criterio de presets que
/// `apps/web/src/components/automatizaciones/AutomationForm.tsx`: 3
/// frecuencias listas para usar + una `rrule` personalizada para quien la
/// sepa escribir. Crear un disparador webhook queda fuera de esta app.
private struct NuevaAutomatizacionSheet: View {
    let viewModel: AutomatizacionesViewModel
    @Environment(\.dismiss) private var dismiss
    @Environment(SessionStore.self) private var session

    private enum Preset: String, CaseIterable, Identifiable {
        case diario, semanal, mensual, personalizada

        var id: String { rawValue }

        var etiqueta: String {
            switch self {
            case .diario: return "Diario a las 9:00"
            case .semanal: return "Semanal (lunes) a las 9:00"
            case .mensual: return "Mensual (día 1) a las 9:00"
            case .personalizada: return "Personalizada (rrule RFC 5545)"
            }
        }

        var rrule: String? {
            switch self {
            case .diario: return "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
            case .semanal: return "FREQ=WEEKLY;BYDAY=MO;BYHOUR=9;BYMINUTE=0"
            case .mensual: return "FREQ=MONTHLY;BYMONTHDAY=1;BYHOUR=9;BYMINUTE=0"
            case .personalizada: return nil
            }
        }
    }

    @State private var nombre = ""
    @State private var descripcion = ""
    @State private var preset: Preset = .diario
    @State private var rruleManual = "FREQ=DAILY;BYHOUR=9;BYMINUTE=0"
    @State private var instruccion = ""

    private var rrule: String { preset.rrule ?? rruleManual }

    var body: some View {
        NavigationStack {
            Form {
                Section("Nombre") {
                    TextField("Reporte de ventas diario", text: $nombre)
                }
                Section("Frecuencia") {
                    Picker("Frecuencia", selection: $preset) {
                        ForEach(Preset.allCases) { p in Text(p.etiqueta).tag(p) }
                    }
                    if preset == .personalizada {
                        TextField("FREQ=DAILY;BYHOUR=9", text: $rruleManual)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                }
                Section {
                    TextField("Descripción (opcional)", text: $descripcion)
                    TextField("Instrucción para el agente", text: $instruccion, axis: .vertical)
                        .lineLimit(3...6)
                } footer: {
                    Text("Lo que el agente debe hacer en cada corrida, sin herramientas peligrosas.")
                }
                if let error = viewModel.errorCreacion {
                    Section { Text(error).font(.footnote).foregroundStyle(.red) }
                }
            }
            .navigationTitle("Nueva automatización")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await crear() }
                    } label: {
                        if viewModel.creando { ProgressView() } else { Text("Crear") }
                    }
                    .disabled(viewModel.creando || !formularioValido)
                }
            }
        }
    }

    private func crear() async {
        let creada = await viewModel.crear(
            nombre: nombre, descripcion: descripcion, rrule: rrule, instruccion: instruccion,
            client: session.client
        )
        if creada != nil { dismiss() }
    }

    private var formularioValido: Bool {
        !nombre.trimmingCharacters(in: .whitespaces).isEmpty
            && !instruccion.trimmingCharacters(in: .whitespaces).isEmpty
            && !rrule.trimmingCharacters(in: .whitespaces).isEmpty
    }
}
