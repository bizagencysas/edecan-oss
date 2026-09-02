import SwiftUI
import EdecanKit

/// Sección Agente — light, alineada con el chat de Edecán.
struct IDEAgenteView: View {
    @Environment(SessionStore.self) private var session
    @Bindable var viewModel: IDEViewModel
    @State private var mostrandoModelo = false

    var body: some View {
        ScrollView {
            VStack(spacing: 14) {
                cabecera
                timeline

                VStack(alignment: .leading, spacing: 10) {
                    Text("¿Qué quieres construir?")
                        .font(.headline)
                        .foregroundStyle(IDETheme.texto)

                    TextEditor(text: $viewModel.promptAgente)
                        .frame(minHeight: 100)
                        .padding(8)
                        .font(.system(.subheadline, design: .monospaced))
                        .scrollContentBackground(.hidden)
                        .background(IDETheme.gutter, in: RoundedRectangle(cornerRadius: 13))
                        .overlay(
                            RoundedRectangle(cornerRadius: 13)
                                .strokeBorder(IDETheme.superficieBorde, lineWidth: 1)
                        )
                        .foregroundStyle(IDETheme.texto)
                        .tint(IDETheme.acento)

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
                            .foregroundStyle(IDETheme.texto)
                            .tint(IDETheme.acento)
                            .padding(.top, 8)
                    }
                    .font(.subheadline)
                    .foregroundStyle(IDETheme.textoSuave)

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
                        .padding(.vertical, 4)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(IDETheme.acento)
                    .disabled(
                        viewModel.creandoAgente ||
                        viewModel.promptAgente.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    )
                }
                .padding(14)
                .idePanel(esquina: 18)
            }
            .padding(.horizontal, 14)
            .padding(.top, 8)
            .padding(.bottom, 20)
        }
    }

    private var cabecera: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle()
                    .fill(indicadorConexion)
                    .frame(width: 8, height: 8)
                Text(viewModel.reconectando ? "Reconectando con la Mac" : "En vivo desde tu Mac")
                    .font(.caption.bold())
                    .foregroundStyle(IDETheme.textoSuave)
                Spacer()
                if viewModel.agenteActivo?.isActive == true {
                    ProgressView()
                        .controlSize(.small)
                        .accessibilityLabel("Trabajo en curso")
                }
            }

            HStack(spacing: 10) {
                Menu {
                    let activos = viewModel.agentes.filter(\.isActive)
                    let inactivos = viewModel.agentes.filter { !$0.isActive }

                    if !activos.isEmpty {
                        Section("En ejecución") {
                            ForEach(activos) { agente in
                                sessionButton(agente)
                            }
                        }
                    }
                    if !inactivos.isEmpty {
                        Section("Historial") {
                            ForEach(inactivos) { agente in
                                sessionButton(agente)
                            }
                        }
                    }
                } label: {
                    VStack(alignment: .leading, spacing: 3) {
                        Text(viewModel.agenteActivo?.title ?? "Agente del proyecto")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(IDETheme.texto)
                            .lineLimit(1)
                        Text(IDETheme.estadoDescripcion(viewModel.agenteActivo))
                            .font(.caption)
                            .foregroundStyle(IDETheme.estadoColor(viewModel.agenteActivo))
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
                } else if viewModel.agenteActivo != nil {
                    Button("Cerrar") {
                        Task { await viewModel.cerrarAgente(client: session.client) }
                    }
                    .font(.caption.bold())
                    .foregroundStyle(IDETheme.textoSuave)
                }
            }
        }
        .padding(14)
        .idePanel(esquina: 18)
    }

    private var indicadorConexion: Color {
        if viewModel.reconectando { return IDETheme.naranja }
        if viewModel.agenteActivo?.isActive == true { return IDETheme.verde }
        return IDETheme.textoSuave.opacity(0.5)
    }

    @ViewBuilder
    private func sessionButton(_ agente: IDESession) -> some View {
        Button {
            Task {
                await viewModel.seleccionarAgente(
                    id: agente.id,
                    client: session.client
                )
            }
        } label: {
            HStack {
                Text(agente.title ?? "Agente \(agente.id.prefix(6))")
                Spacer()
                Text(IDETheme.estadoEtiqueta(agente))
                    .font(.caption2)
                    .foregroundStyle(IDETheme.estadoColor(agente))
            }
        }
    }

    private var timeline: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label("Progreso en vivo", systemImage: "waveform.path.ecg")
                    .font(.headline)
                    .foregroundStyle(IDETheme.texto)
                Spacer()
                if viewModel.agenteActivo?.isActive == true {
                    ProgressView().controlSize(.small)
                }
            }

            if viewModel.eventosAgente.isEmpty {
                Text("Cuando Edecán empiece, verás aquí cada avance aunque cambies de app.")
                    .font(.subheadline)
                    .foregroundStyle(IDETheme.textoSuave)
                    .frame(maxWidth: .infinity, minHeight: 92, alignment: .center)
                    .multilineTextAlignment(.center)
            } else {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(viewModel.eventosAgente) { evento in
                        HStack(alignment: .top, spacing: 10) {
                            Image(systemName: IDETheme.eventoIcono(evento))
                                .foregroundStyle(IDETheme.eventoColor(evento))
                                .frame(width: 20)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(IDETheme.eventoEtiqueta(evento))
                                    .font(.caption.bold())
                                    .foregroundStyle(IDETheme.textoSuave)
                                Text(evento.text)
                                    .font(.subheadline)
                                    .foregroundStyle(IDETheme.texto)
                                    .textSelection(.enabled)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                            }
                        }
                    }
                }
            }
        }
        .padding(14)
        .idePanel(esquina: 18)
    }
}
