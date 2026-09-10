import SwiftUI
import EdecanKit

/// Conectores OAuth + MCP. Casa = VPS: conectar/desconectar redes vía API del servidor
/// (`ASWebAuthenticationSession` → `return_to=mobile` → `edecan://conectores`). Sin Mac companion.
struct ConectoresView: View {
    @Environment(SessionStore.self) private var session

    @State private var mcp: [MCPServerSummary] = []
    @State private var mcpHealth: MCPHealthSummary?
    @State private var cargandoMCP = true
    @State private var errorMCP: String?
    @State private var mostrarRegistrarMCP = false
    @State private var servidorAEliminar: String?
    @State private var eliminandoMCP = false
    @State private var saludMCPNoDisponible = false

    var body: some View {
        List {
            Section {
                Text("El VPS es la casa: LinkedIn, X, Meta y YouTube se conectan contra tu servidor Edecán. Conectar abre el proveedor en Safari; al terminar vuelves a Edecán. La Mac companion es opcional.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowBackground(Color.clear)
            }

            SocialConnectorsSection()

            if let errorMCP {
                Section {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(errorMCP).font(.footnote).foregroundStyle(.red)
                        Button("Reintentar") {
                            Task { await cargarMCP() }
                        }
                        .font(.footnote.weight(.semibold))
                    }
                }
            }

            Section("MCP (bring-your-own)") {
                if let mcpHealth {
                    MCPHealthBanner(health: mcpHealth)
                        .listRowBackground(Color.clear)
                } else if saludMCPNoDisponible && errorMCP == nil {
                    Text("Salud MCP no disponible (GET /v1/mcp/health no respondió). La lista de servidores sigue siendo la del VPS.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                if mcp.isEmpty && !cargandoMCP {
                    Text("Sin servidores MCP configurados.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(mcp) { servidor in
                        HStack {
                            Image(systemName: servidor.transporte == "http" ? "network" : "cable.connector")
                                .foregroundStyle(EdecanTheme.morado)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(servidor.nombre).font(.subheadline.weight(.semibold))
                                Text(servidor.etiquetaSalud)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            Spacer()
                            Circle()
                                .fill(colorSalud(servidor))
                                .frame(width: 8, height: 8)
                        }
                        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
                            Button(role: .destructive) {
                                servidorAEliminar = servidor.nombre
                            } label: {
                                Label("Quitar", systemImage: "trash")
                            }
                            .disabled(eliminandoMCP)
                        }
                    }
                }
                Button {
                    mostrarRegistrarMCP = true
                } label: {
                    Label("Registrar servidor MCP", systemImage: "plus.circle")
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Conectores")
        .navigationBarTitleDisplayMode(.large)
        .sheet(isPresented: $mostrarRegistrarMCP) {
            RegistrarMCPSheet {
                Task { await cargarMCP() }
            }
            .environment(session)
        }
        .overlay {
            if cargandoMCP && mcp.isEmpty {
                ProgressView()
            }
        }
        .task { await cargarMCP() }
        .refreshable { await cargarMCP() }
        .confirmationDialog(
            "¿Quitar «\(servidorAEliminar ?? "")» del VPS?",
            isPresented: Binding(
                get: { servidorAEliminar != nil },
                set: { if !$0 { servidorAEliminar = nil } }
            ),
            titleVisibility: .visible
        ) {
            Button("Quitar del VPS", role: .destructive) {
                guard let nombre = servidorAEliminar else { return }
                Task { await eliminarServidor(nombre) }
            }
            Button("Cancelar", role: .cancel) { servidorAEliminar = nil }
        } message: {
            Text("Borra la configuración en el servidor vía DELETE /v1/mcp/servers. Para auth conversacional después, pide al bot conectar_mcp.")
        }
    }

    private func colorSalud(_ servidor: MCPServerSummary) -> Color {
        switch servidor.colorSalud {
        case "green": return .green
        case "orange": return .orange
        case "red": return .red
        case "purple": return EdecanTheme.morado
        default: return .secondary
        }
    }

    private func cargarMCP() async {
        guard let client = session.client else {
            errorMCP = "No hay sesión activa."
            cargandoMCP = false
            return
        }
        cargandoMCP = true
        errorMCP = nil
        saludMCPNoDisponible = false
        defer { cargandoMCP = false }
        // Secuencial a propósito: `withTaskGroup` mutando estado de la vista
        // golpea una limitación del region-based isolation checker de Swift 6
        // ("pattern ... does not understand how to check"). Las dos llamadas
        // son rápidas; el resultado es idéntico.
        do {
            mcp = try await client.listMCPServers()
        } catch {
            mcp = []
            errorMCP = "No se pudieron cargar los servidores MCP."
        }
        do {
            mcpHealth = try await client.getMCPHealth()
            saludMCPNoDisponible = false
        } catch {
            mcpHealth = nil
            saludMCPNoDisponible = true
        }
    }

    private func eliminarServidor(_ nombre: String) async {
        guard let client = session.client else { return }
        eliminandoMCP = true
        defer {
            eliminandoMCP = false
            servidorAEliminar = nil
        }
        do {
            try await client.deleteMCPServer(nombre: nombre)
            Haptico.exito()
            await cargarMCP()
        } catch {
            errorMCP = "No se pudo quitar «\(nombre)» del VPS."
        }
    }
}
