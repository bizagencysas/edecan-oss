import SwiftUI
import EdecanKit

/// Panel de código light con gutter, números de línea y resaltado básico.
struct IDEArchivoEditorView: View {
    @Environment(SessionStore.self) private var session
    @Bindable var viewModel: IDEViewModel
    let onVerArchivos: () -> Void
    @State private var editando = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        Group {
            if viewModel.cargandoArchivo && viewModel.archivoAbierto == nil {
                ProgressView("Abriendo archivo…")
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .tint(IDETheme.acento)
            } else if let archivo = viewModel.archivoAbierto {
                if archivo.encoding == "utf-8" {
                    editor(archivo)
                } else {
                    EmptyStateView(
                        icono: "doc.questionmark",
                        titulo: "Archivo binario",
                        descripcion: "Este archivo no es texto y todavía no puede editarse aquí."
                    )
                }
            } else {
                sinArchivo
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .idePanel(esquina: 16)
        .padding(.horizontal, 14)
        .padding(.bottom, 10)
    }

    private func editor(_ archivo: IDEFileOut) -> some View {
        VStack(spacing: 0) {
            cabecera(archivo)
            Divider().overlay(IDETheme.superficieBorde)
            if editando {
                TextEditor(text: $viewModel.contenidoEditable)
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(IDETheme.texto)
                    .scrollContentBackground(.hidden)
                    .padding(8)
                    .background(IDETheme.terminal)
            } else {
                visor
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private func cabecera(_ archivo: IDEFileOut) -> some View {
        HStack(spacing: 10) {
            Button(action: onVerArchivos) {
                Image(systemName: "sidebar.leading")
                    .font(.system(size: 13, weight: .semibold))
                    .foregroundStyle(IDETheme.acento)
                    .frame(width: 32, height: 32)
                    .background(IDETheme.gutter, in: RoundedRectangle(cornerRadius: 9))
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Ver archivos")

            Image(systemName: IDETheme.archivoIcono((archivo.path as NSString).lastPathComponent))
                .foregroundStyle(IDETheme.acento)
            Text((archivo.path as NSString).lastPathComponent)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(IDETheme.texto)
                .lineLimit(1)

            Spacer()

            Text(lineas.count == 1 ? "1 línea" : "\(lineas.count) líneas")
                .font(.caption2)
                .foregroundStyle(IDETheme.textoSuave)

            Button {
                if reduceMotion {
                    editando.toggle()
                } else {
                    withAnimation(.easeOut(duration: 0.14)) {
                        editando.toggle()
                    }
                }
            } label: {
                Text(editando ? "Listo" : "Editar")
                    .font(.caption.weight(.semibold))
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(IDETheme.gutter, in: RoundedRectangle(cornerRadius: 9))
                    .foregroundStyle(IDETheme.texto)
            }
            .buttonStyle(.plain)

            Button {
                Task { await viewModel.guardar(client: session.client) }
            } label: {
                Group {
                    if viewModel.guardandoArchivo {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "square.and.arrow.down")
                    }
                }
                .frame(width: 32, height: 32)
                .background(IDETheme.gutter, in: RoundedRectangle(cornerRadius: 9))
                .foregroundStyle(
                    consentidoGuardar ? IDETheme.acento : IDETheme.textoSuave
                )
            }
            .buttonStyle(.plain)
            .disabled(!consentidoGuardar)
            .accessibilityLabel("Guardar")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(IDETheme.terminalLinea)
    }

    private var consentidoGuardar: Bool {
        viewModel.archivoAbierto?.encoding == "utf-8" &&
            viewModel.contenidoEditable != viewModel.archivoAbierto?.content
    }

    private var lineas: [String] {
        viewModel.contenidoEditable.components(separatedBy: "\n")
    }

    private var visor: some View {
        ScrollView {
            ScrollView(.horizontal, showsIndicators: true) {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lineas.indices, id: \.self) { i in
                        HStack(alignment: .top, spacing: 0) {
                            Text("\(i + 1)")
                                .font(.system(.footnote, design: .monospaced))
                                .foregroundStyle(IDETheme.numeroLinea)
                                .frame(width: 44, alignment: .trailing)
                                .padding(.vertical, 2)
                                .background(IDETheme.gutter)
                            Text(IDESyntaxHighlight.resaltar(lineas[i]))
                                .lineLimit(1)
                                .textSelection(.enabled)
                                .padding(.leading, 12)
                                .padding(.vertical, 2)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .background(i.isMultiple(of: 2) ? Color.clear : IDETheme.terminal.opacity(0.5))
                    }
                }
                .padding(.vertical, 8)
            }
        }
        .background(IDETheme.terminal)
    }

    private var sinArchivo: some View {
        VStack(spacing: 18) {
            Image(systemName: "doc.text")
                .font(.system(size: 48, weight: .light))
                .foregroundStyle(EdecanTheme.degradado)
            VStack(spacing: 6) {
                Text("Elige un archivo")
                    .font(.title3.bold())
                    .foregroundStyle(IDETheme.texto)
                Text("Toca un archivo del explorador para verlo aquí y editarlo.")
                    .font(.subheadline)
                    .foregroundStyle(IDETheme.textoSuave)
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
            Button("Ver archivos", action: onVerArchivos)
                .buttonStyle(.borderedProminent)
                .tint(IDETheme.acento)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }
}
