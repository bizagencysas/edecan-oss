import AVFoundation
import Foundation

/// Reproduce PCM crudo (s16le 24 kHz mono) en streaming con `AVAudioEngine` +
/// `AVAudioPlayerNode`: cada `agregar(_:)` encola un buffer y el primero suena
/// de inmediato — Edecán empieza a HABLAR sin esperar el mp3 completo. Es el
/// reproductor del camino `tts_pcm`: el servidor manda chunks `audio/pcm24` y
/// la pantalla los va soltando tal cual llegan.
///
/// El fin de la reproducción se avisa por el callback `alTerminar`: se dispara
/// cuando `finalizar()` ya marcó el cierre del stream y todos los buffers
/// encolados completaron su playback (vía el `completionHandler` de
/// `scheduleBuffer`). Si nunca hubo audio (respuesta vacía), `finalizar()`
/// avisa de inmediato para no dejar la llamada clavada en "Hablando…".
///
/// `@MainActor` como ``ReproductorMPEGStream``, y `@unchecked Sendable` por la
/// misma razón que ``VozRecorder``: el `completionHandler` de `scheduleBuffer`
/// es `@Sendable` y corre en el hilo de audio, así que capturar `self` exige
/// `Sendable`; la conformidad es segura porque todo el estado se muta en el
/// MainActor (el completion hace `Task { @MainActor in ... }` para saltar).
@MainActor
final class ReproductorPCMStream: @unchecked Sendable {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private var formato: AVAudioFormat?
    private var enMarcha = false
    private var pendientes = 0
    private var esperandoTermino = false

    /// Avisa (en MainActor) cuando la reproducción terminó y no queda nada
    /// pendiente. Se limpia en `detener()`.
    var alTerminar: (() -> Void)?

    /// Prepara motor, sesión y nodo. Reutiliza `.playAndRecord` + `.voiceChat`
    /// (la misma sesión que el micrófono continuo): así grabar y reproducir
    /// conviven durante la llamada y el audio sale por el altavoz.
    private func preparar() throws {
        guard !enMarcha else { return }
        let sesion = AVAudioSession.sharedInstance()
        try sesion.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try sesion.setActive(true)
        guard let fmt = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 24_000, channels: 1, interleaved: true) else {
            throw NSError(
                domain: "edecan.tts",
                code: 1,
                userInfo: [NSLocalizedDescriptionKey: "No se pudo preparar el formato PCM."]
            )
        }
        formato = fmt
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: fmt)
        engine.prepare()
        try engine.start()
        player.play()
        enMarcha = true
    }

    /// Encola un chunk PCM16 s16le 24 kHz mono y lo suelta en cuanto el nodo
    /// está listo (el primero arranca solo la reproducción).
    func agregar(_ data: Data) {
        if !enMarcha { try? preparar() }
        guard let formato else { return }
        let frames = data.count / 2
        guard frames > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: formato, frameCapacity: AVAudioFrameCount(frames))
        else { return }
        buffer.frameLength = AVAudioFrameCount(frames)
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            guard let origen = raw.baseAddress else { return }
            let destino = buffer.audioBufferList.pointee.mBuffers
            guard let datosDestino = destino.mData else { return }
            memcpy(datosDestino, origen, data.count)
        }
        pendientes += 1
        player.scheduleBuffer(buffer) { [weak self] in
            Task { @MainActor in
                self?.completarBuffer()
            }
        }
    }

    /// Marca que el stream de chunks terminó: cuando el nodo drene lo que
    /// queda encolado, se avisa `alTerminar`.
    func finalizar() {
        esperandoTermino = true
        if !enMarcha {
            // Nunca hubo audio (respuesta vacía o fallo silencioso): termina
            // de inmediato para no dejar la llamada en "Hablando…".
            let callback = alTerminar
            alTerminar = nil
            esperandoTermino = false
            callback?()
            return
        }
        verificarTermino()
    }

    private func completarBuffer() {
        pendientes = max(0, pendientes - 1)
        verificarTermino()
    }

    private func verificarTermino() {
        guard esperandoTermino, pendientes == 0 else { return }
        let callback = alTerminar
        detener()
        callback?()
    }

    /// Para la reproducción y suelta todo el estado (barge-in o colgar).
    func detener() {
        let estabaEnMarcha = enMarcha
        enMarcha = false
        esperandoTermino = false
        pendientes = 0
        alTerminar = nil
        if estabaEnMarcha {
            player.stop()
            engine.stop()
        }
    }
}