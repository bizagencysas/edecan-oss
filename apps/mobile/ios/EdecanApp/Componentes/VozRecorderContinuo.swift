import AVFoundation
import Foundation

/// Un chunk de audio en vivo ya convertido a PCM16 (s16le, 16 kHz, mono) junto
/// con su nivel RMS (0…1, relativo a escala completa) para VAD y para la onda
/// de la pantalla de llamada.
public struct ChunkAudioPCM16: Sendable, Equatable {
    /// PCM crudo s16le 16 kHz mono (típicamente 100 ms = 3 200 bytes).
    public let datos: Data
    /// Energía media del chunk: 0 (silencio) … 1 (escala completa).
    public let rms: Float

    public init(datos: Data, rms: Float) {
        self.datos = datos
        self.rms = rms
    }
}

/// Captura continua del micrófono con `AVAudioEngine` (tap del `inputNode`),
/// convierte con `AVAudioConverter` a PCM16 s16le 16 kHz mono y entrega un
/// `AsyncStream` de chunks de ~100 ms, con RMS por chunk. Es el "oído" de la
/// llamada siempre-escuchando: el loop del view model consume el stream, mide
/// el RMS para el VAD (detección de voz y de silencio para el `commit`) y,
/// si el servidor soporta STT live, manda los chunks por WS con `sendPCM16`.
///
/// `@unchecked Sendable`: el block del tap de `AVAudioEngine` corre en el hilo
/// de audio del SDK, fuera de cualquier actor. Bajo Swift 6 estricto
/// (`SWIFT_STRICT_CONCURRENCY: complete`) capturar `self` ahí exige que el
/// tipo sea `Sendable`. La conformidad es segura: el estado mutable solo se
/// toca desde `iniciar()` / `procesar(_:)` / `detener()`, y `LlamadaViewModel`
/// (el único llamador) serializa esas llamadas — nunca hay dos capturas
/// concurrentes sobre la misma instancia. `AsyncStream.Continuation.yield`
/// es seguro entre hilos, así que el chunk viaja del hilo de audio al
/// MainActor sin carrera.
final class VozRecorderContinuo: @unchecked Sendable {
    enum ErrorRecorder: LocalizedError {
        case permisoDenegado
        case yaGrabando
        case sinGrabacionActiva
        case formatoInvalido

        var errorDescription: String? {
            switch self {
            case .permisoDenegado:
                return "Edecán no tiene permiso para usar el micrófono. Actívalo en Ajustes → Privacidad → Micrófono."
            case .yaGrabando:
                return "Ya hay una escucha en curso."
            case .sinGrabacionActiva:
                return "No hay ninguna escucha activa para detener."
            case .formatoInvalido:
                return "No se pudo preparar el formato de audio."
            }
        }
    }

    /// Bytes de un chunk de 100 ms a 16 kHz mono s16le (32000 muestras/s ×
    /// 2 bytes × 0.1 s).
    private static let bytesPorChunk = 3_200

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var formatoSalida: AVAudioFormat?
    private var continuacion: AsyncStream<ChunkAudioPCM16>.Continuation?
    private var acumulador = Data()
    private var tieneTap = false
    private(set) var grabando = false

    /// Pide permiso de micrófono si todavía no se resolvió — muestra el
    /// diálogo del sistema la primera vez (`NSMicrophoneUsageDescription` ya
    /// está en `project.yml`). `true` si se puede grabar.
    func solicitarPermiso() async -> Bool {
        await withCheckedContinuation { continuation in
            AVAudioApplication.requestRecordPermission { permitido in
                continuation.resume(returning: permitido)
            }
        }
    }

    /// Arranca el motor, instala el tap y devuelve el stream de chunks PCM16.
    /// La sesión usa `.voiceChat` (mejor esfuerzo de cancelación de eco: sin
    /// esto, la voz de Edecán que sale por el altavoz se cuela al micrófono y
    /// dispara barge-in sola).
    func iniciar() throws -> AsyncStream<ChunkAudioPCM16> {
        guard !grabando else { throw ErrorRecorder.yaGrabando }

        let sesion = AVAudioSession.sharedInstance()
        try sesion.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try sesion.setActive(true)

        let inputNode = engine.inputNode
        let formatoEntrada = inputNode.outputFormat(forBus: 0)
        guard formatoEntrada.sampleRate > 0 else { throw ErrorRecorder.formatoInvalido }
        guard let salida = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16_000, channels: 1, interleaved: true),
              let convertidor = AVAudioConverter(from: formatoEntrada, to: salida)
        else { throw ErrorRecorder.formatoInvalido }

        let (stream, continuation) = AsyncStream<ChunkAudioPCM16>.makeStream()
        self.formatoSalida = salida
        self.converter = convertidor
        self.continuacion = continuation
        self.acumulador = Data()

        // Mismo guardia que ``VozRecorder``: `removeTap` levanta una excepción
        // de Objective-C si NO hay tap instalado (instalación nueva).
        if tieneTap {
            inputNode.removeTap(onBus: 0)
            tieneTap = false
        }
        inputNode.installTap(onBus: 0, bufferSize: 4096, format: formatoEntrada) { [weak self] buffer, _ in
            self?.procesar(buffer)
        }
        tieneTap = true

        do {
            engine.prepare()
            try engine.start()
        } catch {
            if tieneTap {
                engine.inputNode.removeTap(onBus: 0)
                tieneTap = false
            }
            self.continuacion = nil
            self.converter = nil
            self.formatoSalida = nil
            continuation.finish()
            throw error
        }

        grabando = true
        return stream
    }

    /// Se llama desde el tap — ver el docstring del tipo para por qué es
    /// seguro capturar `self` ahí bajo Swift 6 estricto.
    private func procesar(_ buffer: AVAudioPCMBuffer) {
        guard let converter, let formatoSalida else { return }

        let ratio = formatoSalida.sampleRate / buffer.format.sampleRate
        let capacidad = AVAudioFrameCount((Double(buffer.frameLength) * ratio).rounded(.up)) + 1
        guard let salida = AVAudioPCMBuffer(pcmFormat: formatoSalida, frameCapacity: capacidad) else { return }

        var alimentado = false
        var error: NSError?
        converter.convert(to: salida, error: &error) { _, estado in
            if alimentado {
                estado.pointee = .noDataNow
                return nil
            }
            alimentado = true
            estado.pointee = .haveData
            return buffer
        }
        guard error == nil else { return }

        let mBuffers = salida.audioBufferList.pointee.mBuffers
        guard let dataPtr = mBuffers.mData, mBuffers.mDataByteSize > 0 else { return }
        acumulador.append(Data(bytes: dataPtr, count: Int(mBuffers.mDataByteSize)))

        let objetivo = Self.bytesPorChunk
        while acumulador.count >= objetivo {
            let trozo = Data(acumulador.prefix(objetivo))
            acumulador.removeFirst(objetivo)
            continuacion?.yield(ChunkAudioPCM16(datos: trozo, rms: Self.rmsPCM16(trozo)))
        }
    }

    /// Detiene la captura y cierra el stream (el `for await` del consumidor
    /// termina limpiamente).
    func detener() {
        guard grabando else { return }
        if tieneTap {
            engine.inputNode.removeTap(onBus: 0)
            tieneTap = false
        }
        engine.stop()
        let continuation = continuacion
        continuacion = nil
        converter = nil
        formatoSalida = nil
        acumulador = Data()
        grabando = false
        continuation?.finish()
    }

    /// RMS (0…1) de un bloque de muestras Int16 interleaved.
    private static func rmsPCM16(_ datos: Data) -> Float {
        let cantidad = datos.count / 2
        guard cantidad > 0 else { return 0 }
        var suma: Double = 0
        datos.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            let muestras = raw.bindMemory(to: Int16.self)
            for indice in 0..<cantidad {
                let muestra = Double(muestras[indice])
                suma += muestra * muestra
            }
        }
        return Float((suma / Double(cantidad)).squareRoot()) / 32_768.0
    }
}