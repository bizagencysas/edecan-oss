import SwiftUI
import EdecanKit

/// Tarjeta inline compacta cuando Edecán pide aprobación para una herramienta
/// peligrosa. Los detalles técnicos quedan plegados; la acción siguiente es
/// obvia: Aprobar, Rechazar o (para computadora) Ver pantalla.
struct TarjetaConfirmacion: View {
    let confirmacion: ChatViewModel.ConfirmacionPendiente
    var deshabilitada: Bool = false
    var onVerComputadora: (() -> Void)? = nil
    let onResolver: (Bool) -> Void

    @State private var mostrarDetalle = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: iconoParaHerramienta)
                    .foregroundStyle(.orange)
                    .font(.body.weight(.semibold))
                Text(tituloCorto)
                    .font(.subheadline.weight(.semibold))
            }

            if let advertencia = ConfirmacionFormato.advertencia(paraHerramienta: confirmacion.nombre) {
                Text(advertencia)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(mostrarDetalle ? nil : 3)
            }

            if !confirmacion.args.isEmpty {
                Button(mostrarDetalle ? "Ocultar detalles" : "Ver detalles") {
                    withAnimation(.easeInOut(duration: 0.18)) { mostrarDetalle.toggle() }
                }
                .font(.caption.weight(.semibold))
                if mostrarDetalle {
                    Text(ConfirmacionFormato.vistaPreviaRecortada(JSONValue.object(confirmacion.args).vistaPrevia))
                        .font(.caption2.monospaced())
                        .foregroundStyle(.secondary)
                        .lineLimit(6)
                        .padding(8)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(Color.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 8))
                }
            }

            HStack(spacing: 8) {
                Button("Rechazar", role: .destructive) { onResolver(false) }
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                if let onVerComputadora {
                    Button("Ver computadora", action: onVerComputadora)
                        .buttonStyle(.bordered)
                        .controlSize(.small)
                }
                Spacer(minLength: 0)
                Button("Aprobar") { onResolver(true) }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
                    .controlSize(.small)
            }
        }
        .padding(12)
        .frame(maxWidth: 340, alignment: .leading)
        .tarjetaVidrio(esquina: 16)
        .disabled(deshabilitada)
        .opacity(deshabilitada ? 0.6 : 1)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Aprobación pendiente para \(confirmacion.nombre)")
    }

    private var tituloCorto: String {
        switch confirmacion.nombre {
        case "usar_computadora": return "¿Uso tu computadora?"
        case "llamar_contacto": return "¿Hago esta llamada?"
        case "instalar_skill": return "¿Instalo esta skill?"
        case "configurar_credencial": return "¿Guardo esta credencial?"
        default:
            return "¿Apruebo «\(confirmacion.nombre.replacingOccurrences(of: "_", with: " "))»?"
        }
    }

    private var iconoParaHerramienta: String {
        switch confirmacion.nombre {
        case "usar_computadora": return "display"
        case "llamar_contacto": return "phone.fill"
        case "instalar_skill": return "puzzlepiece.extension.fill"
        default: return "hand.raised.fill"
        }
    }
}
