import SwiftUI
import EdecanKit

/// "Workspaces" — espacios de trabajo sobre `GET/POST /v1/workspaces`: agrupa
/// agentes alrededor de un contexto compartido. Contrato en paralelo: una ruta
/// inexistente degrada con "Próximamente" (directiva §153).
struct WorkspacesView: View {
    @Environment(SessionStore.self) private var session
    @State private var espacios: [Workspace] = []
    @State private var workers: [PersistentWorker] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false
    @State private var ocupado = false
    @State private var creando = false
    @State private var espacioParaAgentes: Workspace?

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

            if espacios.isEmpty && !cargando && !proximamente {
                Text("Crea un espacio para reunir compañeros alrededor de un contexto de trabajo.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }

            ForEach(espacios) { espacio in
                Button {
                    espacioParaAgentes = espacio
                } label: {
                    FilaWorkspace(espacio: espacio)
                }
                .foregroundStyle(.primary)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Workspaces")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    creando = true
                } label: {
                    Label("Nuevo espacio", systemImage: "plus")
                }
                .disabled(ocupado)
            }
        }
        .overlay {
            if cargando && espacios.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
        .sheet(isPresented: $creando) {
            NavigationStack { NuevoWorkspaceSheet { nombre in
                Task { await crear(nombre) }
            } }
        }
        .sheet(item: $espacioParaAgentes) { espacio in
            NavigationStack {
                AgentesWorkspaceSheet(espacio: espacio, workers: workers) { agenteId in
                    Task { await asignar(espacio, agenteId: agenteId) }
                } onQuitar: { agenteId in
                    Task { await quitar(espacio, agenteId: agenteId) }
                }
            }
        }
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("Los espacios de trabajo están llegando al servidor. Vuelve en un momento.")
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
        cargando = espacios.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            espacios = try await client.listWorkspaces()
            workers = (try? await client.listWorkers()) ?? []
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

    private func crear(_ nombre: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            _ = try await client.createWorkspace(name: nombre)
            creando = false
            await cargar()
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "Los espacios de trabajo están llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func asignar(_ espacio: Workspace, agenteId: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            try await client.addWorkspaceAgent(workspaceId: espacio.id, agentId: agenteId)
            await cargar()
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "Los espacios de trabajo están llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }

    private func quitar(_ espacio: Workspace, agenteId: String) async {
        guard let client = session.client else { return }
        ocupado = true
        defer { ocupado = false }
        do {
            try await client.removeWorkspaceAgent(workspaceId: espacio.id, agentId: agenteId)
            await cargar()
        } catch let apiError as APIClient.APIError {
            self.error = apiError.esProximamente
                ? "Los espacios de trabajo están llegando al servidor."
                : apiError.localizedDescription
        } catch {
            self.error = error.localizedDescription
        }
    }
}

private struct FilaWorkspace: View {
    let espacio: Workspace

    var body: some View {
        HStack(spacing: 12) {
            ZStack {
                Circle().fill(EdecanTheme.azul.opacity(0.14))
                Image(systemName: "square.stack.3d.up.fill")
                    .foregroundStyle(EdecanTheme.azul)
            }
            .frame(width: 40, height: 40)
            VStack(alignment: .leading, spacing: 3) {
                Text(espacio.name.isEmpty ? "Workspace" : espacio.name)
                    .font(.subheadline.weight(.semibold))
                if let descripcion = espacio.description, !descripcion.isEmpty {
                    Text(descripcion)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                Text(espacio.agents.isEmpty
                     ? "Sin agentes asignados"
                     : "\(espacio.agents.count) agente\(espacio.agents.count == 1 ? "" : "s")")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
    }
}

private struct NuevoWorkspaceSheet: View {
    @Environment(\.dismiss) private var dismiss
    let onCrear: (String) -> Void
    @State private var nombre = ""

    var body: some View {
        Form {
            Section("Nombre") {
                TextField("Ej. Cliente ACME, Producto…", text: $nombre)
            }
        }
        .navigationTitle("Nuevo espacio")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .cancellationAction) {
                Button("Cancelar") { dismiss() }
            }
            ToolbarItem(placement: .confirmationAction) {
                Button("Crear") {
                    onCrear(nombre.trimmingCharacters(in: .whitespacesAndNewlines))
                    dismiss()
                }
                .disabled(nombre.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
        }
    }
}

private struct AgentesWorkspaceSheet: View {
    @Environment(\.dismiss) private var dismiss
    let espacio: Workspace
    let workers: [PersistentWorker]
    let onAgregar: (String) -> Void
    let onQuitar: (String) -> Void

    private var asignados: Set<String> {
        Set(espacio.agents.map(\.agentId))
    }

    var body: some View {
        List {
            Section("Asignados") {
                if espacio.agents.isEmpty {
                    Text("Ningún agente todavía.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(espacio.agents) { agente in
                        HStack {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(agente.nombreVisible)
                                    .font(.subheadline.weight(.semibold))
                                if let cargo = agente.roleTitle, !cargo.isEmpty {
                                    Text(cargo)
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Button {
                                onQuitar(agente.agentId)
                            } label: {
                                Image(systemName: "minus.circle")
                                    .foregroundStyle(.red)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            Section("Del roster") {
                ForEach(workers) { worker in
                    Button {
                        onAgregar(worker.id)
                    } label: {
                        HStack(spacing: 12) {
                            VStack(alignment: .leading, spacing: 2) {
                                Text(worker.nombreVisible)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(.primary)
                                Text(worker.cargoVisible)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                            Spacer()
                            if asignados.contains(worker.id) {
                                Image(systemName: "checkmark.circle.fill")
                                    .foregroundStyle(EdecanTheme.morado)
                            } else {
                                Image(systemName: "plus.circle")
                                    .foregroundStyle(.secondary)
                            }
                        }
                    }
                    .disabled(asignados.contains(worker.id))
                }
            }
        }
        .navigationTitle(espacio.name.isEmpty ? "Workspace" : espacio.name)
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                Button("Listo") { dismiss() }
            }
        }
    }
}