import SwiftUI
import EdecanKit

/// Formulario "pegar y validar" de `PUT /v1/credentials/llm`
/// (`DIRECCION_ACTUAL.md` "Principio de UX no negociable: configuración de
/// pocos clicks") — un `Picker` de proveedor, los campos que ese proveedor
/// necesita, y un botón que prueba la credencial al toque mostrando el error
/// EXACTO del backend si no sirve.
struct ConectarLLMSheet: View {
    @Bindable var viewModel: CredencialesViewModel
    let deteccion: DetectLocalProviders?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                if viewModel.credenciales?.llm != nil {
                    seccionModeloActivo
                }

                if let deteccion, deteccion.localMode {
                    seccionDetectados(deteccion)
                }

                Section("Proveedor") {
                    Picker("Tipo", selection: $viewModel.kindSeleccionado) {
                        ForEach(opcionesDisponibles) { kind in
                            Text(kind.nombreVisible).tag(kind)
                        }
                    }
                }

                Section {
                    if requiereAPIKey {
                        SecureField("API key", text: $viewModel.apiKey)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    if requiereBaseURL {
                        TextField(viewModel.kindSeleccionado == .ollama ? "Base URL (opcional, http://localhost:11434)" : "Base URL", text: $viewModel.baseURL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .keyboardType(.URL)
                    }
                    if viewModel.kindSeleccionado == .ollama {
                        TextField("Modelo ya descargado (p. ej. llama3.1)", text: $viewModel.modeloPrincipal)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    if viewModel.kindSeleccionado == .claudeCli || viewModel.kindSeleccionado == .codexCli {
                        Text("No necesita ninguna credencial — el servidor confirma que el binario está instalado y autenticado en esa máquina.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                } footer: {
                    if viewModel.kindSeleccionado.requiereModoLocal && !(deteccion?.localMode ?? false) {
                        Text("Este proveedor requiere la app de escritorio (modo local) — un servidor hospedado no puede usar un binario/puerto de tu máquina.")
                            .foregroundStyle(.orange)
                    }
                }

                if let error = viewModel.errorGuardado {
                    Section {
                        Text(error).font(.footnote).foregroundStyle(.red)
                    }
                }
                if viewModel.guardadoExitoso {
                    Section {
                        Label("Conectado correctamente", systemImage: "checkmark.circle.fill")
                            .foregroundStyle(.green)
                    }
                }
            }
            .navigationTitle("Inteligencia")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cerrar") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button {
                        Task { await viewModel.conectarLLM(client: client) }
                    } label: {
                        if viewModel.guardando {
                            ProgressView()
                        } else {
                            Text("Conectar")
                        }
                    }
                    .disabled(viewModel.guardando || !formularioValido)
                }
            }
            .task {
                await viewModel.cargarModelos(client: client)
            }
        }
    }

    // El sheet no habla con la red directamente — recibe el cliente del
    // padre a través de un closure sería una opción, pero como `PerfilView`
    // ya inyecta `SessionStore` en el entorno, es más simple leerlo aquí
    // también en vez de pasar el `APIClient` por parámetro.
    @Environment(SessionStore.self) private var session
    private var client: APIClient? { session.client }

    private func seccionDetectados(_ deteccion: DetectLocalProviders) -> some View {
        Section("Detectado en esta máquina") {
            if deteccion.claudeCli.installed {
                Button("Usar Claude CLI ya instalado") {
                    Task { await viewModel.conectarDetectado(.claudeCli, client: client) }
                }
            }
            if deteccion.codexCli.installed {
                Button("Usar Codex CLI ya instalado") {
                    Task { await viewModel.conectarDetectado(.codexCli, client: client) }
                }
            }
            if deteccion.ollama.running {
                ForEach(deteccion.ollama.models, id: \.self) { modelo in
                    Button("Usar Ollama — \(modelo)") {
                        Task { await viewModel.conectarDetectado(.ollama, modelo: modelo, client: client) }
                    }
                }
            }
            if !deteccion.claudeCli.installed && !deteccion.codexCli.installed && !deteccion.ollama.running {
                Text("No se detectó ningún CLI/Ollama instalado en esta máquina.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var seccionModeloActivo: some View {
        Section("Modelo activo") {
            Text("El modelo cambia la inteligencia, no los poderes. Internet, archivos y herramientas pertenecen a Edecán.")
                .font(.footnote)
                .foregroundStyle(.secondary)

            if let catalogo = viewModel.catalogoModelos, !catalogo.models.isEmpty {
                Picker("Principal", selection: $viewModel.modeloActivoPrincipal) {
                    ForEach(catalogo.models, id: \.self) { modelo in
                        Text(modelo).tag(modelo)
                    }
                }
                Picker("Rápido", selection: $viewModel.modeloActivoRapido) {
                    ForEach(catalogo.models, id: \.self) { modelo in
                        Text(modelo).tag(modelo)
                    }
                }
            }

            DisclosureGroup("Escribir un modelo nuevo") {
                TextField("Modelo principal", text: $viewModel.modeloActivoPrincipal)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                TextField("Modelo rápido", text: $viewModel.modeloActivoRapido)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            }

            if let error = viewModel.errorModelo {
                Text(error).font(.footnote).foregroundStyle(.red)
            }
            if viewModel.modeloGuardadoExitoso {
                Label("Modelo actualizado", systemImage: "checkmark.circle.fill")
                    .foregroundStyle(.green)
            }
            Button {
                Task { await viewModel.guardarModelos(client: client) }
            } label: {
                if viewModel.guardandoModelo {
                    ProgressView()
                } else {
                    Text("Usar este modelo")
                }
            }
            .disabled(
                viewModel.guardandoModelo
                    || viewModel.modeloActivoPrincipal.trimmingCharacters(in: .whitespaces).isEmpty
            )
        }
    }

    private var opcionesDisponibles: [LLMProviderKind] {
        LLMProviderKind.allCases.filter { !$0.requiereModoLocal || (deteccion?.localMode ?? false) }
    }

    private var requiereAPIKey: Bool {
        switch viewModel.kindSeleccionado {
        case .anthropic, .vertex, .openaiCompat: return true
        case .claudeCli, .codexCli, .ollama: return false
        }
    }

    private var requiereBaseURL: Bool {
        switch viewModel.kindSeleccionado {
        case .openaiCompat, .ollama: return true
        default: return false
        }
    }

    private var formularioValido: Bool {
        switch viewModel.kindSeleccionado {
        case .anthropic, .vertex:
            return !viewModel.apiKey.trimmingCharacters(in: .whitespaces).isEmpty
        case .openaiCompat:
            return !viewModel.baseURL.trimmingCharacters(in: .whitespaces).isEmpty
        case .ollama:
            return !viewModel.modeloPrincipal.trimmingCharacters(in: .whitespaces).isEmpty
        case .claudeCli, .codexCli:
            return true
        }
    }
}
