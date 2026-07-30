import Foundation
import ImageIO
import SwiftUI
import EdecanKit
import UIKit

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
    /// Manda una respuesta como mensaje del usuario (modal de `QuestionBlock`).
    let onResponder: (String) -> Void
    /// Texto del primer mensaje del usuario POSTERIOR a este, o `nil` si
    /// todavía no hay ninguno. Lo calcula ``ChatViewModel/respuestasAPreguntas``
    /// sobre el hilo completo; acá solo se pasa hacia las tarjetas de pregunta,
    /// que son las que necesitan saber si ya se contestaron.
    let respuestaPosterior: String?

    var body: some View {
        HStack {
            if mensaje.rol == .usuario { Spacer(minLength: 40) }

            VStack(alignment: .leading, spacing: 9) {
                // Orden deliberado: acuse breve -> resumen de trabajo plegado -> respuesta.
                // Antes el bloque de herramientas iba PRIMERO y toda la narración se pegaba
                // en un solo párrafo, así que la respuesta real quedaba enterrada al final
                // de un muro de texto.
                if !mensaje.textoApertura.isEmpty {
                    Text(textoMarkdown(mensaje.textoApertura, enBurbujaPropia: mensaje.rol == .usuario))
                }

                if let trabajo = mensaje.trabajo {
                    ProgresoTrabajoView(trabajo: trabajo)
                }

                if mensaje.texto.isEmpty && mensaje.enProgreso && mensaje.trabajo == nil {
                    ProgressView()
                        .tint(mensaje.rol == .usuario ? .white : .primary)
                } else if !mensaje.texto.isEmpty {
                    Text(textoMarkdown(mensaje.texto, enBurbujaPropia: mensaje.rol == .usuario))
                }

                if !mensaje.adjuntos.isEmpty {
                    ForEach(mensaje.adjuntos) { attachment in
                        if attachment.mime?.lowercased().hasPrefix("image/") == true {
                            VistaPreviaImagenAdjunta(attachment: attachment, client: client)
                        } else {
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
                }

                if mensaje.rol == .asistente && !mensaje.bloques.isEmpty {
                    BloquesChatView(
                        bloques: mensaje.bloques,
                        client: client,
                        onAction: onAction,
                        onResponder: onResponder,
                        respuestaPosterior: respuestaPosterior
                    )
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
                                    Image(systemName: "doc.text.magnifyingglass")
                                }
                                Text(artefacto.filename)
                                    .lineLimit(1)
                                Spacer(minLength: 0)
                                Image(systemName: "eye")
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
            // Copiar cuelga de la BURBUJA entera, no del `Text` de adentro. Antes
            // colgaba del texto y dejaba de responder en cuanto el mensaje traía
            // un enlace: el gesto largo se lo queda la interacción del enlace y
            // el menú no llega a salir. Además, mantener pulsado el relleno o el
            // saludo de apertura tampoco ofrecía nada. Desde acá cubre todo lo
            // que se ve como una burbuja, que es lo que la persona aprieta.
            .contextMenu {
                Button {
                    // `Text` con selección nativa puede poner una representación
                    // RTFD en el portapapeles. Esta ruta escribe solo texto plano
                    // y quita los marcadores Markdown ya renderizados.
                    UIPasteboard.general.string = textoPlanoParaCopiar(
                        [mensaje.textoApertura, mensaje.texto]
                            .filter { !$0.isEmpty }
                            .joined(separator: "\n\n")
                    )
                } label: {
                    Label("Copiar texto", systemImage: "doc.on.doc")
                }
            }
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

private struct VistaPreviaImagenAdjunta: View {
    let attachment: ChatAttachment
    let client: APIClient?
    @State private var image: UIImage?
    @State private var cargaFinalizada = false

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
            } else if cargaFinalizada {
                ZStack {
                    Color.white.opacity(0.10)
                    Image(systemName: "photo.badge.exclamationmark")
                        .font(.title2)
                        .foregroundStyle(.secondary)
                }
            } else {
                ZStack {
                    Color.white.opacity(0.10)
                    ProgressView().tint(.white)
                }
            }
        }
        .frame(minWidth: 180, maxWidth: 280, minHeight: 130, maxHeight: 220)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        .overlay(alignment: .bottomLeading) {
            Text(attachment.filename)
                .font(.caption2.weight(.medium))
                .lineLimit(1)
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(.ultraThinMaterial, in: Capsule())
                .padding(8)
        }
        .task(id: attachment.fileId) {
            guard image == nil, !cargaFinalizada, let client else { return }
            let artifact = ArtifactRef(
                fileId: attachment.fileId,
                filename: attachment.filename,
                mime: attachment.mime
            )
            guard let downloaded = try? await client.descargarArtefacto(artifact) else {
                cargaFinalizada = true
                return
            }
            let thumbnail = await Task.detached(priority: .userInitiated) {
                cgImagePreviaAcotada(data: downloaded.data, maxPixelSize: 1_280)
            }.value
            guard !Task.isCancelled else { return }
            image = thumbnail.map(UIImage.init(cgImage:))
            cargaFinalizada = true
        }
        .accessibilityLabel("Imagen adjunta: \(attachment.filename)")
    }
}

/// ImageIO evita expandir una foto de cámara de decenas de megapíxeles antes
/// de mostrarla. Se devuelve `CGImage`, que puede cruzar el trabajo en
/// background; `UIImage` se crea de nuevo dentro del actor principal.
func cgImagePreviaAcotada(data: Data, maxPixelSize: CGFloat) -> CGImage? {
    guard let source = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
    return cgImagePreviaAcotada(source: source, maxPixelSize: maxPixelSize)
}

func cgImagePreviaAcotada(url: URL, maxPixelSize: CGFloat) -> CGImage? {
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return cgImagePreviaAcotada(source: source, maxPixelSize: maxPixelSize)
}

private func cgImagePreviaAcotada(source: CGImageSource, maxPixelSize: CGFloat) -> CGImage? {
    let options: [CFString: Any] = [
        kCGImageSourceCreateThumbnailFromImageAlways: true,
        kCGImageSourceCreateThumbnailWithTransform: true,
        kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
        kCGImageSourceShouldCacheImmediately: true,
    ]
    return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
}

/// Progreso verificable de una ejecución larga. Solo muestra eventos
/// operativos públicos (`tool_start/progress/end`), nunca el razonamiento
/// privado del modelo.
private struct ProgresoTrabajoView: View {
    let trabajo: ChatViewModel.Trabajo

    /// Plegado por defecto UNA VEZ TERMINADO: el detalle de las herramientas es auditoría,
    /// no la respuesta. Mientras trabaja se queda abierto para que se vea el avance en vivo.
    @State private var desplegadoManualmente: Bool?

    private var desplegado: Bool { desplegadoManualmente ?? trabajo.estaActivo }

    /// "Ejecutó 3 comandos" — el resumen de una línea del modo plegado.
    private var resumenCorto: String {
        let n = trabajo.pasos.count
        if n == 0 { return trabajo.tituloEstado }
        return n == 1 ? "Ejecutó 1 comando" : "Ejecutó \(n) comandos"
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { _ in
            VStack(alignment: .leading, spacing: 10) {
                Button {
                    withAnimation(.easeInOut(duration: 0.18)) {
                        desplegadoManualmente = !desplegado
                    }
                } label: {
                    HStack(spacing: 8) {
                        if trabajo.estaActivo {
                            ProgressView().controlSize(.small)
                        } else {
                            Image(systemName: "checkmark.circle.fill")
                                .foregroundStyle(.green)
                                .font(.caption)
                        }
                        Text(desplegado ? trabajo.tituloEstado : resumenCorto)
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        Text(duracion(trabajo.segundosTranscurridos))
                            .font(.caption2.monospacedDigit())
                            .foregroundStyle(.secondary)
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.secondary)
                            .rotationEffect(.degrees(desplegado ? 90 : 0))
                    }
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(resumenCorto)
                .accessibilityHint(desplegado ? "Toca para ocultar el detalle" : "Toca para ver qué hizo")

                if desplegado {

                ForEach(trabajo.pasos) { paso in
                    HStack(alignment: .top, spacing: 9) {
                        icono(paso.estado)
                            .frame(width: 16, height: 18)
                        VStack(alignment: .leading, spacing: 2) {
                            Text(nombreVisible(paso.nombre))
                                .font(.caption.weight(.semibold))
                            if let detalle = paso.detalle, !detalle.isEmpty {
                                Text(detalle)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .lineLimit(paso.estado == .ejecutando ? 1 : 2)
                            }
                        }
                    }
                }
                if let error = trabajo.errorMision, !error.isEmpty {
                    Text(error)
                        .font(.caption2)
                        .foregroundStyle(.red)
                }
                }
            }
            .padding(desplegado ? 12 : 8)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                EdecanTheme.morado.opacity(desplegado ? 0.08 : 0.05),
                in: RoundedRectangle(cornerRadius: desplegado ? 14 : 10, style: .continuous)
            )
        }
    }

    @ViewBuilder
    private func icono(_ estado: ChatViewModel.Trabajo.Paso.Estado) -> some View {
        switch estado {
        case .ejecutando:
            ProgressView().controlSize(.mini)
        case .completado:
            Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .error:
            Image(systemName: "exclamationmark.circle.fill").foregroundStyle(.red)
        }
    }

    private func nombreVisible(_ raw: String) -> String {
        raw.replacingOccurrences(of: "_", with: " ")
            .replacingOccurrences(of: "-", with: " ")
            .capitalized
    }

    private func duracion(_ seconds: Int) -> String {
        let minutes = seconds / 60
        let remainder = seconds % 60
        return minutes > 0 ? "\(minutes)m \(remainder)s" : "\(remainder)s"
    }
}

/// SwiftUI no interpreta Markdown cuando recibe un `String` normal. Esta
/// conversión conserva saltos y espacios del chat, pero aplica el Markdown
/// inline que una persona espera ver: negritas, cursivas, código y enlaces.
/// Si llega texto incompleto mientras se transmite, degrada al original sin
/// perder ningún carácter visible.
///
/// No es privada porque el IDE (`TextoRicoIDE`) pinta la prosa exactamente
/// igual que el chat: duplicar estas opciones de parseo sería abrir la puerta
/// a que el mismo texto se vea distinto en dos pantallas de la misma app.
func textoMarkdown(_ texto: String, enBurbujaPropia: Bool = false) -> AttributedString {
    let opciones = AttributedString.MarkdownParsingOptions(
        interpretedSyntax: .inlineOnlyPreservingWhitespace,
        failurePolicy: .returnPartiallyParsedIfPossible
    )
    var resultado = (try? AttributedString(markdown: texto, options: opciones))
        ?? AttributedString(texto)
    guard enBurbujaPropia else { return resultado }

    // En la burbuja del usuario el fondo es el degradado morado/azul, y ahí el
    // azul de enlace que pone el sistema por defecto queda casi del color del
    // fondo: la URL se lee peor que el resto del mensaje, justo al revés de lo
    // que un enlace debería hacer. Se pinta en blanco -- que es el color del
    // texto de esa burbuja -- y se subraya, porque quitarle el color sin darle
    // otra marca lo volvería indistinguible del texto normal.
    for corrida in resultado.runs where corrida.link != nil {
        resultado[corrida.range].foregroundColor = .white
        resultado[corrida.range].underlineStyle = .single
    }
    return resultado
}

private func textoPlanoParaCopiar(_ texto: String) -> String {
    String(textoMarkdown(texto).characters)
}

/// Render allowlisted de los bloques del contrato v1. Ningun bloque decide
/// navegacion por su cuenta: todas las acciones vuelven a ``ChatView`` para
/// aplicar validacion de URL, pantalla o prefill.
private struct BloquesChatView: View {
    let bloques: [ChatBlock]
    let client: APIClient?
    let onAction: (ChatAction) -> Void
    let onResponder: (String) -> Void
    let respuestaPosterior: String?

    var body: some View {
        ForEach(bloques.indices, id: \.self) { index in
            let bloque = bloques[index]
            if esBloqueViaje(bloque) {
                if index == bloques.startIndex || !esBloqueViaje(bloques[index - 1]) {
                    CarruselViajes(
                        bloques: Array(bloques[index...].prefix(while: esBloqueViaje)),
                        onAction: onAction
                    )
                }
            } else {
                BloqueChatView(
                    bloque: bloque,
                    client: client,
                    onAction: onAction,
                    onResponder: onResponder,
                    respuestaPosterior: respuestaPosterior
                )
            }
        }
    }
}

private struct CarruselViajes: View {
    let bloques: [ChatBlock]
    let onAction: (ChatAction) -> Void

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            LazyHStack(alignment: .top, spacing: 10) {
                ForEach(Array(bloques.enumerated()), id: \.offset) { _, bloque in
                    switch bloque {
                    case .flight(let flight):
                        FlightCard(flight: flight, onAction: onAction)
                            .frame(width: 252)
                    case .hotel(let hotel):
                        HotelCard(hotel: hotel, onAction: onAction)
                            .frame(width: 252)
                    default:
                        EmptyView()
                    }
                }
            }
            .scrollTargetLayout()
        }
        .scrollTargetBehavior(.viewAligned)
        .accessibilityLabel("Opciones de viaje")
    }
}

private func esBloqueViaje(_ bloque: ChatBlock) -> Bool {
    switch bloque {
    case .flight, .hotel:
        true
    default:
        false
    }
}

private struct BloqueChatView: View {
    let bloque: ChatBlock
    let client: APIClient?
    let onAction: (ChatAction) -> Void
    /// Envía una respuesta como mensaje del usuario. Lo usa el modal de
    /// ``QuestionBlock``: tocar una opción manda el mensaje de una vez, en vez
    /// de solo rellenar el campo de texto como hace `prefillMessage`.
    let onResponder: (String) -> Void
    /// Primer mensaje del usuario posterior al que trae este bloque; cierra la
    /// tarjeta de pregunta. Ver ``QuestionCardView``.
    let respuestaPosterior: String?

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
        case .socialDraft(let social):
            SocialDraftCardView(bloque: social, client: client, onAction: onAction)
        case .question(let pregunta):
            QuestionCardView(
                bloque: pregunta,
                respuestaPosterior: respuestaPosterior,
                onResponder: onResponder
            )
        case .card(let card):
            CardGenericaView(card: card, client: client, onAction: onAction, onResponder: onResponder)
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
        if let url = ChatAction.httpURLSegura(link.url) {
            Button {
                onAction(.openURL(id: "link-preview", label: "Abrir enlace", url: url))
            } label: {
                HStack(spacing: 7) {
                    Image(systemName: "globe")
                    Text(tituloVisible)
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.bold))
                }
                .font(.subheadline.weight(.medium))
                .foregroundStyle(EdecanTheme.morado)
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Abrir \(tituloVisible) en el navegador interno")
            .accessibilityHint("Abre una vista previa dentro de Edecán")
        } else {
            Text(tituloVisible)
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private var tituloVisible: String {
        let title = link.title.trimmingCharacters(in: .whitespacesAndNewlines)
        if !title.isEmpty { return title }
        return ChatAction.httpURLSegura(link.url)?.host ?? link.url
    }
}

private struct FlightCard: View {
    let flight: FlightCardBlock
    let onAction: (ChatAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Label(flight.airline, systemImage: "airplane")
                    .font(.caption.weight(.semibold))
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
                        .font(.subheadline.weight(.bold))
                    Text(flight.stops == 0 ? "Directo" : "\(flight.stops) escala\(flight.stops == 1 ? "" : "s")")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .font(.headline.weight(.bold))

            detalleViaje(inicio: flight.departure, fin: flight.arrival)
            metadatos(provider: flight.provider, taxes: flight.taxes, cancellation: flight.cancellation)
            AccionesBloque(actions: flight.actions, onAction: onAction)
        }
        .tarjetaBloque(compacta: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Vuelo de \(flight.origin) a \(flight.destination) con \(flight.airline), \(flight.currency) \(flight.price)")
    }
}

private struct HotelCard: View {
    let hotel: HotelCardBlock
    let onAction: (ChatAction) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Label(hotel.name, systemImage: "building.2.fill")
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
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
                    .font(.subheadline.weight(.bold))
            }
            detalleViaje(inicio: hotel.checkin, fin: hotel.checkout)
            metadatos(provider: hotel.provider, taxes: hotel.taxes, cancellation: hotel.cancellation)
            AccionesBloque(actions: hotel.actions, onAction: onAction)
        }
        .tarjetaBloque(compacta: true)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Hotel \(hotel.name) en \(hotel.city), \(hotel.currency) \(hotel.price)")
    }
}

/// Modal de opciones de ``QuestionBlock``: el agente pregunta en vez de adivinar.
///
/// Tocar una opción manda la respuesta como mensaje de una vez (no rellena el
/// campo de texto). Una vez respondida queda deshabilitada y con la elección
/// marcada, para que al volver a leer el hilo se vea qué se contestó y no se
/// pueda responder dos veces la misma pregunta.
///
/// "Respondida" NO lo decide la tarjeta: lo decide el hilo (``HiloDePreguntas``,
/// vía `respuestaPosterior`). Cuando el estado vivía en un `@State` de acá, la
/// promesa del párrafo anterior era falsa en dos casos que pasaron de verdad:
/// contestar escribiendo en el campo de texto no la cerraba, y al desplazar o
/// reabrir la conversación SwiftUI recreaba la vista, el `@State` volvía a
/// `false` y una pregunta contestada hacía rato quedaba tocable otra vez.
private struct QuestionCardView: View {
    let bloque: QuestionBlock
    /// Texto del primer mensaje del usuario posterior a esta tarjeta, `nil` si
    /// todavía no contestó. Incluye la burbuja optimista que aún va en vuelo,
    /// para que la tarjeta no parpadee de vuelta a tocable mientras se envía.
    let respuestaPosterior: String?
    let onResponder: (String) -> Void

    /// SOLO el marcado a medias de `multiSelect`, antes de tocar "Enviar
    /// respuesta". Es estado efímero de edición (como el texto a medio
    /// escribir en el composer), no el registro de que la pregunta se
    /// contestó: eso ya no vive en la vista.
    @State private var marcadasEnCurso: Set<String> = []

    private var estado: EstadoDePregunta {
        HiloDePreguntas.estado(de: bloque, respuesta: respuestaPosterior)
    }

    private var respondida: Bool { estado.respondida }

    private var seleccionadas: Set<String> {
        respondida ? estado.opcionesMarcadas : marcadasEnCurso
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            if (bloque.header?.isEmpty == false) || respondida {
                HStack(spacing: 8) {
                    if let header = bloque.header, !header.isEmpty {
                        Text(header.uppercased())
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(EdecanTheme.morado)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                    }
                    Spacer(minLength: 0)
                    if respondida {
                        Label("Respondida", systemImage: "checkmark.circle.fill")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(.green)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.green.opacity(0.12), in: Capsule())
                    }
                }
            }

            Text(bloque.question)
                .font(.subheadline.weight(.semibold))
                .fixedSize(horizontal: false, vertical: true)

            VStack(spacing: 8) {
                ForEach(bloque.options) { opcion in
                    Button {
                        elegir(opcion)
                    } label: {
                        filaOpcion(opcion)
                    }
                    .buttonStyle(.plain)
                    .disabled(respondida)
                }
            }

            // Con `multiSelect` no se puede mandar al primer toque: hay que
            // esperar a que termine de marcar todo lo que quiera.
            if bloque.multiSelect && !respondida {
                Button {
                    enviarSeleccionMultiple()
                } label: {
                    Text("Enviar respuesta")
                        .font(.footnote.weight(.semibold))
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 11)
                        .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                        .foregroundStyle(.white)
                }
                .buttonStyle(.plain)
                .disabled(seleccionadas.isEmpty)
                .opacity(seleccionadas.isEmpty ? 0.5 : 1)
            }

            if bloque.allowFreeText && !respondida {
                Text("O escribe tu propia respuesta abajo.")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(14)
        .tarjetaVidrio(esquina: 16)
        .overlay(
            RoundedRectangle(cornerRadius: 16, style: .continuous)
                .strokeBorder(EdecanTheme.morado.opacity(respondida ? 0.12 : 0.32), lineWidth: 1.2)
        )
    }

    /// Fila de una opción: icono de selección + etiqueta + descripción
    /// opcional. Fondo Y borde cambian juntos con la selección (no solo un
    /// tono de gris sutil) para que el estado marcado sea inequívoco en
    /// claro y oscuro, y el `minHeight` de 44pt deja un área táctil cómoda
    /// aunque la opción sea de una sola línea corta.
    private func filaOpcion(_ opcion: QuestionOption) -> some View {
        let marcada = seleccionadas.contains(opcion.id)
        return HStack(alignment: .top, spacing: 10) {
            Image(systemName: iconoDe(opcion))
                .font(.system(size: 18, weight: .semibold))
                .foregroundStyle(marcada ? EdecanTheme.morado : .secondary)
                .frame(width: 22, height: 22)
            VStack(alignment: .leading, spacing: 3) {
                Text(opcion.label)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(.primary)
                    .fixedSize(horizontal: false, vertical: true)
                if let detalle = opcion.description, !detalle.isEmpty {
                    Text(detalle)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, minHeight: 44, alignment: .leading)
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(marcada ? EdecanTheme.morado.opacity(0.16) : Color.primary.opacity(0.045))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(
                    marcada ? EdecanTheme.morado.opacity(0.55) : Color.primary.opacity(0.09),
                    lineWidth: marcada ? 1.4 : 1
                )
        )
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        // Ya respondida, las opciones no elegidas se atenúan para que la
        // marcada quede como el único punto de atención al releer el hilo.
        .opacity(respondida && !marcada ? 0.5 : 1)
    }

    private func iconoDe(_ opcion: QuestionOption) -> String {
        let marcada = seleccionadas.contains(opcion.id)
        if bloque.multiSelect {
            return marcada ? "checkmark.square.fill" : "square"
        }
        return marcada ? "largecircle.fill.circle" : "circle"
    }

    private func elegir(_ opcion: QuestionOption) {
        guard !respondida else { return }
        if bloque.multiSelect {
            if marcadasEnCurso.contains(opcion.id) {
                marcadasEnCurso.remove(opcion.id)
            } else {
                marcadasEnCurso.insert(opcion.id)
            }
            return
        }
        // No se marca nada acá: la marca la pone el mensaje que sale de este
        // toque en cuanto aparece en el hilo. Si el envío ni siquiera arranca
        // (sin sesión, o con una confirmación pendiente bloqueando el chat),
        // la tarjeta tiene que seguir tocable — y así queda.
        onResponder(opcion.messageText)
    }

    private func enviarSeleccionMultiple() {
        guard !respondida, !marcadasEnCurso.isEmpty else { return }
        // Se recorre `bloque.options` (no el Set) para respetar el orden en que
        // se mostraron: leído al revés, el mensaje resultante confundiría.
        let textos = bloque.options
            .filter { marcadasEnCurso.contains($0.id) }
            .map(\.messageText)
        onResponder(textos.joined(separator: ", "))
    }
}

/// Card de un borrador social (`crear_contenido_social`) directamente en el
/// chat. Es intencionalmente de solo lectura (a diferencia del formulario
/// editable de `LinkedInStudioView`): una card de chat es la foto de una
/// llamada de tool ya ocurrida, no un editor. Las dos variantes replican
/// exactamente `LinkedInStudioView.acciones(_:)`:
/// - `target == .personal` (o desconocido): "Copiar texto" y "Guardar
///   imagen" — ninguna publica nada.
/// - Cualquier otro `target` (una página de organización configurada por el
///   tenant): "Aprobar" llama DIRECTO a
///   `ContentStudioService.publishLinkedIn(confirmed: true, ...)`, el mismo
///   servicio que ya usa el Studio — publicación real, irreversible, y por
///   eso deshabilitada mientras corre o después de un éxito.
private struct SocialDraftCardView: View {
    let bloque: SocialDraftBlock
    let client: APIClient?
    let onAction: (ChatAction) -> Void

    @State private var previewTarget: SecurePreviewTarget?
    @State private var publicando = false
    @State private var publicado = false
    @State private var aviso: String?
    @State private var errorMensaje: String?
    @State private var imagen: UIImage?
    @State private var imagenCargada = false

    private var draft: SocialContentDraft { bloque.draft }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Label(draft.platform.label, systemImage: "square.text.square")
                    .font(.caption.weight(.semibold))
                Spacer(minLength: 6)
                if let target = draft.target {
                    Text(target.label)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(EdecanTheme.morado)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 3)
                        .background(EdecanTheme.morado.opacity(0.12), in: Capsule())
                }
            }

            if let image = draft.imageArtifact {
                Button {
                    previewTarget = .artifact(image)
                } label: {
                    imagenInline(filename: image.filename)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Ver imagen del borrador: \(image.filename)")
                .task(id: image.fileId) {
                    await cargarImagen(image)
                }
            }

            Text(draft.copy)
                .font(.subheadline)
                .fixedSize(horizontal: false, vertical: true)

            if let warning = draft.visualWarning, !warning.isEmpty {
                Text(warning)
                    .font(.caption2)
                    .foregroundStyle(.orange)
            }
            if let aviso {
                Text(aviso).font(.caption).foregroundStyle(.green)
            }
            if let errorMensaje {
                Text(errorMensaje).font(.caption).foregroundStyle(.red)
            }

            acciones
        }
        .tarjetaBloque()
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Borrador de \(draft.platform.label): \(draft.copy)")
        .sheet(item: $previewTarget) { target in
            SecurePreviewSheet(target: target, client: client)
        }
        // A propósito SIN `.onDisappear { tarea?.cancel() }`: esta card vive
        // en el `LazyVStack` del chat (`ChatView`), así que un simple scroll
        // la hace desaparecer/reaparecer. "Aprobar" dispara una publicación
        // real e irreversible en LinkedIn — cancelarla a mitad de vuelo por
        // un scroll dejaría el estado ambiguo (¿se publicó o no?) y podría
        // inducir una publicación doble en el siguiente intento. El `Task`
        // sigue corriendo hasta terminar aunque la vista se recicle.
    }

    @ViewBuilder
    private func imagenInline(filename: String) -> some View {
        Group {
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .background(Color.black.opacity(0.04))
            } else if imagenCargada {
                ZStack {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(EdecanTheme.morado.opacity(0.08))
                    VStack(spacing: 8) {
                        Image(systemName: "photo.badge.exclamationmark")
                            .font(.title2)
                            .foregroundStyle(.secondary)
                        Text("No se pudo cargar la imagen")
                            .font(.caption.weight(.medium))
                            .foregroundStyle(.secondary)
                    }
                    .padding()
                }
                .frame(maxWidth: .infinity, minHeight: 180)
            } else {
                ZStack {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(EdecanTheme.morado.opacity(0.08))
                    ProgressView()
                }
                .frame(maxWidth: .infinity, minHeight: 220)
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(alignment: .bottomTrailing) {
            Label("Ver", systemImage: "eye")
                .font(.caption2.weight(.semibold))
                .padding(.horizontal, 8)
                .padding(.vertical, 5)
                .background(.ultraThinMaterial, in: Capsule())
                .padding(8)
        }
        .accessibilityLabel("Imagen del borrador: \(filename)")
    }

    @ViewBuilder
    private var acciones: some View {
        if let target = draft.target, !target.isPersonal {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { botonesDeOrganizacion }
                VStack(alignment: .leading, spacing: 8) { botonesDeOrganizacion }
            }
        } else {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 8) { botonesLectura }
                VStack(alignment: .leading, spacing: 8) { botonesLectura }
            }
        }
    }

    @ViewBuilder
    private var botonesDeOrganizacion: some View {
        Button(action: aprobar) {
            Label(
                publicado ? "Publicado" : (publicando ? "Publicando…" : "Aprobar"),
                systemImage: publicado ? "checkmark.seal.fill" : "checkmark.circle.fill"
            )
            .frame(maxWidth: .infinity)
        }
        .buttonStyle(.borderedProminent)
        .tint(.green)
        .disabled(publicando || publicado)

        Button {
            onAction(
                .prefillMessage(
                    id: "social-draft-change-\(draft.imageArtifact?.fileId ?? draft.platform.rawValue)",
                    label: "Hacer cambios",
                    message: "Haz cambios a este borrador de LinkedIn. Mantén el mismo destino, conserva lo bueno, cambia esto: "
                )
            )
        } label: {
            Label("Hacer cambios", systemImage: "wand.and.stars")
        }
        .buttonStyle(.bordered)
        .disabled(publicando)
    }

    @ViewBuilder
    private var botonesLectura: some View {
        Button {
            UIPasteboard.general.string = draft.copy
            aviso = "Texto copiado."
            errorMensaje = nil
        } label: {
            Label("Copiar texto", systemImage: "doc.on.doc")
        }
        .buttonStyle(.borderedProminent)
        .tint(EdecanTheme.morado)

        if let image = draft.imageArtifact {
            Button {
                guardarImagen(image)
            } label: {
                Label("Guardar imagen", systemImage: "photo")
            }
            .buttonStyle(.bordered)
        }
    }

    private func cargarImagen(_ artifact: ArtifactRef) async {
        guard imagen == nil, !imagenCargada, let client else { return }
        do {
            let downloaded = try await client.descargarArtefacto(artifact)
            let thumbnail = await Task.detached(priority: .userInitiated) {
                cgImagePreviaAcotada(data: downloaded.data, maxPixelSize: 1_536)
            }.value
            guard !Task.isCancelled else { return }
            imagen = thumbnail.map(UIImage.init(cgImage:))
            imagenCargada = true
        } catch {
            guard !Task.isCancelled else { return }
            imagenCargada = true
        }
    }

    private func guardarImagen(_ artifact: ArtifactRef) {
        Task { @MainActor in
            if imagen == nil {
                await cargarImagen(artifact)
            }
            guard let imagen else {
                errorMensaje = "No pude guardar la imagen porque no se cargó."
                return
            }
            UIImageWriteToSavedPhotosAlbum(imagen, nil, nil, nil)
            aviso = "Imagen guardada en Fotos."
            errorMensaje = nil
        }
    }

    private func aprobar() {
        // El botón ya se deshabilita mientras `publicando`/`publicado`, así
        // que no hace falta cancelar un intento anterior: solo puede haber
        // una publicación en vuelo por card.
        guard !publicando, !publicado else { return }
        guard let client else {
            errorMensaje = "Inicia sesión para publicar."
            return
        }
        // Publica exactamente al destino con el que se creó este borrador
        // (`draft.target`, un id abierto configurado por el tenant) — nunca
        // un destino fijo en el código. Esta rama solo corre cuando ya se
        // confirmó que `draft.target` no es `personal` (ver `acciones`).
        guard let target = draft.target else { return }
        publicando = true
        errorMensaje = nil
        aviso = nil
        Task { @MainActor in
            do {
                _ = try await ContentStudioService(client: client).publishLinkedIn(
                    SocialContentPublishRequest(
                        target: target,
                        text: draft.copy,
                        imageFileId: draft.imageArtifact?.fileId,
                        altText: draft.altText,
                        confirmed: true
                    )
                )
                aviso = "Publicado en LinkedIn (\(target.label))."
                publicado = true
            } catch is CancellationError {
                publicando = false
                return
            } catch {
                errorMensaje = error.localizedDescription
            }
            publicando = false
        }
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
            .controlSize(.small)
            .tint(EdecanTheme.morado)
        }
    }

    private func icono(_ action: ChatAction) -> String {
        switch action {
        case .openURL: "arrow.up.right"
        case .openScreen: "rectangle.portrait.and.arrow.right"
        case .prefillMessage: "text.cursor"
        case .copyText: "doc.on.doc"
        case .saveArtifact: "square.and.arrow.down"
        case .sendMessage: "paperplane"
        case .approveDraft: "checkmark.seal"
        case .openConversation: "bubble.left.and.bubble.right"
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
    func tarjetaBloque(compacta: Bool = false) -> some View {
        self
            .padding(compacta ? 10 : 12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                EdecanTheme.morado.opacity(0.08),
                in: RoundedRectangle(cornerRadius: compacta ? 12 : 14)
            )
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
