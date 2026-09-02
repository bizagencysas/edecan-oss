import SwiftUI
import EdecanKit

/// Terminal remota con estilo light legible — no caja negra con verde fosforescente.
struct IDETerminalView: View {
    @Environment(SessionStore.self) private var session
    @Bindable var viewModel: IDEViewModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        VStack(spacing: 0) {
            barra
            salida
            entrada
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .idePanel(esquina: 18)
        .padding(.horizontal, 14)
        .padding(.bottom, 10)
    }

    private var barra: some View {
        HStack(spacing: 10) {
            Menu {
                let activas = viewModel.terminales.filter(\.isActive)
                let inactivas = viewModel.terminales.filter { !$0.isActive }

                if !activas.isEmpty {
                    Section("En ejecución") {
                        ForEach(activas) { terminal in
                            sessionButton(terminal)
                        }
                    }
                }
                if !inactivas.isEmpty {
                    Section("Historial") {
                        ForEach(inactivas) { terminal in
                            sessionButton(terminal)
                        }
                    }
                }
            } label: {
                VStack(alignment: .leading, spacing: 2) {
                    Text(viewModel.terminalActivo?.title ?? "Terminal")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(IDETheme.texto)
                    HStack(spacing: 6) {
                        if viewModel.terminalActivo?.isActive == true {
                            Circle()
                                .fill(IDETheme.verde)
                                .frame(width: 6, height: 6)
                        }
                        Text(IDETheme.estadoDescripcion(viewModel.terminalActivo))
                            .font(.caption2)
                            .foregroundStyle(IDETheme.estadoColor(viewModel.terminalActivo))
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            .disabled(viewModel.terminales.isEmpty)

            Button {
                Task { await viewModel.crearTerminal(client: session.client) }
            } label: {
                Group {
                    if viewModel.creandoTerminal {
                        ProgressView().controlSize(.small)
                    } else {
                        Image(systemName: "plus")
                    }
                }
                .foregroundStyle(IDETheme.acento)
            }
            .accessibilityLabel("Nueva terminal")

            Button(role: .destructive) {
                Task { await viewModel.cerrarTerminal(client: session.client) }
            } label: {
                Image(systemName: "xmark.circle")
                    .foregroundStyle(IDETheme.rojo.opacity(0.85))
            }
            .disabled(viewModel.terminalActivo == nil)
            .accessibilityLabel("Cerrar terminal")
        }
        .padding(12)
        .background(IDETheme.terminalLinea)
    }

    @ViewBuilder
    private func sessionButton(_ terminal: IDESession) -> some View {
        Button {
            Task {
                await viewModel.seleccionarTerminal(
                    id: terminal.id,
                    client: session.client
                )
            }
        } label: {
            HStack {
                Text(terminal.title ?? "Terminal \(terminal.id.prefix(6))")
                Spacer()
                Text(IDETheme.estadoEtiqueta(terminal))
                    .font(.caption2)
                    .foregroundStyle(IDETheme.estadoColor(terminal))
            }
        }
    }

    private var salida: some View {
        ScrollViewReader { proxy in
            ScrollView {
                Group {
                    if viewModel.salidaTerminal.isEmpty {
                        Text("Abre una terminal para trabajar en este proyecto.")
                            .foregroundStyle(IDETheme.terminalPlaceholder)
                    } else {
                        Text(viewModel.salidaTerminal)
                            .foregroundStyle(IDETheme.terminalTexto)
                    }
                }
                .font(.system(.footnote, design: .monospaced))
                .textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .topLeading)
                .padding()

                Color.clear.frame(height: 1).id("terminal-final")
            }
            .onChange(of: viewModel.salidaTerminal) { _, _ in
                if reduceMotion {
                    proxy.scrollTo("terminal-final", anchor: .bottom)
                } else {
                    withAnimation(.easeOut(duration: 0.18)) {
                        proxy.scrollTo("terminal-final", anchor: .bottom)
                    }
                }
            }
        }
        .background(IDETheme.terminal)
    }

    private var entrada: some View {
        HStack(spacing: 10) {
            Button("⌃C") {
                Task { await viewModel.interrumpirTerminal(client: session.client) }
            }
            .font(.system(.caption, design: .monospaced).bold())
            .foregroundStyle(IDETheme.textoSuave)
            .disabled(viewModel.terminalActivo?.isActive != true)
            .accessibilityLabel("Interrumpir")

            HStack(spacing: 6) {
                Text("$")
                    .font(.system(.body, design: .monospaced).bold())
                    .foregroundStyle(IDETheme.terminalPrompt)
                TextField("Escribe un comando", text: $viewModel.entradaTerminal)
                    .font(.system(.body, design: .monospaced))
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .submitLabel(.send)
                    .onSubmit {
                        Task { await viewModel.enviarTerminal(client: session.client) }
                    }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(IDETheme.superficie, in: RoundedRectangle(cornerRadius: 10))
            .overlay(
                RoundedRectangle(cornerRadius: 10)
                    .strokeBorder(IDETheme.superficieBorde, lineWidth: 1)
            )

            Button {
                Task { await viewModel.enviarTerminal(client: session.client) }
            } label: {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.title2)
                    .foregroundStyle(EdecanTheme.degradado)
            }
            .disabled(viewModel.entradaTerminal.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            .accessibilityLabel("Ejecutar comando")
        }
        .padding(12)
        .background(IDETheme.terminalLinea)
    }
}
