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

/// Explorador de archivos light con panel elevado.
struct IDEArchivosView: View {
    @Environment(SessionStore.self) private var session
    @Bindable var viewModel: IDEViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var mostrandoExplorador = true

    var body: some View {
        Group {
            if mostrandoExplorador {
                explorador
            } else {
                IDEArchivoEditorView(viewModel: viewModel) {
                    mostrarExplorador()
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .onAppear {
            mostrandoExplorador = viewModel.archivoAbierto == nil
        }
    }

    private func mostrarExplorador() {
        if reduceMotion {
            mostrandoExplorador = true
        } else {
            withAnimation(.easeOut(duration: 0.16)) {
                mostrandoExplorador = true
            }
        }
    }

    private func mostrarEditor() {
        if reduceMotion {
            mostrandoExplorador = false
        } else {
            withAnimation(.easeOut(duration: 0.16)) {
                mostrandoExplorador = false
            }
        }
    }

    private var explorador: some View {
        VStack(spacing: 0) {
            barraRuta
            contenidoArbol
        }
        .idePanel(esquina: 16)
        .padding(.horizontal, 14)
        .padding(.bottom, 10)
    }

    private var barraRuta: some View {
        HStack(spacing: 8) {
            Button {
                let componentes = viewModel.rutaActual.split(separator: "/")
                viewModel.rutaActual = componentes.dropLast().joined(separator: "/")
                Task { await viewModel.abrirRuta(client: session.client) }
            } label: {
                Image(systemName: "chevron.left")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(IDETheme.acento)
                    .frame(width: 30, height: 30)
                    .background(IDETheme.gutter, in: Circle())
            }
            .buttonStyle(.plain)
            .disabled(viewModel.rutaActual.isEmpty)
            .accessibilityLabel("Subir de carpeta")

            TextField("Ruta dentro del proyecto", text: $viewModel.rutaActual)
                .font(.system(.subheadline, design: .monospaced))
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .submitLabel(.go)
                .onSubmit {
                    Task { await viewModel.abrirRuta(client: session.client) }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 7)
                .background(IDETheme.gutter, in: RoundedRectangle(cornerRadius: 10))

            Button {
                Task { await viewModel.refrescarArbol(client: session.client) }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(IDETheme.acento)
                    .frame(width: 30, height: 30)
                    .background(IDETheme.gutter, in: Circle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Refrescar archivos")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(IDETheme.terminalLinea)
    }

    private var contenidoArbol: some View {
        List {
            if viewModel.truncado {
                Label(
                    "Hay más archivos. Abre una carpeta o escribe su ruta para continuar.",
                    systemImage: "info.circle"
                )
                .font(.footnote)
                .foregroundStyle(IDETheme.textoSuave)
                .listRowBackground(IDETheme.gutter.opacity(0.5))
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
        .background(IDETheme.superficie)
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
                    filaEtiqueta(icono: "folder.fill", nombre: nodo.entry.name, activo: false)
                }
                .buttonStyle(.plain)
                .listRowBackground(Color.clear)
            } else {
                filaEtiqueta(icono: "folder.fill", nombre: nodo.entry.name, activo: false)
                    .listRowBackground(Color.clear)
            }
        } else {
            Button {
                Task { await viewModel.abrir(ruta: nodo.ruta, client: session.client) }
                mostrarEditor()
            } label: {
                filaEtiqueta(
                    icono: IDETheme.archivoIcono(nodo.entry.name),
                    nombre: nodo.entry.name,
                    activo: esActivo(nodo),
                    bytes: nodo.entry.sizeBytes
                )
            }
            .buttonStyle(.plain)
            .listRowBackground(esActivo(nodo) ? IDETheme.seleccion : Color.clear)
        }
    }

    private func filaEtiqueta(
        icono: String,
        nombre: String,
        activo: Bool,
        bytes: Int? = nil
    ) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icono)
                .foregroundStyle(activo ? IDETheme.acento : IDETheme.textoSuave)
                .frame(width: 18)
            Text(nombre)
                .font(.system(.subheadline, design: .monospaced))
                .foregroundStyle(activo ? IDETheme.acento : IDETheme.texto)
                .lineLimit(1)
            Spacer()
            if let bytes {
                Text(ByteCountFormatter.string(fromByteCount: Int64(bytes), countStyle: .file))
                    .font(.caption2)
                    .foregroundStyle(IDETheme.textoSuave)
            }
        }
        .padding(.vertical, 5)
    }

    private func esActivo(_ nodo: NodoIDE) -> Bool {
        nodo.ruta == viewModel.rutaAbierta
    }
}
