import SwiftUI
import EdecanKit

/// Sección Git con tarjetas light elevadas.
struct IDEGitView: View {
    @Environment(SessionStore.self) private var session
    @Bindable var viewModel: IDEViewModel
    @State private var confirmarPush = false

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                if viewModel.cargandoGit {
                    ProgressView("Leyendo Git…")
                        .tint(IDETheme.acento)
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
            .padding(.horizontal, 14)
            .padding(.top, 8)
            .padding(.bottom, 20)
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

    private func tarjeta<Content: View>(@ViewBuilder _ content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            content()
        }
        .padding(14)
        .idePanel(esquina: 18)
    }

    private func estadoGit(_ status: IDEGitStatus) -> some View {
        tarjeta {
            HStack(spacing: 12) {
                Image(systemName: "arrow.triangle.branch")
                    .font(.title2)
                    .foregroundStyle(IDETheme.acento)
                VStack(alignment: .leading, spacing: 3) {
                    Text(status.branch ?? "HEAD separado")
                        .font(.headline)
                        .foregroundStyle(IDETheme.texto)
                    HStack(spacing: 8) {
                        if let upstream = status.upstream {
                            Text(upstream)
                        }
                        if status.ahead > 0 { Text("↑ \(status.ahead)") }
                        if status.behind > 0 { Text("↓ \(status.behind)") }
                    }
                    .font(.caption)
                    .foregroundStyle(IDETheme.textoSuave)
                }
                Spacer()
                Button {
                    Task { await viewModel.refrescarGit(client: session.client) }
                } label: {
                    Image(systemName: "arrow.clockwise")
                        .foregroundStyle(IDETheme.textoSuave)
                }
                .accessibilityLabel("Refrescar Git")
            }
        }
    }

    private func cambiosGit(_ status: IDEGitStatus) -> some View {
        tarjeta {
            HStack {
                Text("Cambios")
                    .font(.headline)
                    .foregroundStyle(IDETheme.texto)
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
                            .foregroundStyle(IDETheme.textoSuave)
                    }
                }
            }

            if status.files.isEmpty {
                Label("Todo está al día", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(IDETheme.verde)
                    .font(.subheadline)
            } else {
                ForEach(status.files) { archivo in
                    HStack(spacing: 10) {
                        Text("\(archivo.indexStatus)\(archivo.worktreeStatus)")
                            .font(.system(.caption, design: .monospaced).bold())
                            .foregroundStyle(archivo.isStaged ? IDETheme.verde : IDETheme.naranja)
                            .frame(width: 24)
                        Text(archivo.path)
                            .font(.subheadline)
                            .foregroundStyle(IDETheme.texto)
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
                        .foregroundStyle(IDETheme.acento)
                    }
                    .padding(.vertical, 4)
                }
            }
        }
    }

    private var commitGit: some View {
        tarjeta {
            Text("Guardar versión")
                .font(.headline)
                .foregroundStyle(IDETheme.texto)
            TextField("Describe el cambio", text: $viewModel.mensajeCommit, axis: .vertical)
                .textFieldStyle(.roundedBorder)
                .foregroundStyle(IDETheme.texto)
                .tint(IDETheme.acento)
                .lineLimit(2...4)
            Button {
                Task { await viewModel.commit(client: session.client) }
            } label: {
                Label("Crear commit", systemImage: "checkmark.seal")
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 4)
            }
            .buttonStyle(.borderedProminent)
            .tint(IDETheme.acento)
            .disabled(
                viewModel.accionGitEnCurso ||
                viewModel.mensajeCommit.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            )
        }
    }

    private var ramasGit: some View {
        tarjeta {
            Text("Ramas y remoto")
                .font(.headline)
                .foregroundStyle(IDETheme.texto)
            HStack {
                TextField("Nueva rama", text: $viewModel.nuevaRama)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                    .foregroundStyle(IDETheme.texto)
                    .tint(IDETheme.acento)
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
                    .padding(.vertical, 4)
            }
            .buttonStyle(.bordered)
            .disabled(viewModel.accionGitEnCurso)
        }
    }

    private var diffGit: some View {
        tarjeta {
            Text("Diferencias")
                .font(.headline)
                .foregroundStyle(IDETheme.texto)
            if let diff = viewModel.gitDiff, !diff.text.isEmpty {
                ScrollView(.horizontal) {
                    Text(diff.text)
                        .font(.system(.caption2, design: .monospaced))
                        .foregroundStyle(IDETheme.texto)
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                if diff.truncated {
                    Text("Vista recortada por tamaño.")
                        .font(.caption)
                        .foregroundStyle(IDETheme.textoSuave)
                }
            } else {
                Text("No hay diferencias sin preparar.")
                    .font(.subheadline)
                    .foregroundStyle(IDETheme.textoSuave)
            }
        }
    }

    private var historialGit: some View {
        tarjeta {
            Text("Historial")
                .font(.headline)
                .foregroundStyle(IDETheme.texto)
            if viewModel.gitLog.isEmpty {
                Text("Este proyecto todavía no tiene commits.")
                    .font(.subheadline)
                    .foregroundStyle(IDETheme.textoSuave)
            } else {
                ForEach(viewModel.gitLog.prefix(20)) { commit in
                    HStack(alignment: .top, spacing: 10) {
                        Text(commit.shortHash)
                            .font(.system(.caption, design: .monospaced).bold())
                            .foregroundStyle(IDETheme.acento)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(commit.subject)
                                .font(.subheadline)
                                .foregroundStyle(IDETheme.texto)
                            Text(commit.author)
                                .font(.caption)
                                .foregroundStyle(IDETheme.textoSuave)
                        }
                    }
                }
            }
        }
    }
}
