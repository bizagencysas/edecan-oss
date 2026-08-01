import SwiftUI
import EdecanKit
import UIKit

/// Renderer genérico de ``GenericCardBlock`` — MVP de Server-Driven UI del
/// chat (ver `/private/tmp/sdui_plan_mvp.json`). El backend decide QUÉ se
/// compone y con qué ROL semántico (``NodoCard``); esta vista decide CÓMO se
/// ve cada rol, mapeando a ``EdecanTheme`` y a fuentes/colores nativos. Un
/// nodo `.desconocido` simplemente no pinta nada (nivel 1 de forward-compat):
/// un stack con 5 hijos donde 1 es desconocido sigue mostrando los otros 4.
///
/// `copy_text` y `save_artifact` se resuelven AQUÍ MISMO, sin pasar por
/// `onAction` — igual que ya hace `SocialDraftCardView` con "Copiar texto" y
/// "Guardar imagen" (`BurbujaMensaje.swift`): son acciones locales, sin
/// efectos de navegación, así que no hace falta subir hasta `ChatView`.
/// `open_url`/`open_screen`/`prefill_message`/`open_conversation` sí viajan
/// por `onAction` (necesitan estado de la app: navegador, tabs, historial).
/// `send_message` usa `onResponder` -- el mismo camino que ya manda la
/// respuesta de ``QuestionBlock`` como mensaje real y visible del usuario.
/// `approve_draft` pide confirmación aquí y despacha por `onAction`: la
/// publicación real necesita `ContentStudioService`, que vive en la capa de
/// `ChatView` (ver la DESVIACIÓN documentada en `ejecutarAccion`).
struct CardGenericaView: View {
    let card: GenericCardBlock
    let client: APIClient?
    let onAction: (ChatAction) -> Void
    let onResponder: (String) -> Void

    var body: some View {
        NodoCardView(
            nodo: .stack(card.raiz),
            client: client,
            artefactos: artefactosDeImagen(.stack(card.raiz)),
            onAction: onAction,
            onResponder: onResponder
        )
        .accessibilityElement(children: .contain)
    }

    /// Recolecta, por `fileId`, los artifacts de TODAS las imágenes de la
    /// card -- así un botón `save_artifact` (que solo trae `fileId`) puede
    /// resolver filename/mime reales en vez de un valor genérico.
    private func artefactosDeImagen(_ nodo: NodoCard) -> [String: ArtifactRef] {
        switch nodo {
        case .imagen(let imagen):
            return [imagen.artifact.fileId: imagen.artifact]
        case .stack(let contenedor):
            return contenedor.hijos.reduce(into: [:]) { acumulado, hijo in
                acumulado.merge(artefactosDeImagen(hijo)) { primero, _ in primero }
            }
        default:
            return [:]
        }
    }
}

/// Pinta UN nodo del árbol. `.desconocido` es ``EmptyView`` a propósito: el
/// nivel 1 de forward-compat es saltar el nodo, no mostrar un hueco visible.
private struct NodoCardView: View {
    let nodo: NodoCard
    let client: APIClient?
    let artefactos: [String: ArtifactRef]
    let onAction: (ChatAction) -> Void
    let onResponder: (String) -> Void

    @ViewBuilder
    var body: some View {
        switch nodo {
        case .stack(let contenedor):
            StackNodoView(
                contenedor: contenedor,
                client: client,
                artefactos: artefactos,
                onAction: onAction,
                onResponder: onResponder
            )
        case .texto(let texto):
            TextoNodoView(nodo: texto)
        case .imagen(let imagen):
            ImagenNodoView(nodo: imagen, client: client)
        case .boton(let boton):
            BotonNodoView(nodo: boton, client: client, artefactos: artefactos, onAction: onAction, onResponder: onResponder)
        case .badge(let badge):
            BadgeNodoView(nodo: badge)
        case .divisor:
            Divider()
        case .desconocido:
            EmptyView()
        }
    }
}

/// El VStack/HStack que hoy está horneado en Swift, como dato: `eje`,
/// `espaciado`, `relleno`, `fondo` y `alineacion` son ROLES semánticos, nunca
/// CSS/px/hex -- esta vista es la única que sabe a qué valores concretos
/// mapea cada uno.
private struct StackNodoView: View {
    let contenedor: StackNode
    let client: APIClient?
    let artefactos: [String: ArtifactRef]
    let onAction: (ChatAction) -> Void
    let onResponder: (String) -> Void

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        Group {
            if contenedor.eje == .horizontal {
                HStack(alignment: alineacionVertical, spacing: espaciado) { hijos }
            } else {
                VStack(alignment: alineacionHorizontal, spacing: espaciado) { hijos }
            }
        }
        .padding(relleno)
        .frame(maxWidth: .infinity, alignment: alineacionFrame)
        .background(fondo, in: RoundedRectangle(cornerRadius: relleno > 0 ? 14 : 0, style: .continuous))
    }

    @ViewBuilder
    private var hijos: some View {
        ForEach(Array(contenedor.hijos.enumerated()), id: \.offset) { _, hijo in
            NodoCardView(nodo: hijo, client: client, artefactos: artefactos, onAction: onAction, onResponder: onResponder)
        }
    }

    private var espaciado: CGFloat {
        switch contenedor.espaciado {
        case .xs: 4
        case .s: 8
        case .m: 12
        case .l: 18
        }
    }

    private var relleno: CGFloat {
        switch contenedor.relleno {
        case .ninguno: 0
        case .compacto: 8
        case .tarjeta: 14
        }
    }

    private var fondo: Color {
        switch contenedor.fondo {
        case .ninguno: .clear
        case .tarjeta: EdecanTheme.fondoTarjeta(colorScheme)
        case .acentoSuave: EdecanTheme.morado.opacity(0.08)
        case .advertenciaSuave: Color.orange.opacity(0.12)
        case .exitoSuave: Color.green.opacity(0.12)
        }
    }

    private var alineacionHorizontal: HorizontalAlignment {
        switch contenedor.alineacion {
        case .inicio: .leading
        case .centro: .center
        case .fin: .trailing
        }
    }

    private var alineacionVertical: VerticalAlignment {
        switch contenedor.alineacion {
        case .inicio: .top
        case .centro: .center
        case .fin: .bottom
        }
    }

    private var alineacionFrame: Alignment {
        switch contenedor.alineacion {
        case .inicio: .leading
        case .centro: .center
        case .fin: .trailing
        }
    }
}

/// Resuelve la clase de bug "titular muy grande": `rol` (no un tamaño en
/// puntos) es lo que el backend elige de un enum cerrado.
private struct TextoNodoView: View {
    let nodo: TextoNode

    var body: some View {
        Text(nodo.contenido)
            .font(fuente)
            .fontWeight(nodo.enfasis == .fuerte ? .semibold : nil)
            .foregroundStyle(color)
            .lineLimit(nodo.maxLineas)
            .fixedSize(horizontal: false, vertical: true)
    }

    private var fuente: Font {
        switch nodo.rol {
        case .titulo: .headline
        case .subtitulo: .subheadline
        case .cuerpo: .body
        case .pie: .footnote
        case .etiqueta: .caption
        }
    }

    private var color: Color {
        switch nodo.color {
        case .primario: .primary
        case .secundario: .secondary
        case .acento: EdecanTheme.morado
        case .advertencia: .orange
        case .exito: .green
        }
    }
}

/// Imagen dentro de una card compuesta (hoy `MediaBlock` solo existe suelto).
/// Reusa el mismo par descarga+miniatura que ``VistaPreviaImagenAdjunta`` y
/// `SocialDraftCardView` (`client.descargarArtefacto` +
/// `cgImagePreviaAcotada`, ambos en `BurbujaMensaje.swift`) en vez de
/// `VistaPreviaMultimedia` en sí: ese componente está tipado a `MediaBlock`
/// (trae `mediaKind`/`caption`, que `ImagenNode` no tiene), así que envolver
/// uno dentro del otro sería más artificial que compartir el par de
/// funciones que de verdad hacen el trabajo de red y decodificación.
private struct ImagenNodoView: View {
    let nodo: ImagenNode
    let client: APIClient?

    @State private var imagen: UIImage?
    @State private var cargaFinalizada = false

    var body: some View {
        Group {
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .aspectRatio(proporcion, contentMode: .fill)
            } else if cargaFinalizada {
                ZStack {
                    Color.primary.opacity(0.06)
                    Image(systemName: "photo.badge.exclamationmark")
                        .foregroundStyle(.secondary)
                }
                .aspectRatio(proporcion ?? 16.0 / 9.0, contentMode: .fit)
            } else {
                ZStack {
                    Color.primary.opacity(0.06)
                    ProgressView()
                }
                .aspectRatio(proporcion ?? 16.0 / 9.0, contentMode: .fit)
            }
        }
        .frame(maxWidth: .infinity)
        .clipShape(RoundedRectangle(cornerRadius: esquinas, style: .continuous))
        .task(id: nodo.artifact.fileId) {
            guard imagen == nil, !cargaFinalizada, let client else {
                cargaFinalizada = true
                return
            }
            guard let descargado = try? await client.descargarArtefacto(nodo.artifact) else {
                cargaFinalizada = true
                return
            }
            let miniatura = await Task.detached(priority: .userInitiated) {
                cgImagePreviaAcotada(data: descargado.data, maxPixelSize: 1_280)
            }.value
            guard !Task.isCancelled else { return }
            imagen = miniatura.map(UIImage.init(cgImage:))
            cargaFinalizada = true
        }
        .accessibilityLabel(nodo.alt.isEmpty ? "Imagen" : nodo.alt)
    }

    /// `nil` para `.libre`: sin proporción fija, se deja crecer con el
    /// contenido (`scaledToFit` implícito del `Group` de arriba).
    private var proporcion: CGFloat? {
        switch nodo.aspecto {
        case .cuadrada: 1
        case .panoramica: 16.0 / 9.0
        case .libre: nil
        }
    }

    private var esquinas: CGFloat {
        switch nodo.esquinas {
        case .ninguna: 0
        case .s: 8
        case .m: 16
        }
    }
}

/// El badge demo/live verde/naranja/gris que hoy está fijo en Swift
/// (``FuenteBadge`` en `BurbujaMensaje.swift`), como dato.
private struct BadgeNodoView: View {
    let nodo: BadgeNode

    var body: some View {
        Text(nodo.contenido)
            .font(.caption2.weight(.semibold))
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
    }

    private var color: Color {
        switch nodo.tono {
        case .neutro: .secondary
        case .acento: EdecanTheme.morado
        case .advertencia: .orange
        case .exito: .green
        }
    }
}

/// Convierte en dato lo que hoy está cableado a mano en `SocialDraftCardView`.
/// Una acción `.unsupported` (nivel 1 de forward-compat) hace que el botón NO
/// se pinte -- ni un placeholder, ni un botón deshabilitado: nada.
private struct BotonNodoView: View {
    let nodo: BotonNode
    let client: APIClient?
    let artefactos: [String: ArtifactRef]
    let onAction: (ChatAction) -> Void
    let onResponder: (String) -> Void

    @State private var aviso: String?
    @State private var trabajando = false
    @State private var confirmandoAprobar = false

    var body: some View {
        if case .unsupported = nodo.accion {
            EmptyView()
        } else {
            VStack(alignment: .leading, spacing: 4) {
                Button(action: manejarToque) {
                    HStack(spacing: 6) {
                        if trabajando {
                            ProgressView().controlSize(.small).tint(colorTexto)
                        }
                        Text(nodo.accion.label ?? "Continuar")
                            .font(.footnote.weight(.semibold))
                            .lineLimit(1)
                    }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 8)
                    .foregroundStyle(colorTexto)
                    .background(fondo, in: Capsule())
                    .overlay(borde)
                }
                .buttonStyle(.plain)
                .disabled(trabajando)
                .accessibilityLabel(nodo.accion.label ?? "Continuar")

                if let aviso {
                    Text(aviso)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .confirmationDialog(
                "¿Aprobar y publicar este borrador?",
                isPresented: $confirmandoAprobar,
                titleVisibility: .visible
            ) {
                Button("Aprobar") { onAction(nodo.accion) }
                Button("Cancelar", role: .cancel) {}
            }
        }
    }

    private var fondo: Color {
        switch nodo.estilo {
        case .primario: EdecanTheme.morado
        case .secundario, .discreto: .clear
        case .destructivo: .red
        }
    }

    private var colorTexto: Color {
        switch nodo.estilo {
        case .primario, .destructivo: .white
        case .secundario: EdecanTheme.morado
        case .discreto: .secondary
        }
    }

    @ViewBuilder
    private var borde: some View {
        switch nodo.estilo {
        case .secundario:
            Capsule().strokeBorder(EdecanTheme.morado.opacity(0.5), lineWidth: 1.2)
        case .discreto:
            Capsule().strokeBorder(Color.primary.opacity(0.15), lineWidth: 1)
        case .primario, .destructivo:
            EmptyView()
        }
    }

    private func manejarToque() {
        switch nodo.accion {
        case .openURL, .openScreen, .prefillMessage, .openConversation:
            onAction(nodo.accion)
        case .copyText(_, _, let texto):
            UIPasteboard.general.string = texto
            aviso = "Copiado."
        case .saveArtifact(_, _, let fileId):
            guardarArtefacto(fileId: fileId)
        case .sendMessage(_, _, let mensaje):
            onResponder(mensaje)
        case .approveDraft:
            // Nunca se publica al primer toque: pide confirmación explícita
            // primero, igual que exige el contrato de `ApproveDraftAction`.
            confirmandoAprobar = true
        case .unsupported:
            break
        }
    }

    private func guardarArtefacto(fileId: String) {
        guard let client else {
            aviso = "Inicia sesión para guardar."
            return
        }
        // Se resuelve contra los artifacts de imagen de la MISMA card
        // (recolectados en `CardGenericaView`); si no aparece ahí (no debería
        // pasar: el backend ya valida `file_id` contra la misma tool call) se
        // cae a un nombre genérico -- la descarga solo necesita el id.
        let artifact = artefactos[fileId] ?? ArtifactRef(fileId: fileId, filename: "archivo")
        trabajando = true
        Task { @MainActor in
            defer { trabajando = false }
            guard let descargado = try? await client.descargarArtefacto(artifact) else {
                aviso = "No se pudo descargar el archivo."
                return
            }
            guard let imagen = UIImage(data: descargado.data) else {
                aviso = "El archivo no es una imagen válida."
                return
            }
            UIImageWriteToSavedPhotosAlbum(imagen, nil, nil, nil)
            aviso = "Guardado en Fotos."
        }
    }
}
