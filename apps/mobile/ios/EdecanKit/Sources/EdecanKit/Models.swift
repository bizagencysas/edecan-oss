import Foundation

// MARK: - Autenticación

/// Respuesta de `POST /v1/auth/login`, `/register` y `/refresh`
/// (`docs/api.md` §"Autenticación y sesión"; `TokenPairOut` en el backend).
public struct TokenPair: Codable, Sendable, Equatable {
    public let accessToken: String
    public let refreshToken: String
    public let tokenType: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
    }

    public init(accessToken: String, refreshToken: String, tokenType: String = "bearer") {
        self.accessToken = accessToken
        self.refreshToken = refreshToken
        self.tokenType = tokenType
    }
}

// MARK: - Perfil (`GET /v1/me`)

/// `GET /v1/me` (`docs/api.md` §"Perfil y persona"). `flags` mezcla banderas
/// booleanas (`voice.web`) y límites numéricos (`limits.messages_per_day`)
/// bajo el mismo diccionario — igual que lo devuelve el backend
/// (`edecan_schemas.plans`, ver `ARCHITECTURE.md` §10.13) — por eso cada
/// valor se decodifica como ``FlagValue`` en vez de forzar un solo tipo.
public struct Me: Codable, Sendable, Equatable {
    public struct UserInfo: Codable, Sendable, Equatable {
        public let id: String
        public let email: String
        public let isSuperadmin: Bool
        public let createdAt: Date

        enum CodingKeys: String, CodingKey {
            case id, email
            case isSuperadmin = "is_superadmin"
            case createdAt = "created_at"
        }
    }

    public struct TenantInfo: Codable, Sendable, Equatable {
        public let id: String
        public let name: String
        public let slug: String
        public let planKey: String
        public let status: String
        public let createdAt: Date

        enum CodingKeys: String, CodingKey {
            case id, name, slug, status
            case planKey = "plan_key"
            case createdAt = "created_at"
        }
    }

    public let user: UserInfo
    public let tenant: TenantInfo
    public let flags: [String: FlagValue]

    /// Nombre "de pila" disponible para saludos personalizados —
    /// la API no manda un nombre propio (solo `email`), así que se toma lo
    /// que hay antes de la arroba, igual que hace hoy el frontend web.
    public var nombrePila: String {
        String(user.email.split(separator: "@").first ?? Substring(user.email))
    }
}

/// Un valor de `Me.flags`: o booleano (`"voice.web": true`) o entero
/// (`"limits.messages_per_day": 600`, con `-1` = ilimitado por convención
/// del backend, `ARCHITECTURE.md` §10.13).
public enum FlagValue: Codable, Sendable, Equatable {
    case bool(Bool)
    case int(Int)

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if let value = try? container.decode(Bool.self) {
            self = .bool(value)
            return
        }
        if let value = try? container.decode(Int.self) {
            self = .int(value)
            return
        }
        throw DecodingError.typeMismatch(
            FlagValue.self,
            .init(codingPath: decoder.codingPath, debugDescription: "FlagValue debe ser Bool o Int")
        )
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .bool(let value): try container.encode(value)
        case .int(let value): try container.encode(value)
        }
    }

    public var boolValue: Bool? {
        if case .bool(let value) = self { return value }
        return nil
    }

    public var intValue: Int? {
        if case .int(let value) = self { return value }
        return nil
    }
}

// MARK: - Conversaciones

/// Un elemento de `GET /v1/conversations` o la respuesta de
/// `POST /v1/conversations` (`docs/api.md` §"Conversaciones y chat (SSE)").
/// `channel` se deja como `String` en vez de un enum cerrado a propósito:
/// si el backend agrega un canal nuevo mañana, decodificar no debe romperse
/// solo porque el cliente todavía no lo conoce.
public struct Conversation: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String?
    public let channel: String
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, title, channel
        case createdAt = "created_at"
    }
}

// MARK: - Eventos del turno del agente (SSE)

/// Métricas de tokens del evento `message.done` (`Usage` en
/// `ARCHITECTURE.md` §10.6/§10.7).
public struct Usage: Codable, Sendable, Equatable {
    public let inputTokens: Int
    public let outputTokens: Int

    enum CodingKeys: String, CodingKey {
        case inputTokens = "input_tokens"
        case outputTokens = "output_tokens"
    }
}

/// Un evento del stream SSE de `POST /v1/conversations/{id}/messages` (y de
/// `POST .../confirm`), decodificado por el campo `"type"` del `data:` de
/// cada bloque SSE — exactamente el `AgentEvent` de `ARCHITECTURE.md` §10.7
/// / la tabla de `docs/api.md`. El nombre del `event:` de SSE (p. ej.
/// `message.delta`) es redundante con `"type"` (`text_delta`) y no hace
/// falta para decodificar; lo usa `SSEClient` solo para mensajes de error
/// más claros.
public enum ChatEvent: Decodable, Sendable, Equatable {
    case textDelta(text: String)
    case toolStart(name: String, args: [String: JSONValue])
    case toolEnd(name: String, resultPreview: String)
    case confirmationRequired(toolCallId: String, name: String, args: [String: JSONValue])
    case done(usage: Usage?)
    case error(message: String)

    enum CodingKeys: String, CodingKey {
        case type, text, name, args, usage, message
        case resultPreview = "result_preview"
        case toolCallId = "tool_call_id"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let type = try container.decode(String.self, forKey: .type)
        switch type {
        case "text_delta":
            self = .textDelta(text: try container.decode(String.self, forKey: .text))
        case "tool_start":
            self = .toolStart(
                name: try container.decode(String.self, forKey: .name),
                args: try container.decodeIfPresent([String: JSONValue].self, forKey: .args) ?? [:]
            )
        case "tool_end":
            self = .toolEnd(
                name: try container.decode(String.self, forKey: .name),
                resultPreview: try container.decode(String.self, forKey: .resultPreview)
            )
        case "confirmation_required":
            self = .confirmationRequired(
                toolCallId: try container.decode(String.self, forKey: .toolCallId),
                name: try container.decode(String.self, forKey: .name),
                args: try container.decodeIfPresent([String: JSONValue].self, forKey: .args) ?? [:]
            )
        case "done":
            self = .done(usage: try container.decodeIfPresent(Usage.self, forKey: .usage))
        case "error":
            self = .error(message: try container.decode(String.self, forKey: .message))
        default:
            throw DecodingError.dataCorruptedError(
                forKey: .type, in: container, debugDescription: "Tipo de evento SSE desconocido: \(type)"
            )
        }
    }
}

/// JSON genérico para los `args` de una herramienta (`tool_start`,
/// `confirmation_required`): el backend puede mandar cualquier forma según
/// la tool que se esté llamando, así que no hay un `Codable` fijo posible.
///
/// Importante: `JSONValue.object` decodifica sus claves con la estrategia
/// **por defecto** (`.useDefaultKeys`), nunca `.convertFromSnakeCase` — esas
/// claves son nombres de argumentos reales de la herramienta (p. ej.
/// `"cliente_nombre"`), no deben transformarse o la UI mostraría (o algún
/// día reenviaría) un nombre distinto al que espera el backend.
public enum JSONValue: Codable, Sendable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)
    case object([String: JSONValue])
    case array([JSONValue])
    case null

    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() {
            self = .null
        } else if let value = try? container.decode(Bool.self) {
            self = .bool(value)
        } else if let value = try? container.decode(Double.self) {
            self = .number(value)
        } else if let value = try? container.decode(String.self) {
            self = .string(value)
        } else if let value = try? container.decode([JSONValue].self) {
            self = .array(value)
        } else if let value = try? container.decode([String: JSONValue].self) {
            self = .object(value)
        } else {
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "JSON no soportado")
        }
    }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .string(let value): try container.encode(value)
        case .number(let value): try container.encode(value)
        case .bool(let value): try container.encode(value)
        case .object(let value): try container.encode(value)
        case .array(let value): try container.encode(value)
        case .null: try container.encodeNil()
        }
    }

    /// Representación corta para mostrar en la UI (p. ej. el banner de
    /// "usando tool_start" en `ChatView`) — no es JSON válido de vuelta,
    /// solo texto legible.
    public var vistaPrevia: String {
        switch self {
        case .string(let value): return value
        case .number(let value): return String(value)
        case .bool(let value): return value ? "true" : "false"
        case .null: return "null"
        case .array(let value): return "[\(value.map(\.vistaPrevia).joined(separator: ", "))]"
        case .object(let value):
            return "{\(value.map { "\($0.key): \($0.value.vistaPrevia)" }.sorted().joined(separator: ", "))}"
        }
    }
}
