import Foundation
import SwiftUI
import EdecanKit

/// Una burbuja de mensaje en ``ChatView``: alineada a la derecha con el
/// degradado morado/azul para el usuario, a la izquierda con vidrio para
/// Edecán.
struct BurbujaMensaje: View {
    let mensaje: ChatViewModel.Mensaje
    let client: APIClient?
    let artefactoDescargandoId: String?
    let onAbrirArtefacto: (ArtifactRef) -> Void
    let onAction: (ChatAction) -> Void
    let onRetry: (String) -> Void

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

                if !mensaje.adjuntos.isEmpty {
                    ForEach(mensaje.adjuntos) { attachment in
                        Label(attachment.filename, systemImage: iconoAdjunto(attachment.mime))
                            .font(.caption.weight(.medium))
                            .lineLimit(1)
                            .padding(.horizontal, 9)
                            .padding(.vertical, 6)
                            .background(
                                mensaje.rol == .usuario ? Color.white.opacity(0.16) : EdecanTheme.morado.opacity(0.10),
                                in: Capsule()
                            )
                    }
                }

                if mensaje.rol == .asistente && !mensaje.bloques.isEmpty {
                    ForEach(Array(mensaje.bloques.enumerated()), id: \.offset) { _, bloque in
                        BloqueChatView(bloque: bloque, client: client, onAction: onAction)
                    }
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

                if mensaje.rol == .usuario && mensaje.falloEnvio {
                    Button {
                        onRetry(mensaje.id)
                    } label: {
                        Label("Reintentar", systemImage: "arrow.clockwise")
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.bordered)
                    .tint(.white)
                    .accessibilityHint("Vuelve a enviar esta misma solicitud")
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

    private func iconoAdjunto(_ mime: String?) -> String {
        let value = mime?.lowercased() ?? ""
        if value.hasPrefix("image/") { return "photo" }
        if value.hasPrefix("video/") { return "video" }
        if value.hasPrefix("audio/") { return "waveform" }
        if value == "application/pdf" { return "doc.richtext" }
        return "doc"
    }
}

/// Render allowlisted de los bloques del contrato v1. Ningun bloque decide
/// navegacion por su cuenta: todas las acciones vuelven a ``ChatView`` para
/// aplicar validacion de URL, pantalla o prefill.
private struct BloqueChatView: View {
    let bloque: ChatBlock
    let client: APIClient?
    let onAction: (ChatAction) -> Void

    @ViewBuilder
    var body: some View {
        switch bloque {
        case .media(let media):
            VistaPreviaMultimedia(bloque: media, client: client)
        case .linkPreview(let link):
            LinkPreviewCard(link: link, onAction: onAction)
        case .flight(let flight):
            FlightCard(flight: flight, onAction: onAction)
        case .hotel(let hotel):
            HotelCard(hotel: hotel, onAction: onAction)
        case .unsupported(_, let fallbackText):
            if let fallbackText, !fallbackText.isEmpty {
                Text(fallbackText)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 12))
            }
        }
    }
}

private struct LinkPreviewCard: View {
    let link: LinkPreviewBlock
    let onAction: (ChatAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            HStack(spacing: 7) {
                Image(systemName: "link")
                Text(link.siteName ?? hostVisible)
                    .lineLimit(1)
                Spacer(minLength: 4)
                FuenteBadge(mode: link.sourceMode)
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(.secondary)

            Text(link.title)
                .font(.headline)
            if let description = link.description, !description.isEmpty {
                Text(description)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
            }

            if let url = ChatAction.httpURLSegura(link.url) {
                Button {
                    onAction(.openURL(id: "link-preview", label: "Abrir enlace", url: url))
                } label: {
                    Label("Abrir enlace", systemImage: "arrow.up.right")
                }
                .buttonStyle(.bordered)
                .accessibilityHint("Se abre fuera de Edecan")
            }
            AccionesBloque(actions: link.actions, onAction: onAction)
        }
        .tarjetaBloque()
    }

    private var hostVisible: String {
        ChatAction.httpURLSegura(link.url)?.host ?? "Enlace"
    }
}

private struct FlightCard: View {
    let flight: FlightCardBlock
    let onAction: (ChatAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(flight.airline, systemImage: "airplane")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                Spacer(minLength: 6)
                FuenteBadge(mode: flight.sourceMode)
            }

            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(flight.origin.uppercased())
                Image(systemName: "arrow.right")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                Text(flight.destination.uppercased())
                Spacer()
                VStack(alignment: .trailing, spacing: 1) {
                    Text("\(flight.currency) \(flight.price)")
                        .font(.headline)
                    Text(flight.stops == 0 ? "Directo" : "\(flight.stops) escala\(flight.stops == 1 ? "" : "s")")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
            }
            .font(.title3.weight(.bold))

            detalleViaje(inicio: flight.departure, fin: flight.arrival)
            metadatos(provider: flight.provider, taxes: flight.taxes, cancellation: flight.cancellation)
            AccionesBloque(actions: flight.actions, onAction: onAction)
        }
        .tarjetaBloque()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Vuelo de \(flight.origin) a \(flight.destination) con \(flight.airline), \(flight.currency) \(flight.price)")
    }
}

private struct HotelCard: View {
    let hotel: HotelCardBlock
    let onAction: (ChatAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(hotel.name, systemImage: "building.2.fill")
                    .font(.headline)
                    .lineLimit(2)
                Spacer(minLength: 6)
                FuenteBadge(mode: hotel.sourceMode)
            }
            HStack(alignment: .firstTextBaseline) {
                Text(hotel.city)
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                if let rating = hotel.rating, !rating.isEmpty {
                    Label(rating, systemImage: "star.fill")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(.orange)
                }
                Spacer()
                Text("\(hotel.currency) \(hotel.price)")
                    .font(.headline)
            }
            detalleViaje(inicio: hotel.checkin, fin: hotel.checkout)
            metadatos(provider: hotel.provider, taxes: hotel.taxes, cancellation: hotel.cancellation)
            AccionesBloque(actions: hotel.actions, onAction: onAction)
        }
        .tarjetaBloque()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Hotel \(hotel.name) en \(hotel.city), \(hotel.currency) \(hotel.price)")
    }
}

private struct AccionesBloque: View {
    let actions: [ChatAction]
    let onAction: (ChatAction) -> Void

    private var soportadas: [ChatAction] { actions.filter(\.isSupported) }

    var body: some View {
        if !soportadas.isEmpty {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { botones }
                VStack(alignment: .leading, spacing: 8) { botones }
            }
        }
    }

    @ViewBuilder
    private var botones: some View {
        ForEach(Array(soportadas.enumerated()), id: \.offset) { _, action in
            Button {
                onAction(action)
            } label: {
                Label(action.label ?? "Continuar", systemImage: icono(action))
                    .lineLimit(1)
            }
            .buttonStyle(.borderedProminent)
            .tint(EdecanTheme.morado)
        }
    }

    private func icono(_ action: ChatAction) -> String {
        switch action {
        case .openURL: "arrow.up.right"
        case .openScreen: "rectangle.portrait.and.arrow.right"
        case .prefillMessage: "text.cursor"
        case .unsupported: "questionmark"
        }
    }
}

private struct FuenteBadge: View {
    let mode: ChatSourceMode

    var body: some View {
        Text(etiqueta)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
    }

    private var etiqueta: String {
        switch mode {
        case .live: "En vivo"
        case .demo: "Demostracion"
        case .unknown: "Fuente externa"
        }
    }

    private var color: Color {
        switch mode {
        case .live: .green
        case .demo: .orange
        case .unknown: .secondary
        }
    }
}

@ViewBuilder
private func detalleViaje(inicio: String?, fin: String?) -> some View {
    if inicio != nil || fin != nil {
        HStack(spacing: 8) {
            if let inicio { Label(fechaVisible(inicio), systemImage: "calendar") }
            if let fin {
                Image(systemName: "arrow.right").font(.caption2)
                Text(fechaVisible(fin))
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
    }
}

@ViewBuilder
private func metadatos(provider: String?, taxes: String?, cancellation: String?) -> some View {
    if provider != nil || taxes != nil || cancellation != nil {
        VStack(alignment: .leading, spacing: 3) {
            if let provider, !provider.isEmpty { Text("Fuente: \(provider)") }
            if let taxes, !taxes.isEmpty { Text("Impuestos: \(taxes)") }
            if let cancellation, !cancellation.isEmpty { Text(cancellation).lineLimit(2) }
        }
        .font(.caption2)
        .foregroundStyle(.secondary)
    }
}

private func fechaVisible(_ raw: String) -> String {
    let formatter = ISO8601DateFormatter()
    guard let date = formatter.date(from: raw) else { return raw }
    return date.formatted(date: .abbreviated, time: .shortened)
}

private extension View {
    func tarjetaBloque() -> some View {
        self
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(EdecanTheme.morado.opacity(0.08), in: RoundedRectangle(cornerRadius: 14))
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
