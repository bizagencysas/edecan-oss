import SwiftUI
import EdecanKit

/// Estado real de las extensiones de Edecan. Vive en Ajustes avanzados: la
/// persona no necesita abrir esta pantalla para usarlas, solo pedir algo en
/// el chat; sirve para entender qué está disponible y detectar conexiones.
struct CapabilitiesView: View {
    @Environment(SessionStore.self) private var session
    @State private var skills: [SkillSummary] = []
    @State private var servers: [MCPServerSummary] = []
    @State private var loading = true
    @State private var skillsError: String?
    @State private var mcpError: String?

    var body: some View {
        List {
            Section {
                Label("No tienes que elegir herramientas. Dile a Edecan qué necesitas y él usa la capacidad correcta.", systemImage: "sparkles")
                    .font(.subheadline)
            }

            Section("Habilidades") {
                if let skillsError {
                    Text(skillsError).foregroundStyle(.secondary)
                } else if skills.isEmpty && !loading {
                    Text("No hay habilidades adicionales instaladas.").foregroundStyle(.secondary)
                } else {
                    ForEach(skills) { skill in
                        HStack(spacing: 12) {
                            Image(systemName: skill.enabled ? "checkmark.seal.fill" : "pause.circle")
                                .foregroundStyle(skill.enabled ? Color.green : Color.secondary)
                            VStack(alignment: .leading, spacing: 3) {
                                Text(skill.nombre).font(.subheadline.weight(.medium))
                                Text(skill.descripcion).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            }
                            Spacer()
                            Text(skill.enabled ? "Activa" : "Pausada").font(.caption2)
                        }
                    }
                }
            }

            Section("Conexiones externas") {
                if let mcpError {
                    Text(mcpError).foregroundStyle(.secondary)
                } else if servers.isEmpty && !loading {
                    Text("No hay servicios externos conectados.").foregroundStyle(.secondary)
                } else {
                    ForEach(servers) { server in
                        HStack {
                            Image(systemName: "point.3.connected.trianglepath.dotted")
                                .foregroundStyle(EdecanTheme.morado)
                            VStack(alignment: .leading) {
                                Text(server.nombre)
                                Text(server.transporte == "http" ? "Servicio web" : "Herramienta local")
                                    .font(.caption).foregroundStyle(.secondary)
                            }
                            Spacer()
                            Circle().fill(server.estado == "active" ? Color.green : Color.orange)
                                .frame(width: 8, height: 8)
                        }
                    }
                }
            }
        }
        .overlay { if loading { ProgressView() } }
        .navigationTitle("Capacidades")
        .refreshable { await cargar() }
        .task { await cargar() }
    }

    private func cargar() async {
        guard let client = session.client else { loading = false; return }
        loading = true
        skillsError = nil
        mcpError = nil
        do { skills = try await client.listSkills() }
        catch { skillsError = "No se pudieron consultar las habilidades." }
        do { servers = try await client.listMCPServers() }
        catch APIClient.APIError.servidor(let status, _) where status == 403 {
            mcpError = "Las conexiones externas no están habilitadas en este plan."
        } catch { mcpError = "No se pudieron consultar las conexiones externas." }
        loading = false
    }
}
