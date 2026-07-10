import SwiftUI

/// Estado vacío reutilizable para pestañas que todavía son placeholder
/// (IDE, Negocios, Llamadas) — mismo espíritu que `EmptyState` en
/// `apps/web/src/components/ui.tsx`, para que la app se sienta consistente
/// con el panel web aunque la pantalla todavía no tenga funcionalidad real.
struct EmptyStateView: View {
    let icono: String
    let titulo: String
    let descripcion: String
    var etiquetaRoadmap: String? = nil

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: icono)
                .font(.system(size: 44))
                .foregroundStyle(EdecanTheme.degradado)
            Text(titulo)
                .font(.title3.weight(.semibold))
            Text(descripcion)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 32)
            if let etiquetaRoadmap {
                Text(etiquetaRoadmap)
                    .font(.caption.weight(.medium))
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .tarjetaVidrio(esquina: 12)
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .padding()
    }
}
