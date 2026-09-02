import SwiftUI
import EdecanKit

/// "Conectores" — marketplace de servicios externos sobre `GET /v1/mcp/servers`
/// (ahora con `health`/`latency_ms`/`last_error`). Cada card muestra nombre,
/// conexión y salud. Conectar/desconectar apunta al flujo existente
/// (`CapabilitiesView`), porque el cliente todavía no tiene endpoints de
/// connect/disconnect — se marca "Próximamente" sin fingir éxito (directiva §153).
struct ConectoresView: View {
    @Environment(SessionStore.self) private var session
    @State private var servidores: [MCPServerSummary] = []
    @State private var cargando = true
    @State private var error: String?
    @State private var proximamente = false

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
            if servidores.isEmpty && !cargando && !proximamente {
                Text("No hay conectores configurados todavía.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .listRowSeparator(.hidden)
            }
            ForEach(servidores) { servidor in
                TarjetaConector(servidor: servidor)
                    .listRowSeparator(.hidden)
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Conectores")
        .navigationBarTitleDisplayMode(.large)
        .overlay {
            if cargando && servidores.isEmpty {
                ProgressView()
            }
        }
        .task { await cargar() }
        .refreshable { await cargar() }
    }

    private var filaProximamente: some View {
        VStack(alignment: .leading, spacing: 6) {
            Label("Próximamente", systemImage: "hourglass")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(EdecanTheme.morado)
            Text("El marketplace de conectores está llegando al servidor. Vuelve en un momento.")
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
        cargando = servidores.isEmpty
        error = nil
        proximamente = false
        defer { cargando = false }
        do {
            servidores = try await client.listMCPServers()
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
}

private struct TarjetaConector: View {
    let servidor: MCPServerSummary
    @Environment(\.colorScheme) private var colorScheme

    private var salud: SaludConector { SaludConector.de(servidor.health) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                Image(systemName: servidor.transporte == "http" ? "network" : "cable.connector")
                    .foregroundStyle(EdecanTheme.morado)
                    .frame(width: 28, height: 28)
                    .background(EdecanTheme.morado.opacity(0.12), in: Circle())
                VStack(alignment: .leading, spacing: 2) {
                    Text(servidor.nombre)
                        .font(.subheadline.weight(.semibold))
                    Text(servidor.transporte == "http" ? "Servicio web" : "Herramienta local")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 8)
                estado
            }

            HStack(spacing: 12) {
                Label(salud.etiqueta, systemImage: salud.icono)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(salud.color)
                if let latencia = servidor.latencyMs {
                    Text("\(latencia) ms")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }

            if let ultimoError = servidor.lastError, !ultimoError.isEmpty, !salud.esSaludable {
                Text(ultimoError)
                    .font(.caption)
                    .foregroundStyle(.red)
                    .lineLimit(2)
            }

            HStack(spacing: 10) {
                NavigationLink {
                    CapabilitiesView()
                } label: {
                    Label("Gestionar", systemImage: "slider.horizontal.3")
                        .font(.caption.weight(.semibold))
                }

                Spacer()

                Text("Conectar/desconectar: Próximamente")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            .padding(.top, 2)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            EdecanTheme.fondoTarjeta(colorScheme),
            in: RoundedRectangle(cornerRadius: 14, style: .continuous)
        )
    }

    private var estado: some View {
        HStack(spacing: 4) {
            Text(servidor.estaConectado ? "Conectado" : "Sin conectar")
                .font(.caption2.weight(.medium))
            Circle()
                .fill(servidor.estaConectado ? Color.green : Color.secondary)
                .frame(width: 8, height: 8)
        }
        .foregroundStyle(.primary)
    }
}

private struct SaludConector {
    let etiqueta: String
    let icono: String
    let color: Color
    let esSaludable: Bool

    static func de(_ health: String?) -> SaludConector {
        switch (health ?? "").lowercased() {
        case "healthy", "ok", "up":
            return SaludConector(etiqueta: "Saludable", icono: "checkmark.circle.fill", color: .green, esSaludable: true)
        case "degraded", "degrading":
            return SaludConector(etiqueta: "Degradado", icono: "exclamationmark.triangle.fill", color: .orange, esSaludable: false)
        case "down", "unhealthy", "error":
            return SaludConector(etiqueta: "Caído", icono: "xmark.circle.fill", color: .red, esSaludable: false)
        default:
            return SaludConector(etiqueta: "Sin dato", icono: "questionmark.circle", color: .secondary, esSaludable: false)
        }
    }
}