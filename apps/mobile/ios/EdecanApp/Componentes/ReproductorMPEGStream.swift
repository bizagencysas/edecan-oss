import AVFoundation
import EdecanKit
import Foundation

/// Acumula el HTTP stream de audio y lo reproduce con `AVAudioPlayer`.
/// El audio llega a trozos y se reproduce fluido al completarse la descarga.
@MainActor
final class ReproductorMPEGStream: NSObject, AVAudioPlayerDelegate {
    private var reproductor: AVAudioPlayer?
    private var continuacion: CheckedContinuation<Void, Never>?
    private var cancelado = false

    func detener() {
        cancelado = true
        reproductor?.stop()
        reproductor = nil
        continuacion?.resume()
        continuacion = nil
    }

    func reproducir(stream: AsyncThrowingStream<TrozoHablar, Error>) async throws -> Data {
        cancelado = false
        var audio = Data()
        var mime = "audio/mpeg"
        for try await trozo in stream {
            if cancelado { return audio }
            audio.append(trozo.chunk)
            mime = trozo.mime
        }
        if cancelado { return audio }
        // StubTTS = WAV corto de silencio. MPEG real de ElevenLabs se toca
        // aunque la oración sea breve.
        if mime.contains("wav"), audio.count < 2_000 {
            return audio
        }
        try await reproducir(data: audio)
        return audio
    }

    /// Reproduce un audio ya completo (p. ej. desde el caché del mensaje) sin
    /// volver a la API. Misma lógica que el stream acumulado.
    func reproducir(data: Data) async throws {
        cancelado = false
        guard data.count > 64 else { return }
        configurarSesion()
        let player = try AVAudioPlayer(data: data)
        player.delegate = self
        reproductor = player
        player.prepareToPlay()
        guard player.play() else {
            reproductor = nil
            throw NSError(
                domain: "edecan.tts",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No se pudo reproducir el audio."]
            )
        }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            continuacion = cont
        }
    }

    private func configurarSesion() {
        let sesion = AVAudioSession.sharedInstance()
        try? sesion.setCategory(.playback, mode: .spokenAudio, options: [.duckOthers])
        try? sesion.setActive(true, options: .notifyOthersOnDeactivation)
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            continuacion?.resume()
            continuacion = nil
            reproductor = nil
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor in
            continuacion?.resume()
            continuacion = nil
            reproductor = nil
        }
    }
}
