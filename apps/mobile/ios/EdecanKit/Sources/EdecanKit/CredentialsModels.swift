import Foundation

// MARK: - `GET /v1/credentials` (`apps/api/edecan_api/routers/credentials.py`,
// `docs/api.md` §"Rutas v3") — bring-your-own de LLM/voz/imágenes/búsqueda
// por tenant. Cada bloque es `nil` si el tenant todavía no conectó nada ahí;
// `masked` NUNCA es el secreto completo (`"…" + últimos 4 caracteres`).

public struct LLMCredentialOut: Codable, Sendable, Equatable {
    public let kind: String?
    public let modelPrincipal: String?
    public let modelRapido: String?
    public let baseURL: String?
    public let masked: String?

    enum CodingKeys: String, CodingKey {
        case kind, masked
        case modelPrincipal = "model_principal"
        case modelRapido = "model_rapido"
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

// MARK: - `PUT /v1/credentials/llm` — cuerpo de la petición

/// `kind` ∈ `anthropic`\|`openai_compat`\|`vertex`\|`claude_cli`\|`codex_cli`\|`ollama`
/// (`ARCHITECTURE.md` §12.c). Los tres últimos solo los acepta el servidor si
/// corre con `EDECAN_LOCAL_MODE=true` — ver ``SetupStatus``/``DetectLocalProviders``.
public struct LLMCredentialsIn: Encodable, Sendable, Equatable {
    public var kind: String
    public var apiKey: String?
    public var baseURL: String?
    public var modelPrincipal: String?
    public var modelRapido: String?
    public var extra: [String: String]
    /// Antes de guardar, valida la credencial de verdad contra el proveedor
    /// (`docs/api.md`: "pegar y validar") — `true` por defecto, igual que el
    /// backend.
    public var validate: Bool

    public init(
        kind: String,
        apiKey: String? = nil,
        baseURL: String? = nil,
        modelPrincipal: String? = nil,
        modelRapido: String? = nil,
        extra: [String: String] = [:],
        validate: Bool = true
    ) {
        self.kind = kind
        self.apiKey = apiKey
        self.baseURL = baseURL
        self.modelPrincipal = modelPrincipal
        self.modelRapido = modelRapido
        self.extra = extra
        self.validate = validate
    }

    enum CodingKeys: String, CodingKey {
        case kind, extra, validate
        case apiKey = "api_key"
        case baseURL = "base_url"
        case modelPrincipal = "model_principal"
        case modelRapido = "model_rapido"
    }
}

/// `GET /v1/credentials/llm/models`: catálogo detectado más selección actual.
public struct LLMModelsOut: Codable, Sendable, Equatable {
    public let kind: String
    public let modelPrincipal: String?
    public let modelRapido: String?
    public let models: [String]
    public let manualAllowed: Bool
    public let capabilitiesManagedByEdecan: Bool
    public let discoveryError: String?

    enum CodingKeys: String, CodingKey {
        case kind, models
        case modelPrincipal = "model_principal"
        case modelRapido = "model_rapido"
        case manualAllowed = "manual_allowed"
        case capabilitiesManagedByEdecan = "capabilities_managed_by_edecan"
        case discoveryError = "discovery_error"
    }
}

public struct LLMModelsIn: Encodable, Sendable, Equatable {
    public let modelPrincipal: String
    public let modelRapido: String?

    public init(modelPrincipal: String, modelRapido: String? = nil) {
        self.modelPrincipal = modelPrincipal
        self.modelRapido = modelRapido
    }

    enum CodingKeys: String, CodingKey {
        case modelPrincipal = "model_principal"
        case modelRapido = "model_rapido"
    }
}

/// Los `kind` de LLM que reconoce el backend (`ARCHITECTURE.md` §12.c) — un
/// solo lugar para el Picker de "Conectar LLM" en Perfil y para saber cuáles
/// necesitan `EDECAN_LOCAL_MODE` (``requiereModoLocal``).
public enum LLMProviderKind: String, CaseIterable, Sendable, Identifiable {
    case anthropic
    case openaiCompat = "openai_compat"
    case vertex
    case claudeCli = "claude_cli"
    case codexCli = "codex_cli"
    case ollama

    public var id: String { rawValue }

    /// Solo tienen sentido con el backend corriendo LOCAL en la máquina del
    /// cliente (`DIRECCION_ACTUAL.md`) — el servidor los rechaza con `400`
    /// fuera de `EDECAN_LOCAL_MODE=true`.
    public var requiereModoLocal: Bool {
        switch self {
        case .claudeCli, .codexCli, .ollama: return true
        case .anthropic, .openaiCompat, .vertex: return false
        }
    }

    public var nombreVisible: String {
        switch self {
        case .anthropic: return "Anthropic (API key)"
        case .openaiCompat: return "Compatible con OpenAI"
        case .vertex: return "Google AI / Vertex (API key)"
        case .claudeCli: return "Claude CLI (ya instalado)"
        case .codexCli: return "Codex CLI (ya instalado)"
        case .ollama: return "Ollama (local)"
        }
    }
}

// MARK: - `/v1/setup` — wizard de primer arranque (`ARCHITECTURE.md` §12.a/§12.d)

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

/// Un CLI local detectado (`claude`/`codex`) — `installed=false` con
/// `path`/`version` en `nil` si no se encontró.
public struct CLIProviderStatus: Codable, Sendable, Equatable {
    public let installed: Bool
    public let path: String?
    public let version: String?
}

/// Ollama detectado — `running=false` si no responde (offline, apagado, no
/// instalado); `models` viene vacío en ese caso.
public struct OllamaStatus: Codable, Sendable, Equatable {
    public let running: Bool
    public let baseURL: String
    public let models: [String]

    enum CodingKeys: String, CodingKey {
        case running, models
        case baseURL = "base_url"
    }
}

/// `GET /v1/setup/detect` — autodetección de un clic (`ARCHITECTURE.md`
/// §12.d). Fuera de modo local (`localMode == false`), el servidor siempre
/// devuelve esta forma vacía (nunca revisa binarios de SU máquina como si
/// fueran del cliente) — la UI debe caer directo al flujo de API key.
public struct DetectLocalProviders: Codable, Sendable, Equatable {
    public let localMode: Bool
    public let claudeCli: CLIProviderStatus
    public let codexCli: CLIProviderStatus
    public let ollama: OllamaStatus

    enum CodingKeys: String, CodingKey {
        case localMode = "local_mode"
        case claudeCli = "claude_cli"
        case codexCli = "codex_cli"
        case ollama
    }
}
