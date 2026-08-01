import Foundation

// MARK: - `GET /v1/credentials`

public struct LLMCredentialOut: Codable, Sendable, Equatable {
    public let kind: String?
    public let modelPrincipal: String?
    public let modelRapido: String?
    public let modelProfundo: String?
    public let reasoningEffortProfundo: String?
    public let baseURL: String?
    public let masked: String?

    enum CodingKeys: String, CodingKey {
        case kind, masked
        case modelPrincipal = "model_principal"
        case modelRapido = "model_rapido"
        case modelProfundo = "model_profundo"
        case reasoningEffortProfundo = "reasoning_effort_profundo"
        case baseURL = "base_url"
    }
}

public struct VoiceSTTCredentialOut: Codable, Sendable, Equatable {
    public let provider: String?
    public let masked: String?
}

public struct VoiceTTSCredentialOut: Codable, Sendable, Equatable {
    public let provider: String?
    public let voiceId: String?
    public let masked: String?

    enum CodingKeys: String, CodingKey {
        case provider, masked
        case voiceId = "voice_id"
    }
}

public struct ImagesCredentialOut: Codable, Sendable, Equatable {
    public let baseURL: String?
    public let model: String?
    public let masked: String?

    enum CodingKeys: String, CodingKey {
        case model, masked
        case baseURL = "base_url"
    }
}

public struct SearchCredentialOut: Codable, Sendable, Equatable {
    public let provider: String?
    public let masked: String?
}

/// `GET /v1/credentials` completo — un campo por recurso, cada uno `nil` si
/// el tenant no conectó nada todavía ahí.
public struct CredentialsOut: Codable, Sendable, Equatable {
    public let llm: LLMCredentialOut?
    public let voiceStt: VoiceSTTCredentialOut?
    public let voiceTts: VoiceTTSCredentialOut?
    public let images: ImagesCredentialOut?
    public let search: SearchCredentialOut?

    enum CodingKeys: String, CodingKey {
        case llm, images, search
        case voiceStt = "voice_stt"
        case voiceTts = "voice_tts"
    }
}

// MARK: - `/v1/setup` — wizard de primer arranque

/// `GET /v1/setup/status` — shape REAL de `apps/api/edecan_api/routers/setup.py`
/// (verificado contra el código fuente: distinto del ejemplo aspiracional que
/// trae `docs/api.md`, escrito antes de que este router aterrizara).
public struct SetupStatus: Codable, Sendable, Equatable {
    public let localMode: Bool
    public let llmConfigured: Bool
    public let version: String

    enum CodingKeys: String, CodingKey {
        case version
        case localMode = "local_mode"
        case llmConfigured = "llm_configured"
    }
}
