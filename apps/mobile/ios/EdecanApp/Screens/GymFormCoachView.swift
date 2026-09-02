import AVFoundation
import CoreTransferable
import EdecanKit
import PhotosUI
import SwiftUI
import UIKit
import UniformTypeIdentifiers

/// Coach de técnica: el usuario elige una foto o un video de la galería y un
/// ejercicio, y la app manda el frame (o los frames extraídos del video) a
/// `POST /v1/gym/form/analizar` para recibir feedback de forma con IA.
/// Sin cámara a propósito: el permiso de cámara complica el flujo y la
/// fototeca basta para el coach.
struct GymFormCoachView: View {
    @Environment(SessionStore.self) private var session

    private enum ModoCoach: String, CaseIterable, Identifiable {
        case foto = "Foto"
        case video = "Vídeo"

        var id: String { rawValue }
    }

    /// Ítem elegido en el picker de fotos (la foto decodificada vive en `foto`).
    @State private var fotoItem: PhotosPickerItem?
    @State private var foto: UIImage?
    @State private var videoItem: PhotosPickerItem?
    @State private var frames: [UIImage] = []
    @State private var modo: ModoCoach = .foto
    @State private var ejercicio = "Sentadilla"
    @State private var cargando = false
    @State private var feedback: String?
    @State private var errorMensaje: String?

    private let ejercicios = [
        "Sentadilla",
        "Press de banca",
        "Peso muerto",
        "Dominadas",
        "Curl de bíceps",
        "Press militar",
    ]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                selectorModo
                tarjetaEjercicio
                if modo == .foto {
                    tarjetaFoto
                } else {
                    tarjetaVideo
                }
                botonAnalizar
                resultado
            }
            .padding()
        }
        .background(EdecanTheme.degradado.opacity(0.12).ignoresSafeArea())
        .navigationTitle("Coach de técnica")
        .navigationBarTitleDisplayMode(.inline)
        .onChange(of: fotoItem) { _, item in
            guard let item else { return }
            Task { await cargarFoto(item) }
        }
        .onChange(of: videoItem) { _, item in
            guard let item else { return }
            Task { await cargarVideo(item) }
        }
    }

    // MARK: - Selector de modo

    private var selectorModo: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Tipo de análisis", systemImage: "square.stack.3d.up.fill")
                .font(.headline)

            Picker("Modo", selection: $modo) {
                ForEach(ModoCoach.allCases) { modo in
                    Text(modo.rawValue).tag(modo)
                }
            }
            .pickerStyle(.segmented)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Tarjeta de foto

    private var tarjetaFoto: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Foto de tu ejecución", systemImage: "figure.strengthtraining.traditional")
                .font(.headline)

            if let foto {
                Image(uiImage: foto)
                    .resizable()
                    .scaledToFit()
                    .frame(maxWidth: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
                    .overlay(alignment: .topTrailing) {
                        Button {
                            self.foto = nil
                            fotoItem = nil
                            feedback = nil
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .font(.title2)
                                .symbolRenderingMode(.hierarchical)
                                .foregroundStyle(.white, .black.opacity(0.4))
                                .padding(8)
                        }
                        .accessibilityLabel("Quitar foto")
                    }
            } else {
                PhotosPicker(
                    selection: $fotoItem,
                    matching: .images,
                    photoLibrary: .shared()
                ) {
                    VStack(spacing: 8) {
                        Image(systemName: "photo.badge.plus")
                            .font(.system(size: 32))
                            .foregroundStyle(EdecanTheme.morado)
                        Text("Elegir una foto de la galería…")
                            .font(.subheadline.weight(.medium))
                        Text("De preferencia con buena luz y cuerpo completo en cuadro.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 22)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Tarjeta de video

    private var tarjetaVideo: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Video de tu ejecución", systemImage: "video.fill")
                .font(.headline)

            if !frames.isEmpty {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(Array(frames.enumerated()), id: \.offset) { _, frame in
                            Image(uiImage: frame)
                                .resizable()
                                .scaledToFill()
                                .frame(width: 72, height: 96)
                                .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                        }
                    }
                }
                .overlay(alignment: .topTrailing) {
                    Button {
                        frames = []
                        videoItem = nil
                        feedback = nil
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .font(.title2)
                            .symbolRenderingMode(.hierarchical)
                            .foregroundStyle(.white, .black.opacity(0.4))
                            .padding(8)
                    }
                    .accessibilityLabel("Quitar video")
                }
            } else {
                PhotosPicker(
                    selection: $videoItem,
                    matching: .videos,
                    photoLibrary: .shared()
                ) {
                    VStack(spacing: 8) {
                        Image(systemName: "video.badge.plus")
                            .font(.system(size: 32))
                            .foregroundStyle(EdecanTheme.morado)
                        Text("Elegir un video de la galería…")
                            .font(.subheadline.weight(.medium))
                        Text("Se extraerán hasta 6 frames para analizar tu técnica.")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .multilineTextAlignment(.center)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 22)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Tarjeta de ejercicio

    private var tarjetaEjercicio: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("Ejercicio", systemImage: "dumbbell.fill")
                .font(.headline)

            Picker("Ejercicio", selection: $ejercicio) {
                ForEach(ejercicios, id: \.self) { nombre in
                    Text(nombre).tag(nombre)
                }
            }
            .pickerStyle(.menu)
            .tint(EdecanTheme.morado)
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(16)
        .frame(maxWidth: .infinity, alignment: .leading)
        .tarjetaVidrio(esquina: 18)
    }

    // MARK: - Botón de análisis

    private var listoParaAnalizar: Bool {
        switch modo {
        case .foto: return foto != nil
        case .video: return !frames.isEmpty
        }
    }

    private var botonAnalizar: some View {
        Button {
            Task { await analizar() }
        } label: {
            if cargando {
                HStack(spacing: 10) {
                    ProgressView().tint(.white)
                    Text("Analizando técnica…")
                        .font(.headline)
                }
                .frame(maxWidth: .infinity)
                .padding(.vertical, 14)
            } else {
                Label("Analizar técnica", systemImage: "sparkles")
                    .font(.headline)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 14)
            }
        }
        .buttonStyle(.plain)
        .background(EdecanTheme.degradado, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .foregroundStyle(.white)
        .disabled(!listoParaAnalizar || cargando)
        .opacity(listoParaAnalizar ? 1 : 0.5)
    }

    // MARK: - Resultado

    @ViewBuilder
    private var resultado: some View {
        if let error = errorMensaje {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "exclamationmark.triangle.fill")
                    .font(.subheadline)
                    .foregroundStyle(.orange)
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tarjetaVidrio(esquina: 14)
        } else if let feedback {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "sparkles")
                    .font(.subheadline)
                    .foregroundStyle(EdecanTheme.morado)
                Text(feedback)
                    .font(.footnote)
                    .foregroundStyle(.primary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .textSelection(.enabled)
            }
            .padding(14)
            .frame(maxWidth: .infinity, alignment: .leading)
            .tarjetaVidrio(esquina: 14)
        }
    }

    // MARK: - Lógica

    private func cargarFoto(_ item: PhotosPickerItem) async {
        guard let datos = try? await item.loadTransferable(type: Data.self) else {
            errorMensaje = "No se pudo leer la foto elegida."
            return
        }
        guard let imagen = UIImage(data: datos) else {
            errorMensaje = "El archivo elegido no es una imagen válida."
            return
        }
        foto = imagen
        feedback = nil
        errorMensaje = nil
    }

    private func cargarVideo(_ item: PhotosPickerItem) async {
        do {
            guard let video = try await item.loadTransferable(type: CoachVideoTransferable.self) else {
                errorMensaje = "No se pudo leer el video elegido."
                return
            }
            let extraidos = try await extraerFrames(url: video.url, cantidad: 6)
            guard !extraidos.isEmpty else {
                errorMensaje = "No se pudieron extraer frames del video."
                return
            }
            frames = extraidos
            feedback = nil
            errorMensaje = nil
        } catch {
            errorMensaje = error.localizedDescription
        }
    }

    private func extraerFrames(url: URL, cantidad: Int) async throws -> [UIImage] {
        let asset = AVURLAsset(url: url)
        let duracion = try await asset.load(.duration)
        let segundos = CMTimeGetSeconds(duracion)
        guard segundos.isFinite, segundos > 0 else {
            throw CoachVideoError.sinDuracion
        }

        let generador = AVAssetImageGenerator(asset: asset)
        generador.appliesPreferredTrackTransform = true

        var extraidos: [UIImage] = []
        for indice in 0..<cantidad {
            let tiempo = CMTime(
                seconds: segundos * Double(indice) / Double(cantidad),
                preferredTimescale: 600
            )
            if let resultado = try? await generador.image(at: tiempo) {
                extraidos.append(UIImage(cgImage: resultado.image))
            }
        }
        return extraidos
    }

    private func analizar() async {
        guard let client = session.client else {
            errorMensaje = "Necesitas sesión activa para analizar."
            return
        }

        cargando = true
        feedback = nil
        errorMensaje = nil
        defer { cargando = false }

        do {
            switch modo {
            case .foto:
                guard let foto else {
                    errorMensaje = "Necesitas una foto para analizar."
                    return
                }
                guard let jpeg = foto.jpegData(compressionQuality: 0.7) else {
                    errorMensaje = "No se pudo comprimir la imagen."
                    return
                }
                let resultado = try await client.gymAnalizarForma(imagen: jpeg, ejercicio: ejercicio)
                feedback = resultado.feedback
            case .video:
                let jpegs = frames.compactMap { $0.jpegData(compressionQuality: 0.7) }
                guard !jpegs.isEmpty else {
                    errorMensaje = "No se pudieron preparar los frames del video."
                    return
                }
                let resultado = try await client.gymAnalizarFormaFrames(frames: jpegs, ejercicio: ejercicio)
                feedback = resultado.feedback
            }
        } catch {
            errorMensaje = error.localizedDescription
        }
    }
}

struct CoachVideoTransferable: Transferable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { video in
            SentTransferredFile(video.url)
        } importing: { recibido in
            let extensionOriginal = recibido.file.pathExtension.isEmpty
                ? "mov"
                : recibido.file.pathExtension
            let destino = URL.documentsDirectory
                .appendingPathComponent("coach_video", isDirectory: false)
                .appendingPathExtension(extensionOriginal)
            if FileManager.default.fileExists(atPath: destino.path) {
                try FileManager.default.removeItem(at: destino)
            }
            try FileManager.default.copyItem(at: recibido.file, to: destino)
            return Self(url: destino)
        }
    }
}

enum CoachVideoError: LocalizedError {
    case sinDuracion

    var errorDescription: String? {
        switch self {
        case .sinDuracion:
            return "No se pudo determinar la duración del video."
        }
    }
}