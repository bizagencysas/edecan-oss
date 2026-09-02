import SwiftUI
import EdecanKit

/// Secciones del estudio nativo.
enum IDEStudioSection: String, CaseIterable, Identifiable {
    case editor = "Editor"
    case terminal = "Terminal"
    case agente = "Agente"
    case git = "Git"

    var id: String { rawValue }

    var icono: String {
        switch self {
        case .editor: "chevron.left.forwardslash.chevron.right"
        case .terminal: "terminal"
        case .agente: "sparkles"
        case .git: "arrow.triangle.branch"
        }
    }
}

/// Estudio nativo conectado al Edecán de escritorio — light-only, estilo Cursor/Linear.
struct IDEView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    @State private var viewModel = IDEViewModel()
    @State private var seccion: IDEStudioSection
    @State private var mostrandoNuevoWorkspace = false
    @State private var rutaWorkspace = ""
    @State private var nombreWorkspace = ""

    init(initialSection: IDEStudioSection = .editor) {
        _seccion = State(initialValue: initialSection)
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.cargando && viewModel.workspaces.isEmpty {
                    ProgressView("Abriendo tu estudio…")
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if !viewModel.conectado {
                    sinComputadora
                } else if viewModel.workspaceActivo == nil {
                    sinWorkspace
                } else {
                    estudio
                }
            }
            .background(fondoEstudio.ignoresSafeArea())
            .navigationTitle("IDE")
            .navigationBarTitleDisplayMode(.inline)
            .toolbarBackground(.ultraThinMaterial, for: .navigationBar)
            .toolbarBackground(.visible, for: .navigationBar)
            .ideLightOnly()
            .task {
                await viewModel.cargar(client: session.client)
                viewModel.iniciarPolling(client: session.client)
            }
            .onChange(of: scenePhase) { _, fase in
                if fase == .active {
                    Task { await viewModel.reanudar(client: session.client) }
                } else {
                    viewModel.detenerPolling()
                }
            }
            .onDisappear { viewModel.detenerPolling() }
            .refreshable { await viewModel.reanudar(client: session.client) }
            .sheet(isPresented: $mostrandoNuevoWorkspace) {
                nuevoWorkspace
                    .presentationDetents([.medium, .large])
            }
        }
        .ideLightOnly()
    }

    private var fondoEstudio: some View {
        ZStack {
            IDETheme.fondo
            EdecanTheme.degradado
                .opacity(0.04)
                .blur(radius: 60)
                .offset(y: -120)
        }
    }

    private var estudio: some View {
        VStack(spacing: 0) {
            cabecera
                .padding(.horizontal, 14)
                .padding(.vertical, 10)

            if let error = viewModel.errorMensaje {
                bannerError(error)
                    .padding(.horizontal)
                    .padding(.bottom, 8)
            }

            barraSecciones
                .padding(.horizontal, 14)
                .padding(.bottom, 8)

            Group {
                switch seccion {
                case .editor:
                    IDEArchivosView(viewModel: viewModel)
                case .terminal:
                    IDETerminalView(viewModel: viewModel)
                case .agente:
                    IDEAgenteView(viewModel: viewModel)
                case .git:
                    IDEGitView(viewModel: viewModel)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: Cabecera

    private var cabecera: some View {
        HStack(spacing: 12) {
            Menu {
                ForEach(viewModel.workspaces) { workspace in
                    Button {
                        Task {
                            await viewModel.seleccionarWorkspace(
                                id: workspace.id,
                                client: session.client
                            )
                        }
                    } label: {
                        if workspace.id == viewModel.workspaceActivo?.id {
                            Label(workspace.name, systemImage: "checkmark")
                        } else {
                            Text(workspace.name)
                        }
                    }
                }
                Divider()
                Button {
                    mostrandoNuevoWorkspace = true
                } label: {
                    Label("Autorizar otro proyecto", systemImage: "folder.badge.plus")
                }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "folder.fill")
                        .foregroundStyle(EdecanTheme.degradado)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(viewModel.workspaceActivo?.name ?? "Proyecto")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(IDETheme.texto)
                            .lineLimit(1)
                        Text(tituloCabecera)
                            .font(.caption2)
                            .foregroundStyle(IDETheme.textoSuave)
                            .lineLimit(1)
                    }
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption2.bold())
                        .foregroundStyle(IDETheme.textoSuave)
                }
            }
            .buttonStyle(.plain)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .idePanel(esquina: 14)

            estadoConexion
        }
    }

    private var tituloCabecera: String {
        if let ruta = viewModel.rutaAbierta {
            return (ruta as NSString).lastPathComponent
        }
        return viewModel.workspaceActivo?.path ?? ""
    }

    private var estadoConexion: some View {
        HStack(spacing: 7) {
            if viewModel.reconectando || viewModel.cambiandoWorkspace {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityLabel("Reconectando")
            } else {
                Circle()
                    .fill(viewModel.conectado ? IDETheme.verde : IDETheme.naranja)
                    .frame(width: 9, height: 9)
                    .accessibilityLabel(viewModel.conectado ? "Conectado" : "Desconectado")
            }
        }
        .padding(10)
        .background(IDETheme.superficie, in: Circle())
        .overlay(Circle().strokeBorder(IDETheme.superficieBorde, lineWidth: 1))
    }

    private func bannerError(_ error: String) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(error)
                .font(.footnote)
                .frame(maxWidth: .infinity, alignment: .leading)
            Button {
                viewModel.descartarError()
            } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.plain)
        }
        .foregroundStyle(IDETheme.rojo)
        .padding(12)
        .background(IDETheme.rojo.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
        .overlay(
            RoundedRectangle(cornerRadius: 14)
                .strokeBorder(IDETheme.rojo.opacity(0.15), lineWidth: 1)
        )
    }

    // MARK: Secciones

    private var barraSecciones: some View {
        HStack(spacing: 4) {
            ForEach(IDEStudioSection.allCases) { item in
                Button {
                    if reduceMotion {
                        seccion = item
                    } else {
                        withAnimation(.easeOut(duration: 0.16)) {
                            seccion = item
                        }
                    }
                } label: {
                    VStack(spacing: 3) {
                        Image(systemName: item.icono)
                            .font(.system(size: 14, weight: .medium))
                        Text(item.rawValue)
                            .font(.caption2.weight(.semibold))
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 7)
                    .background(
                        seccion == item ? IDETheme.segSeleccion : Color.clear,
                        in: RoundedRectangle(cornerRadius: 11, style: .continuous)
                    )
                    .foregroundStyle(seccion == item ? IDETheme.acento : IDETheme.textoSuave)
                    .shadow(
                        color: seccion == item ? IDETheme.sombraSuave : .clear,
                        radius: 4,
                        y: 1
                    )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Sección \(item.rawValue)")
                .accessibilityAddTraits(seccion == item ? .isSelected : [])
            }
        }
        .padding(4)
        .background(IDETheme.segTrack, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(IDETheme.superficieBorde, lineWidth: 1)
        )
    }

    // MARK: Estados

    private var sinComputadora: some View {
        VStack(spacing: 16) {
            EmptyStateView(
                icono: "desktopcomputer.trianglebadge.exclamationmark",
                titulo: "Computadora no disponible",
                descripcion: "Abre Edecán en tu computadora. Tus tareas siguen allí y reaparecerán al reconectar.",
                etiquetaRoadmap: nil
            )
            Button("Ir a Ajustes") { tabRouter.seleccion = .settings }
                .buttonStyle(.bordered)
                .tint(IDETheme.acento)
        }
    }

    private var sinWorkspace: some View {
        VStack(spacing: 18) {
            Image(systemName: "folder.badge.plus")
                .font(.system(size: 54))
                .foregroundStyle(EdecanTheme.degradado)
            Text("Elige un proyecto")
                .font(.title2.bold())
                .foregroundStyle(IDETheme.texto)
            Text("Autoriza una carpeta de tu computadora. Edecán solo podrá trabajar dentro de ese proyecto.")
                .multilineTextAlignment(.center)
                .foregroundStyle(IDETheme.textoSuave)
            Button("Autorizar proyecto") {
                mostrandoNuevoWorkspace = true
            }
            .buttonStyle(.borderedProminent)
            .tint(IDETheme.acento)
        }
        .padding(28)
    }

    private var nuevoWorkspace: some View {
        NavigationStack {
            Form {
                Section("Proyecto en tu computadora") {
                    TextField("/ruta/completa/al/proyecto", text: $rutaWorkspace)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Nombre opcional", text: $nombreWorkspace)
                }
                Section {
                    Text("La ruta se valida en tu computadora. Edecán no permite autorizar la raíz, tu carpeta personal completa ni carpetas de credenciales.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Autorizar proyecto")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancelar") { mostrandoNuevoWorkspace = false }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Autorizar") {
                        Task {
                            let creado = await viewModel.crearWorkspace(
                                path: rutaWorkspace,
                                name: nombreWorkspace,
                                client: session.client
                            )
                            if creado {
                                rutaWorkspace = ""
                                nombreWorkspace = ""
                                mostrandoNuevoWorkspace = false
                                viewModel.iniciarPolling(client: session.client)
                            }
                        }
                    }
                    .disabled(
                        viewModel.cambiandoWorkspace ||
                        rutaWorkspace.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
            }
        }
    }
}
