import SwiftUI
import EdecanKit

/// Actividad: accesos simples al trabajo que Edecan ejecuta o vigila.
struct InicioView: View {
    @Environment(SessionStore.self) private var session
    @State private var trabajandoAhora: [PersistentWorker] = []
    @State private var aprobaciones: [PendingApproval] = []
    @State private var fallasAutomatizacion: [AutomationSuggestion] = []
    @State private var proximasRutinas: [AutomationOut] = []
    @State private var errorBrief: String?
    @State private var ocupadoAprobacion = false
    @State private var briefCargado = false

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    introduccion
                    resumenDiario
                    accesosDirectos
                    if let error = session.errorMensaje {
                        Text(error)
                            .font(.footnote)
                            .foregroundStyle(.red)
                            .tarjetaVidrio(esquina: 14)
                    }
                }
                .padding()
            }
            .background(EdecanTheme.degradado.opacity(0.06).ignoresSafeArea())
            .navigationTitle("Actividad")
            .task {
                await session.cargarMe()
                await cargarBrief()
            }
            .refreshable {
                await session.cargarMe()
                await cargarBrief()
            }
        }
    }

    private var introduccion: some View {
        VStack(alignment: .leading, spacing: 4) {
            if session.cargandoMe && session.me == nil {
                ProgressView()
            } else {
                Text(saludoConNombre)
                    .font(.title.weight(.bold))
                Text("Tu equipo sigue trabajando. Aquí está lo que necesita tu mirada.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// "Buenos días/tardes/noches" según la hora local del teléfono.
    private var saludo: String {
        let hora = Calendar.current.component(.hour, from: Date())
        switch hora {
        case 5..<12: return "Buenos días"
        case 12..<19: return "Buenas tardes"
        default: return "Buenas noches"
        }
    }

    private var saludoConNombre: String {
        if let nombre = session.me?.nombrePila, !nombre.isEmpty {
            return "\(saludo), \(nombre)"
        }
        return saludo
    }

    // MARK: - Resumen del día (jefe de staff)

    @ViewBuilder
    private var resumenDiario: some View {
        if !aprobaciones.isEmpty || !fallasAutomatizacion.isEmpty || !trabajandoAhora.isEmpty || !proximasRutinas.isEmpty {
            VStack(alignment: .leading, spacing: 14) {
                if let errorBrief, !errorBrief.isEmpty {
                    Text(errorBrief)
                        .font(.footnote)
                        .foregroundStyle(.red)
                }

                if !aprobaciones.isEmpty || !fallasAutomatizacion.isEmpty {
                    seccionAtencion
                }
                if !trabajandoAhora.isEmpty {
                    seccionTrabajando
                }
                if !proximasRutinas.isEmpty {
                    seccionProximamente
                }
            }
        } else if briefCargado {
            todoAlDia
        }
    }

    /// Cuando no hay nada esperando, en vez de dejar el home muerto se
    /// confirma en una sola línea que está todo al día.
    private var todoAlDia: some View {
        HStack(spacing: 10) {
            Image(systemName: "checkmark.circle.fill")
                .foregroundStyle(.green)
            VStack(alignment: .leading, spacing: 2) {
                Text("Todo al día")
                    .font(.subheadline.weight(.semibold))
                Text("Nada esperando tu aprobación ni compañeros detenidos.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private var seccionAtencion: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Necesita tu atención")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(.orange)

            ForEach(aprobaciones) { aprobacion in
                filaAprobacionBrief(aprobacion)
            }
            ForEach(fallasAutomatizacion) { falla in
                NavigationLink {
                    AutomatizacionesView()
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "bolt.badge.clock.fill")
                            .foregroundStyle(.orange)
                            .padding(.top, 1)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(falla.nombre ?? "Rutina")
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(.primary)
                            Text("Falló \(falla.failureCount ?? 1) \(falla.failureCount ?? 1 == 1 ? "vez" : "veces") consecutivas.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 8)
                        Image(systemName: "chevron.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(.tertiary)
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private func filaAprobacionBrief(_ aprobacion: PendingApproval) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(.orange)
                .frame(width: 8, height: 8)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                Text(aprobacion.name ?? "Herramienta")
                    .font(.subheadline.weight(.semibold))
                if !aprobacion.argsPreview.isEmpty {
                    Text(aprobacion.argsPreview)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(2)
                }
                HStack(spacing: 8) {
                    Button("Aprobar") { Task { await decidirAprobacion(aprobacion, aprobar: true) } }
                        .buttonStyle(.borderedProminent)
                        .tint(EdecanTheme.morado)
                        .controlSize(.small)
                    Button("Denegar") { Task { await decidirAprobacion(aprobacion, aprobar: false) } }
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                .disabled(ocupadoAprobacion)
            }
            Spacer(minLength: 8)
        }
    }

    private var seccionTrabajando: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Trabajando ahora")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)

            ForEach(trabajandoAhora) { worker in
                HStack(spacing: 10) {
                    Circle()
                        .fill(EdecanTheme.morado.opacity(0.16))
                        .frame(width: 34, height: 34)
                        .overlay {
                            Text(iniciales(worker.nombreVisible))
                                .font(.system(size: 14, weight: .bold, design: .rounded))
                                .foregroundStyle(EdecanTheme.morado)
                        }
                    VStack(alignment: .leading, spacing: 2) {
                        Text(worker.nombreVisible)
                            .font(.subheadline.weight(.medium))
                        Text(worker.cargoVisible)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer()
                    Label("En curso", systemImage: "circle.fill")
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(.green)
                        .labelStyle(.titleAndIcon)
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    private func iniciales(_ nombre: String) -> String {
        let partes = nombre.split(separator: " ").prefix(2)
        return partes.compactMap { $0.first }.map(String.init).joined().uppercased()
    }

    /// Rutinas con `nextRunAt` en el futuro, ordenadas por su próxima corrida.
    /// "Próximamente" es un vistazo tranquilo a lo que viene, no un muro de
    /// reglas: se muestran solo las primeras.
    private var seccionProximamente: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Próximamente")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.azul)

            ForEach(proximasRutinas) { rutina in
                NavigationLink {
                    AutomatizacionesView()
                } label: {
                    HStack(alignment: .top, spacing: 10) {
                        Image(systemName: "clock")
                            .foregroundStyle(EdecanTheme.azul)
                            .padding(.top, 1)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(rutina.nombre.isEmpty ? "Rutina" : rutina.nombre)
                                .font(.subheadline.weight(.medium))
                                .foregroundStyle(.primary)
                            Text(rutina.trigger.resumen)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        Spacer(minLength: 8)
                        if let proxima = rutina.nextRunAt {
                            Text(proximaRelativa(proxima))
                                .font(.caption2.weight(.medium))
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
    }

    /// "En 5 min" / "En 2 h" / "En 3 d" — misma voz que el "Hace X" del
    /// roster, para que Inicio se lea como la misma app.
    private func proximaRelativa(_ fecha: Date) -> String {
        let minutos = Int(fecha.timeIntervalSinceNow / 60)
        if minutos < 1 { return "Pronto" }
        if minutos < 60 { return "En \(minutos) min" }
        let horas = minutos / 60
        if horas < 24 { return "En \(horas) h" }
        return "En \(horas / 24) d"
    }

    private func cargarBrief() async {
        guard let client = session.client else { return }
        errorBrief = nil
        do {
            async let workers = try client.listWorkers()
            async let aprob = try client.listApprovals()
            async let sugerencias = try client.listAutomationSuggestions()
            async let automatizaciones = try client.listAutomations()
            let (w, a, sug, aut) = try await (workers, aprob, sugerencias, automatizaciones)
            trabajandoAhora = w.filter { $0.enabled && $0.status == "running" }
            aprobaciones = a
            fallasAutomatizacion = sug.filter {
                $0.kind == "automation_suggestion" && ($0.failureCount ?? 0) > 0
            }
            let ahora = Date()
            proximasRutinas = Array(
                aut
                    .filter { $0.enabled && ($0.nextRunAt.map { $0 > ahora } ?? false) }
                    .sorted { ($0.nextRunAt ?? .distantFuture) < ($1.nextRunAt ?? .distantFuture) }
                    .prefix(4)
            )
        } catch {
            errorBrief = "No pude cargar la actividad: \(error.localizedDescription)"
        }
        briefCargado = true
    }

    private func decidirAprobacion(_ aprobacion: PendingApproval, aprobar: Bool) async {
        guard let client = session.client else { return }
        ocupadoAprobacion = true
        defer { ocupadoAprobacion = false }
        do {
            if aprobar {
                try await client.approveApproval(id: aprobacion.id)
            } else {
                try await client.denyApproval(id: aprobacion.id)
            }
            await cargarBrief()
        } catch {
            errorBrief = error.localizedDescription
        }
    }

    private var accesosDirectos: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 14) {
            NavigationLink {
                MisionesView()
            } label: {
                AccesoDirecto(
                    icono: "point.3.filled.connected.trianglepath.dotted",
                    titulo: "Trabajo delegado",
                    subtitulo: "Objetivos y aprobaciones"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                BotsView()
            } label: {
                AccesoDirecto(
                    icono: "sparkles",
                    titulo: "Bots",
                    subtitulo: "Chats con tus bots"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                ActivityView()
            } label: {
                AccesoDirecto(
                    icono: "list.bullet.rectangle",
                    titulo: "Registro de actividad",
                    subtitulo: "Lo que han hecho últimamente"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                SeguridadView()
            } label: {
                AccesoDirecto(
                    icono: "lock.shield.fill",
                    titulo: "Seguridad",
                    subtitulo: "Cuentas, autonomía y freno de emergencia"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                AutomatizacionesView()
            } label: {
                AccesoDirecto(
                    icono: "bolt.badge.clock.fill",
                    titulo: "Rutinas",
                    subtitulo: "Acciones programadas"
                )
            }
            .buttonStyle(.plain)
            NavigationLink {
                RecordatoriosView()
            } label: {
                AccesoDirecto(icono: "bell.badge.fill", titulo: "Recordatorios", subtitulo: "Pendientes y completados")
            }
            .buttonStyle(.plain)
            NavigationLink {
                LlamadasView()
            } label: {
                AccesoDirecto(icono: "phone.badge.waveform", titulo: "Llamadas", subtitulo: "Entrantes, salientes y estado")
            }
            .buttonStyle(.plain)
            NavigationLink {
                RemotoView()
            } label: {
                AccesoDirecto(icono: "display", titulo: "Remoto", subtitulo: "Ver y controlar tu Mac/PC")
            }
            .buttonStyle(.plain)
            NavigationLink {
                ComputerView()
            } label: {
                AccesoDirecto(icono: "desktopcomputer", titulo: "Computadora", subtitulo: "Toma el control o pausa al agente")
            }
            .buttonStyle(.plain)
            NavigationLink {
                WorkspacesView()
            } label: {
                AccesoDirecto(icono: "square.stack.3d.up.fill", titulo: "Workspaces", subtitulo: "Agentes por contexto de trabajo")
            }
            .buttonStyle(.plain)
            NavigationLink {
                GymView()
            } label: {
                AccesoDirecto(icono: "figure.strengthtraining.traditional", titulo: "Entrenamiento", subtitulo: "Tu rutina de gimnasio de hoy")
            }
            .buttonStyle(.plain)
        }
    }
}

private struct AccesoDirecto: View {
    let icono: String
    let titulo: String
    let subtitulo: String

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Image(systemName: icono)
                .font(.title2)
                .foregroundStyle(EdecanTheme.degradado)
            Text(titulo)
                .font(.headline)
            Text(subtitulo)
                .font(.caption)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(16)
        .tarjetaVidrio(esquina: 18)
        // Sin esto, dentro de un `NavigationLink` con `.buttonStyle(.plain)`
        // SwiftUI solo acepta el toque en lo OPACO: el icono y los dos textos.
        // El relleno de 16 puntos y el fondo de vidrio no responden, así que la
        // tarjeta se ve grande y se comporta como si fuera del tamaño de su
        // título -- hay que apuntarle a la palabra. La forma se declara con la
        // MISMA esquina que dibuja `tarjetaVidrio` para que lo tocable coincida
        // exactamente con lo que se ve, ni un punto de más ni de menos.
        .contentShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
    }
}
