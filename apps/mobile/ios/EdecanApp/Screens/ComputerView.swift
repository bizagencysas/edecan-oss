import SwiftUI
import EdecanKit

/// "Computadora" — plano de control de toma de control por superficie
/// (`GET /v1/computer/sessions` + takeover/return/pause/resume/end,
/// `routers/computer.py`). Administra QUIÉN mueve cada superficie AHORA: el
/// agente o tú. Sin WebRTC: la vista por polling ya vive en ``RemotoView``;
/// esto solo cambia `mode`/`status` y muestra el estado semántico.
struct ComputerView: View {
    @Environment(SessionStore.self) private var session
    @State private var sesiones: [ComputerSession] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false
    @State private var ocupado = false
    @State private var creando = false

    var body: some View {
        List {
            if let error {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.red)
                    .listRowSeparator(.hidden)
            }
            if proximamente {
                filaProximamente
            }

            if sesiones.isEmpty && !cargando && !proximamente {
                Text("Ninguna sesión de computadora todavía. Crea una para que un agente trabaje en una superficie.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }

            ForEach(sesiones) { sesion in
                TarjetaSesion(sesion: sesion, ocupado: ocupado) { accion in
                    Task { await ejecutar(accion, sobre: sesion) }
                }
                .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Computadora")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    creando = true
                } label: {
                    Label("Nueva sesión", systemImage: "plus")
                }
                .disabled(ocupado)
            }
        }
        .overlay {
            if cargando && sesiones.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
        .sheet(isPresented: $creando) {
            NavigationStack { NuevaSesionSheet { kind in
                Task { await crear(kind: kind) }
            } }
        }
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("El plano de control de la computadora está llegando al servidor.")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .listRowSeparator(.hidden)
    }

    private func cargar() async {
        guard let client = session.client else {
            error = "No hay sesión activa."
            cargando = false
            return
        }
        cargando = sesiones.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            sesiones = try await client.listComputerSessions()
        } catch let apiError as APIClient.APIError {
            if apiError.esProximamente {
                proximamente = true
            } else {
                self.error = apiError.localizedDescription
            }
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func crear(kind: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            _ = try await client.createComputerSession(kind: kind)
            creando = false
            await cargar()
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "El plano de control de la computadora está llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }

    enum AccionSesion: String {
        case takeover, returnControl, pause, resume, end
    }

    private func ejecutar(_ accion: AccionSesion, sobre sesion: ComputerSession) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            let actualizada: ComputerSession
            switch accion {
            case .takeover: actualizada = try await client.computerTakeover(id: sesion.id)
            case .returnControl: actualizada = try await client.computerReturn(id: sesion.id)
            case .pause: actualizada = try await client.computerPause(id: sesion.id)
            case .resume: actualizada = try await client.computerResume(id: sesion.id)
            case .end: actualizada = try await client.computerEnd(id: sesion.id)
            }
            if let indice = sesiones.firstIndex(where: { $0.id == actualizada.id }) {
                sesiones[indice] = actualizada
            }
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "El plano de control de la computadora está llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Estado semántico de una sesión de computadora: quién tiene el control AHORA.
private enum EstadoComputadora {
    case tuControl
    case agentePausado
    case agenteAlMando
    case terminada

    init(de sesion: ComputerSession) {
        if sesion.esTerminal { self = .terminada; return }
        switch sesion.mode {
        case "user": self = .tuControl
        case "paused": self = .agentePausado
        default: self = .agenteAlMando
        }
    }

    var titulo: String {
        switch self {
        case .tuControl: return "Tienes el control"
        case .agentePausado: return "Agente pausado"
        case .agenteAlMando: return "Agente al mando"
        case .terminada: return "Sesión terminada"
        }
    }

    var color: Color {
        switch self {
        case .tuControl: return .green
        case .agentePausado: return .orange
        case .agenteAlMando: return EdecanTheme.morado
        case .terminada: return .secondary
        }
    }

    var icono: String {
        switch self {
        case .tuControl: return "hand.raised.fill"
        case .agentePausado: return "pause.circle.fill"
        case .agenteAlMando: return "bolt.fill"
        case .terminada: return "checkmark.circle.fill"
        }
    }
}

private struct TarjetaSesion: View {
    let sesion: ComputerSession
    let ocupado: Bool
    let onAccion: (ComputerView.AccionSesion) -> Void

    private var estado: EstadoComputadora { EstadoComputadora(de: sesion) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: estado.icono)
                    .foregroundStyle(estado.color)
                VStack(alignment: .leading, spacing: 2) {
                    Text("\(sesion.etiquetaKind) · \(estado.titulo)")
                        .font(.subheadline.weight(.semibold))
                    if let agente = sesion.agentId {
                        Text("Agente \(agente.prefix(8))")
                            .font(.caption2.monospaced())
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer(minLength: 8)
                Circle()
                    .fill(estado.color)
                    .frame(width: 9, height: 9)
            }

            if !sesion.esTerminal {
                HStack(spacing: 8) {
                    if sesion.mode == "user" {
                        boton("Devolver", icono: "arrow.uturn.backward") { onAccion(.returnControl) }
                    } else {
                        boton("Tomar control", icono: "hand.raised") { onAccion(.takeover) }
                    }
                    if sesion.mode == "paused" {
                        boton("Reanudar", icono: "play.fill") { onAccion(.resume) }
                    } else {
                        boton("Pausar", icono: "pause.fill") { onAccion(.pause) }
                    }
                    Button(role: .destructive) {
                        onAccion(.end)
                    } label: {
                        Label("Terminar", systemImage: "xmark")
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.bordered)
                    .tint(.red)
                }
                .disabled(ocupado)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(estado.color.opacity(0.08), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
    }

    private func boton(_ titulo: String, icono: String, accion: @escaping () -> Void) -> some View {
        Button(action: accion) {
            Label(titulo, systemImage: icono)
                .font(.caption.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .tint(EdecanTheme.morado)
    }
}

private struct NuevaSesionSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onCrear: (String) -> Void
    @State private var kind = "desktop"

    private let opciones: [(String, String)] = [
        ("desktop", "Escritorio"),
        ("browser", "Navegador"),
        ("terminal", "Terminal"),
        ("files", "Archivos"),
    ]

    var body: some View {
        Form {
            Section("Superficie") {
                Picker("Superficie", selection: $kind) {
                    ForEach(opciones, id: \.0) { valor, etiqueta in
                        Text(etiqueta).tag(valor)
                    }
                }
                .pickerStyle(.inline)
                .labelsHidden()
            }
        }
        .navigationTitle("Nueva sesión")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Crear") {
                    onCrear(kind)
                    dismiss()
                }
            }
        }
    }
}