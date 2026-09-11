import Foundation

/// Una voz del catálogo del tenant (`GET /v1/voz/voces`): voces de stock de
/// ElevenLabs, clones propios, o los stubs offline cuando el tenant no conectó
/// una credencial de voz.
public struct VozElegible: Codable, Equatable, Identifiable, Sendable {
    public let voiceId: String
    public let nombre: String
    public let categoria: String
    public let previewUrl: String?

    public var id: String { voiceId }

    enum CodingKeys: String, CodingKey {
        case voiceId = "voice_id"
        case nombre
        case categoria
        case previewUrl = "preview_url"
    }

    public init(voiceId: String, nombre: String, categoria: String, previewUrl: String?) {
        self.voiceId = voiceId
        self.nombre = nombre
        self.categoria = categoria
        self.previewUrl = previewUrl
    }
}