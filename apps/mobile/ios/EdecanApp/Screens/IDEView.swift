import SwiftUI
import EdecanKit

private struct NodoIDE: Identifiable {
    let entry: IDEEntry
    let ruta: String

    var id: String { ruta }

    var children: [NodoIDE]? {
        entry.children?.map {
            NodoIDE(entry: $0, ruta: ruta.isEmpty ? $0.name : "\(ruta)/\($0.name)")
        }
    }
}

/// Estudio nativo conectado al Edecán de escritorio.
///
/// Los procesos de Terminal y Agente viven en la computadora. El teléfono
/// conserva únicamente IDs opacos y cursores, por lo que puede salir de la
/// app, volver y continuar viendo la misma ejecución sin duplicarla.
struct IDEView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @Environment(\.scenePhase) private var scenePhase

    @State private var viewModel = IDEViewModel()
    @State private var pestaña: Pestaña = .archivos
    @State private var mostrandoNuevoWorkspace = false
    @State private var rutaWorkspace = ""
    @State private var nombreWorkspace = ""
    @State private var mostrandoModelo = false
    @State private var confirmarPush = false

    private enum Pestaña: String, CaseIterable, Identifiable {
        case archivos = "Archivos"
        case agente = "Agente"
        case terminal = "Terminal"
        case git = "Git"

        var id: String { rawValue }

        var icono: String {
            switch self {
            case .archivos: "folder"
            case .agente: "sparkles"
            case .terminal: "terminal"
            case .git: "arrow.triangle.branch"
            }
        }
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
            .background(EdecanTheme.degradado.opacity(0.07).ignoresSafeArea())
            .navigationTitle("Estudio")
            .navigationBarTitleDisplayMode(.inline)
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
            .navigationDestination(item: Binding(
                get: { viewModel.rutaAbierta },
                set: { if $0 == nil { viewModel.cerrarArchivo() } }
            )) { ruta in
                editor(ruta: ruta)
            }
            .confirmationDialog(
                "¿Enviar esta rama al repositorio remoto?",
                isPresented: $confirmarPush,
                titleVisibility: .visible
            ) {
                Button("Enviar cambios") {
                    Task { await viewModel.push(client: session.client) }
                }
                Button("Cancelar", role: .cancel) {}
            } message: {
                Text("Edecán ejecutará Git push desde \(viewModel.workspaceActivo?.name ?? "este proyecto").")
            }
        }
    }

    private var estudio: some View {
        VStack(spacing: 10) {
            selectorWorkspace

            if let error = viewModel.errorMensaje {
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
                .foregroundStyle(.red)
                .padding(12)
                .background(.red.opacity(0.09), in: RoundedRectangle(cornerRadius: 14))
                .padding(.horizontal)
            }

            Picker("Herramienta", selection: $pestaña) {
                ForEach(Pestaña.allCases) { item in
                    Label(item.rawValue, systemImage: item.icono).tag(item)
                }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)

            Group {
                switch pestaña {
                case .archivos: archivos
                case .agente: agente
                case .terminal: terminal
                case .git: git
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
    }

    private var selectorWorkspace: some View {
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
                        .foregroundStyle(EdecanTheme.morado)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(viewModel.workspaceActivo?.name ?? "Proyecto")
                            .font(.subheadline.bold())
                            .foregroundStyle(.primary)
                        Text(viewModel.workspaceActivo?.path ?? "")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                            .lineLimit(1)
                    }
                    Spacer()
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption.bold())
                        .foregroundStyle(.secondary)
                }
            }
            .buttonStyle(.plain)

            if viewModel.reconectando || viewModel.cambiandoWorkspace {
                ProgressView()
                    .controlSize(.small)
                    .accessibilityLabel("Reconectando")
            } else {
                Circle()
                    .fill(.green)
                    .frame(width: 9, height: 9)
                    .accessibilityLabel("Conectado")
            }
        }
        .padding(12)
        .tarjetaVidrio(esquina: 17)
        .padding(.horizontal)
    }

    // MARK: Archivos

    private var archivos: some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                Button {
                    let componentes = viewModel.rutaActual.split(separator: "/")
                    viewModel.rutaActual = componentes.dropLast().joined(separator: "/")
                    Task { await viewModel.abrirRuta(client: session.client) }
                } label: {
                    Image(systemName: "chevron.left")
                }
                .disabled(viewModel.rutaActual.isEmpty)

                TextField("Ruta dentro del proyecto", text: $viewModel.rutaActual)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.go)
                    .onSubmit {
                        Task { await viewModel.abrirRuta(client: session.client) }
                    }

                Button {
                    Task { await viewModel.refrescarArbol(client: session.client) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                }
            }
            .padding(.horizontal)

            List {
                if viewModel.truncado {
                    Label(
                        "Hay más archivos. Abre una carpeta o escribe su ruta para continuar.",
                        systemImage: "info.circle"
                    )
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }

                if viewModel.arbol.isEmpty {
                    ContentUnavailableView(
                        "Carpeta vacía",
                        systemImage: "folder",
                        description: Text("No hay archivos visibles en esta ruta.")
                    )
                    .listRowBackground(Color.clear)
                } else {
                    OutlineGroup(nodosRaiz, children: \.children) { nodo in
                        fila(nodo)
                    }
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
        }
    }

    private var nodosRaiz: [NodoIDE] {
        viewModel.arbol.map {
            NodoIDE(
                entry: $0,
                ruta: viewModel.rutaActual.isEmpty
                    ? $0.name
                    : "\(viewModel.rutaActual)/\($0.name)"
            )
        }
    }

    @ViewBuilder
    private func fila(_ nodo: NodoIDE) -> some View {
        if nodo.entry.isDir {
            if nodo.entry.children == nil {
                Button {
                    viewModel.rutaActual = nodo.ruta
                    Task { await viewModel.abrirRuta(client: session.client) }
                } label: {
                    Label(nodo.entry.name, systemImage: "folder.fill")
                }
                .buttonStyle(.plain)
            } else {
                Label(nodo.entry.name, systemImage: "folder.fill")
            }
        } else {
            Button {
                Task { await viewModel.abrir(ruta: nodo.ruta, client: session.client) }
            } label: {
                HStack {
                    Label(nodo.entry.name, systemImage: iconoArchivo(nodo.entry.name))
                    Spacer()
                    if let bytes = nodo.entry.sizeBytes {
                        Text(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
            .buttonStyle(.plain)
        }
    }

    // MARK: Agente

    private var agente: some View {
        ScrollView {
            VStack(spacing: 14) {
                cabeceraAgente
                timelineAgente

                VStack(alignment: .leading, spacing: 10) {
                    Text("¿Qué quieres construir?")
                        .font(.headline)

                    TextEditor(text: $viewModel.promptAgente)
                        .frame(minHeight: 105)
                        .padding(8)
                        .scrollContentBackground(.hidden)
                        .background(
                            Color.secondary.opacity(0.08),
                            in: RoundedRectangle(cornerRadius: 13)
                        )

                    Picker("Agente", selection: $viewModel.proveedorAgente) {
                        ForEach(IDEAgentProvider.allCases) { proveedor in
                            Text(proveedor.label).tag(proveedor)
                        }
                    }
                    .pickerStyle(.segmented)

                    DisclosureGroup("Elegir modelo manualmente", isExpanded: $mostrandoModelo) {
                        TextField("Modelo opcional", text: $viewModel.modeloAgente)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .textFieldStyle(.roundedBorder)
                            .padding(.top, 8)
                    }
                    .font(.subheadline)

                    Button {
                        Task { await viewModel.crearAgente(client: session.client) }
                    } label: {
                        HStack {
                            if viewModel.creandoAgente {
                                ProgressView().tint(.white)
                            } else {
                                Image(systemName: "sparkles")
                            }
                            Text(viewModel.creandoAgente ? "Preparando…" : "Trabajar en este proyecto")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .disabled(
                        viewModel.creandoAgente ||
                        viewModel.promptAgente.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
                .padding(14)
                .tarjetaVidrio(esquina: 18)
            }
            .padding(.horizontal)
            .padding(.bottom, 20)
        }
    }

    private var cabeceraAgente: some View {
        HStack(spacing: 10) {
            Menu {
                ForEach(viewModel.agentes) { agente in
                    Button {
                        Task {
                            await viewModel.seleccionarAgente(
                                id: agente.id,
                                client: session.client
                            )
                        }
                    } label: {
                        Text(agente.title ?? "Agente \(agente.id.prefix(6))")
                    }
                }
            } label: {
                VStack(alignment: .leading, spacing: 3) {
                    Text(viewModel.agenteActivo?.title ?? "Agente del proyecto")
                        .font(.subheadline.bold())
                        .lineLimit(1)
                    Text(descripcionEstado(viewModel.agenteActivo))
                        .font(.caption)
                        .foregroundStyle(colorEstado(viewModel.agenteActivo))
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            .disabled(viewModel.agentes.isEmpty)

            if viewModel.agenteActivo?.isActive == true {
                Button("Detener", role: .destructive) {
                    Task { await viewModel.cerrarAgente(client: session.client) }
                }
                .font(.caption.bold())
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private var timelineAgente: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Progreso en vivo", systemImage: "waveform.path.ecg")
                    .font(.headline)
                Spacer()
                if viewModel.agenteActivo?.isActive == true {
                    ProgressView().controlSize(.small)
                }
            }

            if viewModel.eventosAgente.isEmpty {
                Text("Cuando Edecán empiece, verás aquí cada avance aunque cambies de app.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, minHeight: 92, alignment: .center)
                    .multilineTextAlignment(.center)
            } else {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(viewModel.eventosAgente) { evento in
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: iconoEvento(evento))
                                .foregroundStyle(colorEvento(evento))
                                .frame(width: 20)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(etiquetaEvento(evento))
                                    .font(.caption.bold())
                                    .foregroundStyle(.secondary)
                                Text(evento.text)
                                    .font(.subheadline)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                }
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: Terminal

    private var terminal: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Menu {
                    ForEach(viewModel.terminales) { terminal in
                        Button {
                            Task {
                                await viewModel.seleccionarTerminal(
                                    id: terminal.id,
                                    client: session.client
                                )
                            }
                        } label: {
                            Text(terminal.title ?? "Terminal \(terminal.id.prefix(6))")
                        }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(viewModel.terminalActivo?.title ?? "Terminal")
                            .font(.subheadline.bold())
                        Text(descripcionEstado(viewModel.terminalActivo))
                            .font(.caption2)
                            .foregroundStyle(colorEstado(viewModel.terminalActivo))
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .buttonStyle(.plain)
                .disabled(viewModel.terminales.isEmpty)

                Button {
                    Task { await viewModel.crearTerminal(client: session.client) }
                } label: {
                    if viewModel.creandoTerminal {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "plus")
                    }
                }
                .accessibilityLabel("Nueva terminal")

                Button(role: .destructive) {
                    Task { await viewModel.cerrarTerminal(client: session.client) }
                } label: {
                    Image(systemName: "trash")
                }
                .disabled(viewModel.terminalActivo == nil)
                .accessibilityLabel("Cerrar terminal")
            }
            .padding(12)
            .background(Color(red: 0.08, green: 0.09, blue: 0.13))

            ScrollViewReader { proxy in
                ScrollView {
                    Text(
                        viewModel.salidaTerminal.isEmpty
                            ? "Abre una terminal para trabajar en este proyecto."
                            : viewModel.salidaTerminal
                    )
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(Color(red: 0.61, green: 0.95, blue: 0.76))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .topLeading)
                    .padding()

                    Color.clear.frame(height: 1).id("terminal-final")
                }
                .onChange(of: viewModel.salidaTerminal) { _, _ in
                    withAnimation(.easeOut(duration: 0.18)) {
                        proxy.scrollTo("terminal-final", anchor: .bottom)
                    }
                }
            }
            .background(Color(red: 0.035, green: 0.04, blue: 0.065))

            HStack(spacing: 10) {
                Button("⌃C") {
                    Task { await viewModel.interrumpirTerminal(client: session.client) }
                }
                .font(.system(.caption, design: .monospaced).bold())
                .disabled(viewModel.terminalActivo?.isActive != true)

                TextField("Escribe un comando", text: $viewModel.entradaTerminal)
                    .font(.system(.body, design: .monospaced))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.send)
                    .onSubmit {
                        Task { await viewModel.enviarTerminal(client: session.client) }
                    }

                Button {
                    Task { await viewModel.enviarTerminal(client: session.client) }
                } label: {
                    Image(systemName: "arrow.up.circle.fill")
                        .font(.title2)
                }
                .disabled(viewModel.entradaTerminal.isEmpty)
            }
            .padding(12)
            .background(Color(red: 0.08, green: 0.09, blue: 0.13))
        }
        .foregroundStyle(.white)
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .padding(.horizontal)
        .padding(.bottom, 8)
    }

    // MARK: Git

    private var git: some View {
        ScrollView {
            VStack(spacing: 14) {
                if viewModel.cargandoGit {
                    ProgressView("Leyendo Git…")
                        .frame(maxWidth: .infinity, minHeight: 160)
                } else if let status = viewModel.gitStatus {
                    estadoGit(status)
                    cambiosGit(status)
                    commitGit
                    ramasGit
                    diffGit
                    historialGit
                } else {
                    ContentUnavailableView(
                        "Git no está disponible",
                        systemImage: "arrow.triangle.branch",
                        description: Text("Este proyecto no parece ser un repositorio Git.")
                    )
                    .frame(minHeight: 260)
                }
            }
            .padding(.horizontal)
            .padding(.bottom, 20)
        }
    }

    private func estadoGit(_ status: IDEGitStatus) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "arrow.triangle.branch")
                .font(.title2)
                .foregroundStyle(EdecanTheme.morado)
            VStack(alignment: .leading, spacing: 3) {
                Text(status.branch ?? "HEAD separado")
                    .font(.headline)
                HStack(spacing: 8) {
                    if let upstream = status.upstream {
                        Text(upstream)
                    }
                    if status.ahead > 0 { Text("↑ \(status.ahead)") }
                    if status.behind > 0 { Text("↓ \(status.behind)") }
                }
                .font(.caption)
                .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                Task { await viewModel.refrescarGit(client: session.client) }
            } label: {
                Image(systemName: "arrow.clockwise")
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private func cambiosGit(_ status: IDEGitStatus) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Cambios")
                    .font(.headline)
                Spacer()
                if !status.files.isEmpty {
                    Menu {
                        Button("Preparar todo") {
                            Task {
                                await viewModel.stage(
                                    paths: status.files.map(\.path),
                                    client: session.client
                                )
                            }
                        }
                        let preparados = status.files.filter(\.isStaged).map(\.path)
                        if !preparados.isEmpty {
                            Button("Quitar todo del commit") {
                                Task {
                                    await viewModel.unstage(
                                        paths: preparados,
                                        client: session.client
                                    )
                                }
                            }
                        }
                    } label: {
                        Image(systemName: "ellipsis.circle")
                    }
                }
            }

            if status.files.isEmpty {
                Label("Todo está al día", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
                    .font(.subheadline)
            } else {
                ForEach(status.files) { archivo in
                    HStack(spacing: 10) {
                        Text("\(archivo.indexStatus)\(archivo.worktreeStatus)")
                            .font(.system(.caption, design: .monospaced).bold())
                            .foregroundStyle(archivo.isStaged ? .green : .orange)
                            .frame(width: 24)
                        Text(archivo.path)
                            .font(.subheadline)
                            .lineLimit(2)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button(archivo.isStaged ? "Quitar" : "Preparar") {
                            Task {
                                if archivo.isStaged {
                                    await viewModel.unstage(
                                        paths: [archivo.path],
                                        client: session.client
                                    )
                                } else {
                                    await viewModel.stage(
                                        paths: [archivo.path],
                                        client: session.client
                                    )
                                }
                            }
                        }
                        .font(.caption.bold())
                    }
                    .padding(.vertical, 4)
                }
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private var commitGit: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Guardar versión")
                .font(.headline)
            TextField("Describe el cambio", text: $viewModel.mensajeCommit, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .lineLimit(2...4)
            Button {
                Task { await viewModel.commit(client: session.client) }
            } label: {
                Label("Crear commit", systemImage: "checkmark.seal")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
            .disabled(
                viewModel.accionGitEnCurso ||
                viewModel.mensajeCommit.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            )
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private var ramasGit: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Ramas y remoto")
                .font(.headline)
            HStack {
                TextField("Nueva rama", text: $viewModel.nuevaRama)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                Button("Crear") {
                    Task { await viewModel.crearRama(client: session.client) }
                }
                .disabled(viewModel.nuevaRama.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Button {
                confirmarPush = true
            } label: {
                Label("Enviar al remoto", systemImage: "arrow.up.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .disabled(viewModel.accionGitEnCurso)
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private var diffGit: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Diferencias")
                .font(.headline)
            if let diff = viewModel.gitDiff, !diff.text.isEmpty {
                ScrollView(.horizontal) {
                    Text(diff.text)
                        .font(.system(.caption2, design: .monospaced))
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if diff.truncated {
                    Text("Vista recortada por tamaño.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("No hay diferencias sin preparar.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    private var historialGit: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Historial")
                .font(.headline)
            if viewModel.gitLog.isEmpty {
                Text("Este proyecto todavía no tiene commits.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            } else {
                ForEach(viewModel.gitLog.prefix(20)) { commit in
                    HStack(alignment: .top, spacing: 10) {
                        Text(commit.shortHash)
                            .font(.system(.caption, design: .monospaced).bold())
                            .foregroundStyle(EdecanTheme.morado)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(commit.subject)
                                .font(.subheadline)
                            Text(commit.author)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: Estados y editor

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
        }
    }

    private var sinWorkspace: some View {
        VStack(spacing: 18) {
            Image(systemName: "folder.badge.plus")
                .font(.system(size: 54))
                .foregroundStyle(EdecanTheme.morado)
            Text("Elige un proyecto")
                .font(.title2.bold())
            Text("Autoriza una carpeta de tu computadora. Edecán solo podrá trabajar dentro de ese proyecto.")
                .multilineTextAlignment(.center)
                .foregroundStyle(.secondary)
            Button("Autorizar proyecto") {
                mostrandoNuevoWorkspace = true
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
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

    @ViewBuilder
    private func editor(ruta: String) -> some View {
        Group {
            if viewModel.cargandoArchivo {
                ProgressView("Abriendo archivo…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if let archivo = viewModel.archivoAbierto {
                if archivo.encoding == "utf-8" {
                    TextEditor(text: $viewModel.contenidoEditable)
                        .font(.system(.footnote, design: .monospaced))
                        .padding(8)
                        .scrollContentBackground(.hidden)
                        .background(Color(uiColor: .secondarySystemBackground))
                } else {
                    EmptyStateView(
                        icono: "doc.questionmark",
                        titulo: "Archivo binario",
                        descripcion: "Este archivo no es texto y todavía no puede editarse aquí.",
                        etiquetaRoadmap: nil
                    )
                }
            } else {
                EmptyStateView(
                    icono: "exclamationmark.triangle",
                    titulo: "No se pudo abrir",
                    descripcion: viewModel.errorMensaje ?? "Inténtalo de nuevo.",
                    etiquetaRoadmap: nil
                )
            }
        }
        .navigationTitle((ruta as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await viewModel.guardar(client: session.client) }
                } label: {
                    if viewModel.guardandoArchivo {
                        ProgressView()
                    } else {
                        Label("Guardar", systemImage: "square.and.arrow.down")
                    }
                }
                .disabled(
                    viewModel.archivoAbierto?.encoding != "utf-8" ||
                    viewModel.contenidoEditable == viewModel.archivoAbierto?.content
                )
            }
        }
    }

    private func descripcionEstado(_ sesion: IDESession?) -> String {
        guard let sesion else { return "Sin sesión activa" }
        switch sesion.status {
        case "starting": return "Preparando"
        case "running": return "Trabajando en vivo"
        case "completed": return "Terminado"
        case "failed": return "Falló"
        case "closed": return "Cerrada"
        case "cancelled": return "Cancelado"
        case "interrupted": return "Interrumpido al reiniciar la computadora"
        default: return sesion.status.capitalized
        }
    }

    private func colorEstado(_ sesion: IDESession?) -> Color {
        guard let sesion else { return .secondary }
        if sesion.isActive { return .green }
        return sesion.status == "completed" ? .secondary : .orange
    }

    private func etiquetaEvento(_ evento: IDESessionEvent) -> String {
        if evento.stream == "stderr" { return "Aviso" }
        if evento.type == "status" { return "Estado" }
        if evento.type == "exit" { return "Finalizado" }
        return "Edecán"
    }

    private func iconoEvento(_ evento: IDESessionEvent) -> String {
        if evento.stream == "stderr" || evento.type == "error" {
            return "exclamationmark.triangle.fill"
        }
        if evento.type == "exit" { return "checkmark.circle.fill" }
        if evento.type == "status" { return "bolt.fill" }
        return "sparkles"
    }

    private func colorEvento(_ evento: IDESessionEvent) -> Color {
        if evento.stream == "stderr" || evento.type == "error" { return .orange }
        if evento.type == "exit" { return .green }
        return EdecanTheme.morado
    }

    private func iconoArchivo(_ nombre: String) -> String {
        switch (nombre as NSString).pathExtension.lowercased() {
        case "swift", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "kt":
            return "chevron.left.forwardslash.chevron.right"
        case "md", "txt":
            return "doc.text"
        case "json", "yml", "yaml", "toml":
            return "gearshape"
        case "png", "jpg", "jpeg", "gif", "svg":
            return "photo"
        default:
            return "doc"
        }
    }
}
