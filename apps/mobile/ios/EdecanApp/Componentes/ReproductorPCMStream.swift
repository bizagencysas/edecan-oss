import AVFoundation
import Foundation

/// Reproduce PCM crudo (s16le 24 kHz mono) en streaming con `AVAudioEngine` +
/// `AVAudioPlayerNode`: cada `agregar(_:)` encola un buffer y el primero suena
/// de inmediato — Edecán empieza a HABLAR sin esperar el mp3 completo. Es el
/// reproductor del camino `tts_pcm`: el servidor manda chunks `audio/pcm24` y
/// la pantalla los va soltando tal cual llegan.
///
/// Los buffers se encolan como PCM Float32 no intercalado (mono): los Int16
/// s16le se normalizan a `[-1, 1]` antes de llegar al mixer. El fin de la
/// reproducción se avisa por el callback `alTerminar`: se dispara cuando
/// `finalizar()` ya marcó el cierre del stream y el último buffer **se
/// reprodujo de verdad** — el completion usa `.dataPlayedBack`, no el
/// `.dataConsumed` por defecto, así el motor jamás se detiene antes de que el
/// hardware termine de sonar (el `.dataConsumed` disparaba al ser absorbido por
/// el nodo, no al salir por el altavoz, y cortaba la cola de la respuesta). Si
/// nunca hubo audio (respuesta vacía), `finalizar()` avisa de inmediato para no
/// dejar la llamada clavada en "Hablando…".
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
    /// Formato de salida (Float32 mono 24 kHz no intercalado). Se construye en
    /// `preparar()` y se reutiliza en los turnos siguientes para no re-negociar
    /// el grafo.
    private var formato: AVAudioFormat?
    /// `true` cuando `player` ya está `attach`ado y conectado al mixer. El
    /// attach/connect se hace UNA sola vez: re-`attach`ar un nodo ya conectado
    /// lanza una excepción de Objective-C, que `agregar` tragaba con `try?` y
    /// dejaba el player parado desde el segundo turno (silencio).
    private var conectado = false
    private var enMarcha = false
    private var pendientes = 0
    private var esperandoTermino = false
    /// Los chunks de red NO terminan justo entre muestras: si llega un número
    /// impar de bytes, el sobrante espera al próximo chunk. Sin esto la
    /// conversión leía más bytes de los reservados (desbordamiento).
    private var sobrante = Data()
    /// Generación del ciclo de vida. `detener()` la incrementa; los completions
    /// de buffers encolados antes del stop (barge-in) quedan huérfanos y NO
    /// deben decrementar el `pendientes` del turno nuevo (si lo hicieran, el
    /// `finalizar()` del turno nuevo vería un cero prematuro y cortaría la voz).
    private var generacion: UInt64 = 0

    /// Avisa (en MainActor) cuando la reproducción terminó y no queda nada
    /// pendiente. Se limpia en `detener()`.
    var alTerminar: (() -> Void)?

    /// Avisa (en MainActor) cuando el arranque del motor o el encolado de un
    /// buffer falla y ese chunk no se reproduce. La UI debe asignarlo por turno
    /// (junto a `alTerminar`) para no perder errores de audio en silencio.
    var alError: ((Error) -> Void)?

    enum ErrorReproductor: LocalizedError {
        case formatoNoDisponible
        case bufferNoCreado

        var errorDescription: String? {
            switch self {
            case .formatoNoDisponible:
                return "No se pudo preparar el formato PCM de salida."
            case .bufferNoCreado:
                return "No se pudo reservar el buffer de audio."
            }
        }
    }

    /// Prepara motor, sesión y nodo. Reutiliza `.playAndRecord` + `.voiceChat`
    /// (la misma sesión que el micrófono continuo): así grabar y reproducir
    /// conviven durante la llamada y el audio sale por el altavoz.
    ///
    /// El nodo se `attach`a y conecta una sola vez (``conectado``); en los
    /// turnos siguientes solo se re-arranca el motor y el player, sin volver a
    /// `attach` (la causa del silencio a partir del segundo turno).
    private func preparar() throws {
        guard !enMarcha else { return }
        let sesion = AVAudioSession.sharedInstance()
        try sesion.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try sesion.setActive(true)
        if sesion.currentRoute.outputs.contains(where: { $0.portType == .builtInReceiver }) {
            try sesion.overrideOutputAudioPort(.speaker)
        }

        let fmt: AVAudioFormat
        if let existente = formato {
            fmt = existente
        } else {
            guard let nuevo = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24_000, channels: 1, interleaved: false) else {
                throw ErrorReproductor.formatoNoDisponible
            }
            formato = nuevo
            fmt = nuevo
        }

        if !conectado {
            engine.attach(player)
            engine.connect(player, to: engine.mainMixerNode, format: fmt)
            conectado = true
        }
        engine.prepare()
        try engine.start()
        player.play()
        enMarcha = true
    }

    /// Encola un chunk PCM16 s16le 24 kHz mono, lo convierte a Float32 y lo
    /// suelta en cuanto el nodo está listo (el primero arranca solo la
    /// reproducción).
    func agregar(_ data: Data) {
        do {
            if !enMarcha { try preparar() }
        } catch {
            reportar(error)
            return
        }
        guard let formato else { return }

        let combinado = sobrante + data
        let bytesCompletos = (combinado.count / 2) * 2
        guard bytesCompletos > 0 else {
            // Un byte suelto no es una muestra: espera al próximo chunk.
            sobrante = combinado
            return
        }
        sobrante = Data(combinado.suffix(combinado.count - bytesCompletos))
        let datos = Data(combinado.prefix(bytesCompletos))
        let frames = bytesCompletos / 2

        guard let buffer = AVAudioPCMBuffer(pcmFormat: formato, frameCapacity: AVAudioFrameCount(frames)) else {
            reportar(ErrorReproductor.bufferNoCreado)
            return
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        rellenar(buffer, conPCM16: datos)

        pendientes += 1
        let generacionActual = generacion
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor in
                guard let self, self.generacion == generacionActual else { return }
                self.completarBuffer()
            }
        }
    }

    /// Convierte s16le → Float32 normalizado y lo escribe en el canal 0 del
    /// buffer (mono no intercalado). El parse es explícitamente little-endian
    /// (`s16le`), independiente de la arquitectura del dispositivo.
    private func rellenar(_ buffer: AVAudioPCMBuffer, conPCM16 datos: Data) {
        let cantidadMuestras = datos.count / 2
        guard cantidadMuestras > 0, let canal = buffer.floatChannelData?[0] else { return }
        datos.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let bytes = raw.bindMemory(to: UInt8.self)
            for indice in 0..<cantidadMuestras {
                let bajo = UInt16(bytes[indice * 2])
                let alto = UInt16(bytes[indice * 2 + 1])
                let valor = Int16(bitPattern: bajo | (alto << 8))
                canal[indice] = Float(valor) / 32_768.0
            }
        }
    }

    /// Marca que el stream de chunks terminó: cuando el nodo drene lo que
    /// queda encolado, se avisa `alTerminar`.
    func finalizar() {
        esperandoTermino = true
        if !enMarcha {
            // Nunca hubo audio (respuesta vacía o fallo en el arranque): termina
            // de inmediato para no dejar la llamada en "Hablando…".
            let callback = alTerminar
            alTerminar = nil
            alError = nil
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

    private func reportar(_ error: Error) {
        let callback = alError
        callback?(error)
    }

    /// Para la reproducción y suelta todo el estado (barge-in o colgar).
    func detener() {
        generacion += 1
        let estabaEnMarcha = enMarcha
        enMarcha = false
        esperandoTermino = false
        pendientes = 0
        sobrante = Data()
        alTerminar = nil
        alError = nil
        if estabaEnMarcha {
            player.stop()
            engine.stop()
            player.reset()
        }
    }
}
