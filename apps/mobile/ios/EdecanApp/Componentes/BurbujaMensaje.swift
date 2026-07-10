import SwiftUI

/// Una burbuja de mensaje en ``ChatView``: alineada a la derecha con el
/// degradado morado/azul para el usuario, a la izquierda con vidrio para
/// Edecán.
struct BurbujaMensaje: View {
    let mensaje: ChatViewModel.Mensaje

    var body: some View {
        HStack {
            if mensaje.rol == .usuario { Spacer(minLength: 40) }

            Group {
                if mensaje.texto.isEmpty && mensaje.enProgreso {
                    ProgressView()
                        .tint(mensaje.rol == .usuario ? .white : .primary)
                } else {
                    Text(mensaje.texto)
                        .textSelection(.enabled)
                }
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .foregroundStyle(mensaje.rol == .usuario ? .white : .primary)
            .background {
                if mensaje.rol == .usuario {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(EdecanTheme.degradado)
                } else {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(.ultraThinMaterial)
                }
            }

            if mensaje.rol == .asistente { Spacer(minLength: 40) }
        }
    }
}

/// Aviso corto de "Edecán está usando «herramienta»" mientras el turno del
/// agente ejecuta una tool (evento SSE `tool_start` sin su `tool_end`
/// todavía — `docs/api.md` §"Conversaciones y chat (SSE)").
struct IndicadorHerramienta: View {
    let nombre: String

    var body: some View {
        HStack(spacing: 8) {
            ProgressView().controlSize(.small)
            Text("Usando \(nombre)…")
                .font(.footnote)
                .foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .tarjetaVidrio(esquina: 14)
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}
