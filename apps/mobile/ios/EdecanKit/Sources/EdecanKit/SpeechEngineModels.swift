import Foundation

/// Contratos de la voz gestionada por Speech Engine de ElevenLabs
/// (`docs/speech-engine.md`). El cliente SOLO recibe el `conversation_token`
/// efímero de WebRTC — jamás la API key del tenant.

/// `GET /v1/voice/preferences` — preferencias efectivas + catálogos.
public struct PreferenciasVozGestionada: Codable, Equatable, Sendable {
    public let preference: PreferenciaVozGestionada?
    public let credentialConnected: Bool
    public let paidProviderNotice: String
    public let catalogs: CatalogosVozGestionada
    public let defaults: DefaultsVozGestionada

    enum CodingKeys: String, CodingKey {
        case preference
        case credentialConnected = "credential_connected"
        case paidProviderNotice = "paid_provider_notice"
        case catalogs
        case defaults
    }
}

public struct PreferenciaVozGestionada: Codable, Equatable, Sendable {
    public let enabled: Bool
    public let provider: String
    public let voiceModelId: String?
    public let delegationModelId: String?
    public let delegationEffort: String?
    public let voiceId: String?
    public let ttsModelId: String?
    public let maxDurationSeconds: Int

    enum CodingKeys: String, CodingKey {
        case enabled, provider
        case voiceModelId = "voice_model_id"
        case delegationModelId = "delegation_model_id"
        case delegationEffort = "delegation_effort"
        case voiceId = "voice_id"
        case ttsModelId = "tts_model_id"
        case maxDurationSeconds = "max_duration_seconds"
    }
}

public struct CatalogosVozGestionada: Codable, Equatable, Sendable {
    public let voiceModels: [ModeloChatCatalogo]
    public let ttsVoices: [VozTTS]
    public let ttsModels: [ModeloTTS]

    enum CodingKeys: String, CodingKey {
        case voiceModels = "voice_models"
        case ttsVoices = "tts_voices"
        case ttsModels = "tts_models"
    }
}

/// Fila del catálogo de modelos del chat (mismo shape que `GET /v1/models/chat`).
public struct ModeloChatCatalogo: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let nombre: String
    public let descripcion: String?
    public let orden: Int?
    public let principal: Bool?
    public let veImagenes: Bool?
    public let soportaEsfuerzo: Bool?

    enum CodingKeys: String, CodingKey {
        case id, nombre, descripcion, orden, principal
        case veImagenes = "ve_imagenes"
        case soportaEsfuerzo = "soporta_esfuerzo"
    }
}

public struct VozTTS: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
}

public struct ModeloTTS: Codable, Equatable, Sendable, Identifiable {
    public let id: String
    public let name: String
}

public struct DefaultsVozGestionada: Codable, Equatable, Sendable {
    public let voiceModelId: String
    public let ttsModelId: String
    public let maxDurationSeconds: Int

    enum CodingKeys: String, CodingKey {
        case voiceModelId = "voice_model_id"
        case ttsModelId = "tts_model_id"
        case maxDurationSeconds = "max_duration_seconds"
    }
}

/// `PUT /v1/voice/preferences` — body.
public struct PutPreferenciasVozGestionada: Encodable, Sendable {
    public var enabled: Bool
    public var provider: String
    public var voiceModelId: String?
    public var delegationModelId: String?
    public var delegationEffort: String?
    public var voiceId: String?
    public var ttsModelId: String?
    public var maxDurationSeconds: Int

    public init(
        enabled: Bool,
        provider: String = "elevenlabs",
        voiceModelId: String?,
        delegationModelId: String?,
        delegationEffort: String?,
        voiceId: String?,
        ttsModelId: String?,
        maxDurationSeconds: Int
    ) {
        self.enabled = enabled
        self.provider = provider
        self.voiceModelId = voiceModelId
        self.delegationModelId = delegationModelId
        self.delegationEffort = delegationEffort
        self.voiceId = voiceId
        self.ttsModelId = ttsModelId
        self.maxDurationSeconds = maxDurationSeconds
    }

    enum CodingKeys: String, CodingKey {
        case enabled, provider
        case voiceModelId = "voice_model_id"
        case delegationModelId = "delegation_model_id"
        case delegationEffort = "delegation_effort"
        case voiceId = "voice_id"
        case ttsModelId = "tts_model_id"
        case maxDurationSeconds = "max_duration_seconds"
    }
}

public struct PutPreferenciasRespuesta: Codable, Sendable {
    public let preference: PreferenciaVozGestionada
}

/// `POST /v1/voice/speech-engine/sessions` — body y respuesta.
public struct CrearSesionVozGestionada: Encodable, Sendable {
    public var conversationId: String?
    public var paidConsent: Bool

    public init(conversationId: String?, paidConsent: Bool) {
        self.conversationId = conversationId
        self.paidConsent = paidConsent
    }

    enum CodingKeys: String, CodingKey {
        case conversationId = "conversation_id"
        case paidConsent = "paid_consent"
    }
}

public struct SesionVozGestionada: Codable, Sendable {
    public let sessionId: String
    public let conversationId: String
    public let conversationToken: String
    public let expiresAt: String
    public let tokenExpiresAt: String
    public let voiceModelId: String
    public let delegationModelId: String?
    public let ttsModelId: String
    public let voiceId: String?
    public let language: String

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case conversationId = "conversation_id"
        case conversationToken = "conversation_token"
        case expiresAt = "expires_at"
        case tokenExpiresAt = "token_expires_at"
        case voiceModelId = "voice_model_id"
        case delegationModelId = "delegation_model_id"
        case ttsModelId = "tts_model_id"
        case voiceId = "voice_id"
        case language
    }
}

public struct FinSesionVozGestionada: Codable, Sendable {
    public let sessionId: String
    public let ended: Bool

    enum CodingKeys: String, CodingKey {
        case sessionId = "session_id"
        case ended
    }
}