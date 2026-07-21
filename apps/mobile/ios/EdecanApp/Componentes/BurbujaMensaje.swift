import SwiftUI
import EdecanKit

/// Una burbuja de mensaje en ``ChatView``: alineada a la derecha con el
/// degradado morado/azul para el usuario, a la izquierda con vidrio para
/// Edecán.
struct BurbujaMensaje: View {
    let mensaje: ChatViewModel.Mensaje
    let artefactoDescargandoId: String?
    let onAbrirArtefacto: (ArtifactRef) -> Void

    var body: some View {
        HStack {
            if mensaje.rol == .usuario { Spacer(minLength: 40) }

            VStack(alignment: .leading, spacing: 9) {
                if mensaje.texto.isEmpty && mensaje.enProgreso {
                    ProgressView()
                        .tint(mensaje.rol == .usuario ? .white : .primary)
                } else if !mensaje.texto.isEmpty {
                    Text(mensaje.texto)
                        .textSelection(.enabled)
                }

                if mensaje.rol == .asistente && !mensaje.artefactos.isEmpty {
                    ForEach(mensaje.artefactos) { artefacto in
                        Button {
                            onAbrirArtefacto(artefacto)
                        } label: {
                            HStack(spacing: 7) {
                                if artefactoDescargandoId == artefacto.fileId {
                                    ProgressView().controlSize(.small)
                                } else {
                                    Image(systemName: "arrow.down.doc")
                                }
                                Text(artefacto.filename)
                                    .lineLimit(1)
                                Spacer(minLength: 0)
                                Image(systemName: "square.and.arrow.up")
                                    .font(.caption)
                            }
                            .font(.footnote.weight(.medium))
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(.horizontal, 10)
                            .padding(.vertical, 8)
                            .background(EdecanTheme.morado.opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
                        }
                        .buttonStyle(.plain)
                        .disabled(artefactoDescargandoId != nil)
                        .accessibilityLabel("Descargar y compartir \(artefacto.filename)")
                    }
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
