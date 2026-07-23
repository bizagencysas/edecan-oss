import SwiftUI
import EdecanKit

/// Un nodo del árbol con su ruta completa ya resuelta (`IDEEntry` solo trae
/// el nombre propio, no la ruta desde la raíz del sandbox) — necesaria para
/// pedir `GET /v1/ide/file?path=` al tocar una hoja.
private struct NodoIDE: Identifiable {
    let entry: IDEEntry
    let ruta: String

    var id: String { ruta }

    var children: [NodoIDE]? {
        entry.children?.map { NodoIDE(entry: $0, ruta: "\(ruta)/\($0.name)") }
    }
}

/// Pestaña "IDE" — árbol de solo lectura del companion de escritorio
/// emparejado (`ARCHITECTURE.md` §11, `ROADMAP_V2.md` §7.6/§7.8, `docs/ide.md`).
/// Escribir/editar/correr comandos queda fuera del alcance de la app móvil
/// v1 (ver `docs/movil-ios.md`) — esta pantalla solo NAVEGA y MUESTRA.
struct IDEView: View {
    @Environment(SessionStore.self) private var session
    @Environment(TabRouter.self) private var tabRouter
    @State private var viewModel = IDEViewModel()
    @State private var pestaña: Pestaña = .archivos

    private enum Pestaña: String, CaseIterable {
        case archivos = "Archivos"
        case agente = "Agente"
        case terminal = "Terminal"
    }

    var body: some View {
        NavigationStack {
            Group {
                if viewModel.cargando && viewModel.arbol.isEmpty {
                    ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if !viewModel.conectado {
                    sinCompanion
                } else if let error = viewModel.errorMensaje, viewModel.arbol.isEmpty {
                    EmptyStateView(
                        icono: "exclamationmark.triangle.fill",
                        titulo: "No se pudo cargar el árbol",
                        descripcion: error,
                        etiquetaRoadmap: nil
                    )
                } else {
                    estudio
                }
            }
            .background(EdecanTheme.degradado.opacity(0.05).ignoresSafeArea())
            .navigationTitle("IDE")
            .task { await viewModel.cargar(client: session.client) }
            .refreshable { await viewModel.cargar(client: session.client) }
            .navigationDestination(item: Binding(
                get: { viewModel.rutaAbierta },
                set: { nuevo in if nuevo == nil { viewModel.cerrarArchivo() } }
            )) { ruta in
                visorDeArchivo(ruta: ruta)
            }
        }
    }

    private var estudio: some View {
        VStack(spacing: 12) {
            HStack {
                Image(systemName: "folder.fill").foregroundStyle(EdecanTheme.morado)
                TextField("Ruta dentro de tu computadora", text: $viewModel.rutaActual)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit { Task { await viewModel.abrirRuta(client: session.client) } }
                Button {
                    Task { await viewModel.abrirRuta(client: session.client) }
                } label: { Image(systemName: "arrow.right.circle.fill") }
            }
            .padding(12)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 16))
            .padding(.horizontal)

            Picker("Modo", selection: $pestaña) {
                ForEach(Pestaña.allCases, id: \.self) { Text($0.rawValue).tag($0) }
            }
            .pickerStyle(.segmented)
            .padding(.horizontal)

            switch pestaña {
            case .archivos:
                arbolDeArchivos
            case .agente:
                VStack(spacing: 14) {
                    Image(systemName: "sparkles.rectangle.stack.fill")
                        .font(.system(size: 54))
                        .foregroundStyle(EdecanTheme.morado)
                    Text("Agente del proyecto").font(.title2.bold())
                    Text("Pídele en el chat que trabaje en “\(viewModel.rutaActual.isEmpty ? "la carpeta compartida" : viewModel.rutaActual)”. Verás el progreso en vivo en la conversación.")
                        .multilineTextAlignment(.center)
                        .foregroundStyle(.secondary)
                }
                .padding(30)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .terminal:
                terminal
            }
        }
    }

    private var terminal: some View {
        VStack(spacing: 0) {
            ScrollViewReader { proxy in
                ScrollView {
                    Text(viewModel.salidaTerminal.isEmpty ? "Terminal segura de \(viewModel.rutaActual.isEmpty ? "tu espacio" : viewModel.rutaActual)." : viewModel.salidaTerminal)
                        .font(.system(.footnote, design: .monospaced))
                        .foregroundStyle(Color(red: 0.35, green: 0.95, blue: 0.70))
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                        .padding()
                        .id("terminal-end")
                }
                .background(Color(red: 0.04, green: 0.05, blue: 0.08))
                .onChange(of: viewModel.salidaTerminal) { _, _ in
                    withAnimation { proxy.scrollTo("terminal-end", anchor: .bottom) }
                }
            }
            HStack {
                Text("$").foregroundStyle(.green).font(.system(.body, design: .monospaced))
                TextField("comando…", text: $viewModel.comando)
                    .font(.system(.body, design: .monospaced))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .onSubmit { Task { await viewModel.ejecutar(client: session.client) } }
                Button {
                    Task { await viewModel.ejecutar(client: session.client) }
                } label: {
                    if viewModel.ejecutandoComando { ProgressView() }
                    else { Image(systemName: "arrow.up.circle.fill").font(.title2) }
                }
            }
            .padding()
            .background(Color(red: 0.07, green: 0.08, blue: 0.12))
        }
        .clipShape(RoundedRectangle(cornerRadius: 18))
        .padding(.horizontal)
    }

    private var sinCompanion: some View {
        VStack(spacing: 16) {
            EmptyStateView(
                icono: "chevron.left.forwardslash.chevron.right",
                titulo: "Computadora no disponible",
                descripcion: "Abre Edecán en tu computadora para navegar aquí el proyecto de ese equipo.",
                etiquetaRoadmap: nil
            )
            Button("Ir a Ajustes") { tabRouter.seleccion = .settings }
                .buttonStyle(.bordered)
        }
    }

    private var arbolDeArchivos: some View {
        List {
            if viewModel.truncado {
                Section {
                    Label("El árbol se recortó por tamaño — algunas carpetas no se expanden.", systemImage: "info.circle")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            OutlineGroup(nodosRaiz, children: \.children) { nodo in
                filaDeNodo(nodo)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
    }

    private var nodosRaiz: [NodoIDE] {
        viewModel.arbol.map {
            NodoIDE(
                entry: $0,
                ruta: viewModel.rutaActual.isEmpty ? $0.name : "\(viewModel.rutaActual)/\($0.name)"
            )
        }
    }

    private func filaDeNodo(_ nodo: NodoIDE) -> some View {
        Group {
            if nodo.entry.isDir {
                Label(nodo.entry.name, systemImage: "folder.fill")
                    .foregroundStyle(.primary)
            } else {
                Button {
                    Task { await viewModel.abrir(ruta: nodo.ruta, client: session.client) }
                } label: {
                    HStack {
                        Label(nodo.entry.name, systemImage: iconoParaArchivo(nodo.entry.name))
                        Spacer()
                        if let tamano = nodo.entry.sizeBytes {
                            Text(formatoTamano(tamano)).font(.caption2).foregroundStyle(.secondary)
                        }
                    }
                }
                .buttonStyle(.plain)
            }
        }
    }

    private func iconoParaArchivo(_ nombre: String) -> String {
        let ext = (nombre as NSString).pathExtension.lowercased()
        switch ext {
        case "swift", "py", "js", "ts", "tsx", "jsx", "rs", "go", "java", "kt": return "chevron.left.forwardslash.chevron.right"
        case "md", "txt": return "doc.text"
        case "json", "yml", "yaml", "toml": return "gearshape"
        case "png", "jpg", "jpeg", "gif", "svg": return "photo"
        default: return "doc"
        }
    }

    private func formatoTamano(_ bytes: Int) -> String {
        ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file)
    }

    @ViewBuilder
    private func visorDeArchivo(ruta: String) -> some View {
        ScrollView([.horizontal, .vertical]) {
            if viewModel.cargandoArchivo {
                ProgressView().padding(40)
            } else if let archivo = viewModel.archivoAbierto {
                if archivo.encoding == "utf-8" {
                    TextEditor(text: $viewModel.contenidoEditable)
                        .font(.system(.footnote, design: .monospaced))
                        .padding()
                        .frame(minWidth: 700, minHeight: 700, alignment: .topLeading)
                } else {
                    EmptyStateView(
                        icono: "doc.questionmark",
                        titulo: "Archivo binario",
                        descripcion: "Este archivo no es texto (\(archivo.sizeBytes) bytes) — todavía no hay un visor para este tipo en la app.",
                        etiquetaRoadmap: nil
                    )
                }
            } else if let error = viewModel.errorMensaje {
                EmptyStateView(icono: "exclamationmark.triangle.fill", titulo: "No se pudo abrir", descripcion: error, etiquetaRoadmap: nil)
            }
        }
        .navigationTitle((ruta as NSString).lastPathComponent)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await viewModel.guardar(client: session.client) }
                } label: {
                    if viewModel.guardandoArchivo { ProgressView() }
                    else { Label("Guardar", systemImage: "square.and.arrow.down") }
                }
                .disabled(
                    viewModel.archivoAbierto?.encoding != "utf-8" ||
                    viewModel.contenidoEditable == viewModel.archivoAbierto?.content
                )
            }
        }
    }
}
