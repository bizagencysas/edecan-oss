import SwiftUI
import EdecanKit

/// Resumen compacto de `GET /v1/mcp/health` — sin secretos, solo conteos y estado.
struct MCPHealthBanner: View {
    let health: MCPHealthSummary

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: icono)
                .foregroundStyle(color)
            VStack(alignment: .leading, spacing: 2) {
                Text("Salud MCP: \(health.etiquetaEstado)")
                    .font(.caption.weight(.semibold))
                Text(subtitulo)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 4)
    }

    private var subtitulo: String {
        var partes = ["\(health.configured) configurado\(health.configured == 1 ? "" : "s")"]
        if health.unchecked > 0 {
            partes.append("\(health.unchecked) sin chequear")
        }
        if let rate = health.operationalRate {
            partes.append("\(Int(rate * 100))% operativos")
        }
        if let ms = health.avgLatencyMs {
            partes.append("~\(Int(ms)) ms avg")
        }
        return partes.joined(separator: " · ")
    }

    private var color: Color {
        switch health.status.lowercased() {
        case "operational", "healthy": return .green
        case "degraded": return .orange
        case "unavailable", "down", "auth_required": return .red
        default: return .secondary
        }
    }

    private var icono: String {
        switch health.status.lowercased() {
        case "operational", "healthy": return "checkmark.circle.fill"
        case "degraded": return "exclamationmark.triangle.fill"
        default: return "xmark.octagon.fill"
        }
    }
}
