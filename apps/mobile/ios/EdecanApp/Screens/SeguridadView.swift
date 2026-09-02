import SwiftUI
import EdecanKit

/// Centro de seguridad: cuentas conectadas, nivel de autonomía de cada
/// compañero, aprobaciones pendientes, sesiones de computadora y un freno de
/// emergencia ("Pausar todos los agentes" → `POST /v1/agents/workers/pause-all`).
/// Cada sección es best-effort: si una ruta todavía no aterrizó, esa sección
/// degrada con "Próximamente" en vez de tumbar la pantalla (directiva §153).
struct SeguridadView: View {
    @Environment(SessionStore.self) private var session
    @State private var workers: [PersistentWorker] = []
    @State private var aprobaciones: [PendingApproval] = []
    @State private var sesiones: [ComputerSession] = []
    @State private var credenciales: CredentialsOut?
    @State private var cargando = true
    @State private var ocupado = false
    @State private var error: String?
    @State private var avisoPausa: String?
    @State private var confirmarPausa = false

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }
            if let avisoPausa {
                Label(avisoPausa, systemImage: "checkmark.circle.fill")
                    .font(.footnote)
                    .foregroundStyle(.green)
                    .listRowSeparator(.hidden)
            }

            Section {
                botonPausa
            } footer: {
                Text("Detiene a todos los compañeros de inmediato. Puedes reactivarlos cuando quieras.")
            }

            Section("Cuentas conectadas") {
                if credenciales == nil && !cargando {
                    Text("No se pudieron leer las cuentas conectadas.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(filasCuentas) { cuenta in
                        filaCuenta(cuenta)
                    }
                }
            }

            Section("Autonomía de los agentes") {
                if workers.isEmpty && !cargando {
                    Text("No hay compañeros todavía.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(workers) { worker in
                        filaAutonomia(worker)
                    }
                }
            }

            Section("Aprobaciones pendientes") {
                if aprobaciones.isEmpty && !cargando {
                    Text("Nada esperando tu OK.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(aprobaciones) { aprobacion in
                        FilaAprobacion(aprobacion: aprobacion, ocupado: ocupado) { aprobar in
                            Task { await decidirAprobacion(aprobacion, aprobar: aprobar) }
                        }
                    }
                }
            }

            Section("Sesiones de computadora") {
                if sesiones.isEmpty && !cargando {
                    Text("Ninguna sesión activa.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(sesiones) { sesion in
                        filaSesion(sesion)
                    }
                }
                NavigationLink {
                    ComputerView()
                } label: {
                    Label("Administrar computadora", systemImage: "desktopcomputer")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Seguridad")
        .navigationBarTitleDisplayMode(.large)
        .overlay {
            if cargando && workers.isEmpty && aprobaciones.isEmpty && sesiones.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
        .alert("¿Pausar todos los agentes?", isPresented: $confirmarPausa) {
            Button("Pausar todos", role: .destructive) {
                Task { await pausar() }
            }
            Button("Cancelar", role: .cancel) {}
        } message: {
            Text("Frena de inmediato a todos los compañeros. Esta acción afecta a todo el equipo.")
        }
    }

    private var botonPausa: some View {
        Button {
            confirmarPausa = true
        } label: {
            Label("Pausar todos los agentes", systemImage: "pause.circle.fill")
                .font(.subheadline.weight(.semibold))
                .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .tint(.red)
        .disabled(ocupado)
        .listRowInsets(EdgeInsets())
        .listRowBackground(Color.clear)
        .padding(.vertical, 4)
    }

    private var filasCuentas: [CuentaConectada] {
        let c = credenciales
        return [
            CuentaConectada(titulo: "Modelo de lenguaje", icono: "brain", conectada: c?.llm != nil, detalle: c?.llm?.masked ?? ""),
            CuentaConectada(titulo: "Voz (escuchar)", icono: "waveform", conectada: c?.voiceStt != nil, detalle: c?.voiceStt?.masked ?? ""),
            CuentaConectada(titulo: "Voz (hablar)", icono: "speaker.wave.2.fill", conectada: c?.voiceTts != nil, detalle: c?.voiceTts?.masked ?? ""),
            CuentaConectada(titulo: "Imágenes", icono: "photo.on.rectangle", conectada: c?.images != nil, detalle: c?.images?.masked ?? ""),
            CuentaConectada(titulo: "Búsqueda", icono: "magnifyingglass", conectada: c?.search != nil, detalle: c?.search?.masked ?? ""),
        ]
    }

    private func filaCuenta(_ cuenta: CuentaConectada) -> some View {
        HStack(spacing: 12) {
            Image(systemName: cuenta.icono)
                .font(.subheadline)
                .foregroundStyle(cuenta.conectada ? EdecanTheme.morado : .secondary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(cuenta.titulo)
                    .font(.subheadline.weight(.medium))
                if cuenta.conectada {
                    Text(cuenta.detalle.isEmpty ? "Conectada" : cuenta.detalle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            Spacer()
            if cuenta.conectada {
                Label("Conectada", systemImage: "checkmark.circle.fill")
                    .font(.caption2.weight(.medium))
                    .foregroundStyle(.green)
                    .labelStyle(.titleAndIcon)
            } else {
                Text("Sin conectar")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func filaAutonomia(_ worker: PersistentWorker) -> some View {
        let nivel = AutonomiaNivel.desde(worker.autonomyLevel)
        return HStack(spacing: 12) {
            Circle()
                .fill(colorAutonomia(nivel))
                .frame(width: 9, height: 9)
            VStack(alignment: .leading, spacing: 2) {
                Text(worker.nombreVisible)
                    .font(.subheadline.weight(.medium))
                Text(worker.cargoVisible)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer()
            Text(nivel.titulo)
                .font(.caption.weight(.semibold))
                .foregroundStyle(colorAutonomia(nivel))
        }
        .padding(.vertical, 2)
    }

    private func colorAutonomia(_ nivel: AutonomiaNivel) -> Color {
        switch nivel {
        case .ask: return .orange
        case .readOnly: return EdecanTheme.azul
        case .draft: return EdecanTheme.morado
        case .full: return .green
        }
    }

    private func filaSesion(_ sesion: ComputerSession) -> some View {
        let color: Color = sesion.esTerminal
            ? .secondary
            : (sesion.mode == "paused" ? .orange : (sesion.mode == "user" ? .green : EdecanTheme.morado))
        return HStack(spacing: 12) {
            Circle().fill(color).frame(width: 9, height: 9)
            VStack(alignment: .leading, spacing: 2) {
                Text(sesion.etiquetaKind)
                    .font(.subheadline.weight(.medium))
                Text(sesion.esTerminal
                     ? "Terminada"
                     : (sesion.mode == "paused" ? "Agente pausado" : (sesion.mode == "user" ? "Tienes el control" : "Agente al mando")))
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = workers.isEmpty && aprobaciones.isEmpty && sesiones.isEmpty
        error = nil
        avisoPausa = nil
        defer { cargando = false }
        workers = (try? await client.listWorkers()) ?? []
        aprobaciones = (try? await client.listApprovals()) ?? []
        sesiones = (try? await client.listComputerSessions()) ?? []
        credenciales = try? await client.credenciales()
    }

    private func decidirAprobacion(_ aprobacion: PendingApproval, aprobar: Bool) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            if aprobar {
                try await client.approveApproval(id: aprobacion.id)
            } else {
                try await client.denyApproval(id: aprobacion.id)
            }
            await cargar()
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func pausar() async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        error = nil
        avisoPausa = nil
        do {
            try await client.pauseAllWorkers()
            await cargar()
            avisoPausa = "Todos los agentes quedaron en pausa."
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "El freno de emergencia está llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct CuentaConectada: Identifiable {
    let titulo: String
    let icono: String
    let conectada: Bool
    let detalle: String

    var id: String { titulo }
}