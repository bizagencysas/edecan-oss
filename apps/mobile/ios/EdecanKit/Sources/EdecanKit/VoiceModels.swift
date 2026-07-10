import Foundation

/// `POST /v1/voice/transcribe` (`docs/api.md` §"Voz web").
public struct TranscribeOut: Codable, Sendable, Equatable {
    public let text: String
}

/// Resultado de `POST /v1/voice/speak`: bytes de audio + el `Content-Type`
/// real que devolvió el servidor. `audio/wav` cuando el proveedor resuelto
/// para el tenant es el `StubTTS` offline (sin credencial de voz conectada,
/// `apps/api/edecan_api/routers/voice.py`); `audio/mpeg` para cualquier
/// proveedor real (ElevenLabs, Polly). El llamador (``VozRecorder``/`AVAudioPlayer`)
/// no necesita saber cuál es cuál — ambos formatos los reproduce igual.
public struct HablarResultado: Sendable, Equatable {
    public let audio: Data
    public let contentType: String
}
