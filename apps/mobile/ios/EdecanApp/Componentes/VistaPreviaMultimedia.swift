import AVKit
import EdecanKit
import SwiftUI
import UIKit

/// Preview de media privada. Los bytes siempre se obtienen con el Bearer de
/// ``APIClient.descargarArtefacto``; nunca se construye ni se expone una URL
/// publica del archivo. Imagenes se cargan al aparecer. Video y audio esperan
/// un toque explicito para no descargar archivos grandes al recorrer el chat.
@MainActor
struct VistaPreviaMultimedia: View {
    let bloque: MediaBlock
    let client: APIClient?

    private enum Estado {
        case sinCargar
        case cargando
        case listo
        case error(String)
    }

    @State private var estado: Estado = .sinCargar
    @State private var imagen: UIImage?
    @State private var player: AVPlayer?
    @State private var estaReproduciendo = false
    @State private var archivoTemporal: URL?

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            contenido

            if let caption = bloque.caption, !caption.isEmpty {
                Text(caption)
                    .font(.footnote)
            }
            HStack(spacing: 6) {
                Image(systemName: icono)
                Text(bloque.artifact.filename)
                    .lineLimit(1)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(.quaternary.opacity(0.35), in: RoundedRectangle(cornerRadius: 14))
        .task(id: bloque.artifact.fileId) {
            limpiarRecursos()
            if bloque.mediaKind == .image {
                await cargar(expectedFileId: bloque.artifact.fileId)
            }
        }
        .onDisappear {
            limpiarRecursos()
        }
        .accessibilityElement(children: .contain)
    }

    @ViewBuilder
    private var contenido: some View {
        switch estado {
        case .sinCargar:
            botonCargar
        case .cargando:
            HStack(spacing: 9) {
                ProgressView()
                Text("Cargando vista previa…")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 90)
        case .listo:
            mediaLista
        case .error(let mensaje):
            VStack(alignment: .leading, spacing: 8) {
                Label(mensaje, systemImage: "exclamationmark.triangle")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                Button("Reintentar") { Task { await cargar(expectedFileId: bloque.artifact.fileId) } }
                    .buttonStyle(.bordered)
            }
            .frame(maxWidth: .infinity, minHeight: 80, alignment: .leading)
        }
    }

    private var botonCargar: some View {
        Button {
            Task { await cargar(expectedFileId: bloque.artifact.fileId) }
        } label: {
            VStack(spacing: 9) {
                Image(systemName: icono)
                    .font(.system(size: 30, weight: .medium))
                Text(etiquetaCargar)
                    .font(.footnote.weight(.semibold))
            }
            .frame(maxWidth: .infinity, minHeight: 100)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(etiquetaCargar)
    }

    @ViewBuilder
    private var mediaLista: some View {
        switch bloque.mediaKind {
        case .image:
            if let imagen {
                Image(uiImage: imagen)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity, maxHeight: 320)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .accessibilityLabel(bloque.alt.isEmpty ? "Imagen generada" : bloque.alt)
            }
        case .video:
            if let player {
                VideoPlayer(player: player)
                    .frame(minHeight: 190, maxHeight: 300)
                    .clipShape(RoundedRectangle(cornerRadius: 10))
                    .accessibilityLabel(bloque.alt.isEmpty ? "Video generado" : bloque.alt)
            }
        case .audio:
            HStack(spacing: 12) {
                Button(action: alternarAudio) {
                    Image(systemName: estaReproduciendo ? "pause.circle.fill" : "play.circle.fill")
                        .font(.system(size: 42))
                }
                .buttonStyle(.plain)
                .accessibilityLabel(estaReproduciendo ? "Pausar audio" : "Reproducir audio")
                VStack(alignment: .leading, spacing: 3) {
                    Text(estaReproduciendo ? "Reproduciendo" : "Audio listo")
                        .font(.subheadline.weight(.semibold))
                    Text("Control privado de Edecan")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
            }
            .padding(.vertical, 12)
        }
    }

    private var icono: String {
        switch bloque.mediaKind {
        case .image: "photo"
        case .video: "play.rectangle.fill"
        case .audio: "waveform"
        }
    }

    private var etiquetaCargar: String {
        switch bloque.mediaKind {
        case .image: "Cargar imagen"
        case .video: "Cargar video"
        case .audio: "Cargar audio"
        }
    }

    private func cargar(expectedFileId: String) async {
        guard let client else {
            estado = .error("Inicia sesion para ver este archivo.")
            return
        }
        if case .cargando = estado { return }
        estado = .cargando
        imagen = nil
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        estaReproduciendo = false
        eliminarTemporalActual()

        do {
            let descarga = try await client.descargarArtefacto(bloque.artifact)
            guard !Task.isCancelled, bloque.artifact.fileId == expectedFileId else { return }
            switch bloque.mediaKind {
            case .image:
                guard let decoded = UIImage(data: descarga.data) else {
                    throw PreviewError.formatoInvalido
                }
                imagen = decoded
            case .video, .audio:
                let url = try await Task.detached(priority: .userInitiated) {
                    try guardarPreviewTemporal(descarga)
                }.value
                guard !Task.isCancelled, bloque.artifact.fileId == expectedFileId else {
                    limpiarPreviewTemporal(url)
                    return
                }
                archivoTemporal = url
                player = AVPlayer(url: url)
            }
            estado = .listo
        } catch {
            guard !Task.isCancelled, bloque.artifact.fileId == expectedFileId else { return }
            estado = .error("No se pudo abrir la vista previa. \(error.localizedDescription)")
        }
    }

    private func limpiarRecursos() {
        player?.pause()
        player?.replaceCurrentItem(with: nil)
        player = nil
        imagen = nil
        estaReproduciendo = false
        eliminarTemporalActual()
        estado = .sinCargar
    }

    private func eliminarTemporalActual() {
        guard let archivoTemporal else { return }
        limpiarPreviewTemporal(archivoTemporal)
        self.archivoTemporal = nil
    }

    private func alternarAudio() {
        guard let player else { return }
        if estaReproduciendo {
            player.pause()
        } else {
            player.play()
        }
        estaReproduciendo.toggle()
    }
}

private enum PreviewError: Error, LocalizedError {
    case formatoInvalido

    var errorDescription: String? {
        "El formato del archivo no coincide con la vista previa."
    }
}

private func guardarPreviewTemporal(_ descarga: DownloadedArtifact) throws -> URL {
    let fileManager = FileManager.default
    let idSeguro = descarga.artifact.fileId
        .filter { $0.isLetter || $0.isNumber || $0 == "-" || $0 == "_" }
        .prefix(80)
    let carpeta = fileManager.temporaryDirectory
        .appendingPathComponent("edecan-media-previews", isDirectory: true)
        .appendingPathComponent(idSeguro.isEmpty ? "archivo" : String(idSeguro), isDirectory: true)
    try fileManager.createDirectory(at: carpeta, withIntermediateDirectories: true)

    let ultimoComponente = descarga.artifact.filename
        .replacingOccurrences(of: "\\", with: "/")
        .split(separator: "/")
        .last
        .map(String.init) ?? "media"
    let nombre = ultimoComponente
        .replacingOccurrences(of: ":", with: "-")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    let destino = carpeta.appendingPathComponent(nombre.isEmpty ? "media" : String(nombre.prefix(180)))
    try descarga.data.write(to: destino, options: .atomic)
    return destino
}

private func limpiarPreviewTemporal(_ url: URL) {
    let raiz = FileManager.default.temporaryDirectory
        .appendingPathComponent("edecan-media-previews", isDirectory: true)
        .standardizedFileURL.path
    let carpeta = url.deletingLastPathComponent().standardizedFileURL
    guard carpeta.path.hasPrefix(raiz + "/") else { return }
    try? FileManager.default.removeItem(at: carpeta)
}
