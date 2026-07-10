import Foundation
import AVFoundation

/// Graba audio del micrófono mientras el usuario mantiene presionado el
/// botón de *push-to-talk* de ``VozView``, usando `AVAudioEngine` (pedido
/// explícito del paquete de trabajo — no `AVAudioRecorder`) con un tap sobre
/// el `inputNode`. Escribe PCM directo a un `.wav` temporal — mismo formato
/// que espera `POST /v1/voice/transcribe` (`audio/wav`).
///
/// `@unchecked Sendable`: el tap block de `AVAudioEngine` es un closure de
/// tiempo real que el SDK invoca en su propio hilo de audio, fuera de
/// cualquier actor — bajo Swift 6 estricto (`SWIFT_STRICT_CONCURRENCY:
/// complete`, `project.yml`) capturar `self` ahí exige que el TIPO se declare
/// `Sendable`. La conformidad es segura en la práctica: todo el estado
/// mutable (`archivo`/`url`/`grabando`) solo se toca desde `iniciar()` /
/// `escribir(_:)` / `detener()` / `cancelar()`, y `VozViewModel` (el único
/// llamador) serializa esas llamadas una a la vez — nunca hay dos
/// grabaciones concurrentes sobre la misma instancia.
final class VozRecorder: @unchecked Sendable {
    enum RecorderError: LocalizedError {
        case permisoDenegado
        case yaGrabando
        case sinGrabacionActiva
        case archivoInvalido

        var errorDescription: String? {
            switch self {
            case .permisoDenegado:
                return "Edecán no tiene permiso para usar el micrófono. Actívalo en Ajustes → Privacidad → Micrófono."
            case .yaGrabando:
                return "Ya hay una grabación en curso."
            case .sinGrabacionActiva:
                return "No hay ninguna grabación activa para detener."
            case .archivoInvalido:
                return "No se pudo preparar el archivo de audio."
            }
        }
    }

    private let engine = AVAudioEngine()
    private var archivo: AVAudioFile?
    private var urlTemporal: URL?
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

    /// Arranca el motor de audio y empieza a escribir el tap del micrófono a
    /// un archivo temporal nuevo.
    func iniciar() throws {
        guard !grabando else { throw RecorderError.yaGrabando }

        let sesion = AVAudioSession.sharedInstance()
        try sesion.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try sesion.setActive(true)

        let inputNode = engine.inputNode
        let formato = inputNode.outputFormat(forBus: 0)
        guard formato.sampleRate > 0 else { throw RecorderError.archivoInvalido }

        let destino = FileManager.default.temporaryDirectory
            .appendingPathComponent("edecan-voz-\(UUID().uuidString)")
            .appendingPathExtension("wav")
        let archivoNuevo = try AVAudioFile(forWriting: destino, settings: formato.settings)

        inputNode.removeTap(onBus: 0)
        inputNode.installTap(onBus: 0, bufferSize: 4096, format: formato) { [weak self] buffer, _ in
            self?.escribir(buffer)
        }

        engine.prepare()
        try engine.start()

        archivo = archivoNuevo
        urlTemporal = destino
        grabando = true
    }

    /// Se llama desde el tap de `AVAudioEngine` — ver el docstring del tipo
    /// para por qué es seguro capturar `self` ahí bajo Swift 6 estricto.
    private func escribir(_ buffer: AVAudioPCMBuffer) {
        try? archivo?.write(from: buffer)
    }

    /// Detiene la grabación y devuelve los bytes WAV grabados — borra el
    /// archivo temporal después de leerlo, nunca deja audio del usuario en
    /// disco más tiempo del necesario para subirlo.
    func detener() throws -> Data {
        guard grabando, let urlTemporal else { throw RecorderError.sinGrabacionActiva }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        archivo = nil
        grabando = false
        defer {
            try? FileManager.default.removeItem(at: urlTemporal)
            self.urlTemporal = nil
        }
        return try Data(contentsOf: urlTemporal)
    }

    /// Corta la grabación sin devolver ni subir nada (p. ej. el usuario
    /// arrastró el dedo fuera del botón para cancelar).
    func cancelar() {
        guard grabando else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        archivo = nil
        grabando = false
        if let urlTemporal {
            try? FileManager.default.removeItem(at: urlTemporal)
        }
        urlTemporal = nil
    }
}
