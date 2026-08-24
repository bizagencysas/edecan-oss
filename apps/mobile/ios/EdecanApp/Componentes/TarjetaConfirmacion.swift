import SwiftUI
import EdecanKit

/// Tarjeta inline "Edecán quiere usar «herramienta»" con botones
/// Aprobar/Rechazar — se muestra cuando `ChatViewModel.confirmacionPendiente`
/// no es `nil` (evento SSE `confirmation_required`, una herramienta marcada
/// `dangerous=True` que necesita aprobación humana antes de ejecutarse,
/// `ARCHITECTURE.md` §10.7). Reutilizada tal cual por ``ChatView``,
/// ``VozView`` (cada una con su propio `ChatViewModel`) y ``MisionesView``
/// (paso `waiting_confirmation` de una misión), así que el flujo de
/// aprobar/rechazar se comporta exactamente igual en las tres pantallas.
///
/// Muestra, ADEMÁS del JSON crudo de los argumentos, la advertencia
/// específica en lenguaje llano de ``ConfirmacionFormato/advertenciasPorHerramienta``
/// cuando la herramienta tiene una registrada (hoy solo `usar_computadora` —
/// mismo hallazgo de auditoría "riesgo-legal-tos" que ya corrigió la web,
/// ver el docstring de ``ConfirmacionFormato``). Herramientas sin entrada se
/// ven exactamente igual que antes de este cambio.
struct TarjetaConfirmacion: View {
    let confirmacion: ChatViewModel.ConfirmacionPendiente
    var deshabilitada: Bool = false
    let onResolver: (Bool) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .foregroundStyle(.orange)
                Text("Edecán quiere usar «\(confirmacion.nombre)»")
                    .font(.subheadline.weight(.semibold))
            }
            if let advertencia = ConfirmacionFormato.advertencia(paraHerramienta: confirmacion.nombre) {
                Text(advertencia)
                    .font(.caption.weight(.medium))
                    .foregroundStyle(.orange)
            }
            if !confirmacion.args.isEmpty {
                Text(ConfirmacionFormato.vistaPreviaRecortada(JSONValue.object(confirmacion.args).vistaPrevia))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }
            Text("Necesita tu aprobación antes de ejecutarla.")
                .font(.caption)
                .foregroundStyle(.secondary)
            HStack {
                Button("Rechazar", role: .destructive) { onResolver(false) }
                    .buttonStyle(.bordered)
                Spacer()
                Button("Aprobar") { onResolver(true) }
                    .buttonStyle(.borderedProminent)
                    .tint(EdecanTheme.morado)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
        .disabled(deshabilitada)
        .opacity(deshabilitada ? 0.6 : 1)
    }
}
