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

/// Respuesta atómica del claim QR: sesión inmediata y credencial durable del
/// dispositivo. `deviceToken` nunca se muestra ni se guarda fuera Keychain.
public struct PairingClaimOut: Codable, Sendable, Equatable {
    public let accessToken: String
    public let refreshToken: String
    public let tokenType: String
    public let deviceId: String
    public let deviceToken: String

    enum CodingKeys: String, CodingKey {
        case accessToken = "access_token"
        case refreshToken = "refresh_token"
        case tokenType = "token_type"
        case deviceId = "device_id"
        case deviceToken = "device_token"
    }

    public var tokens: TokenPair {
        TokenPair(accessToken: accessToken, refreshToken: refreshToken, tokenType: tokenType)
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

// MARK: - Perfil personal (`GET/PUT /v1/perfil`)

public struct ProfileIdentity: Codable, Sendable, Equatable {
    public var nombrePreferido: String
    public var nombreCompleto: String
    public var pronombres: String
    public var fechaNacimiento: String
    public var pais: String
    public var ciudad: String
    public var zonaHoraria: String
    public var ocupacion: String
    public var idiomaPreferido: String
    public var formaDeTrato: String
    public var biografia: String

    enum CodingKeys: String, CodingKey {
        case nombrePreferido = "nombre_preferido"
        case nombreCompleto = "nombre_completo"
        case pronombres
        case fechaNacimiento = "fecha_nacimiento"
        case pais, ciudad
        case zonaHoraria = "zona_horaria"
        case ocupacion
        case idiomaPreferido = "idioma_preferido"
        case formaDeTrato = "forma_de_trato"
        case biografia
    }

    public init(
        nombrePreferido: String = "", nombreCompleto: String = "", pronombres: String = "",
        fechaNacimiento: String = "", pais: String = "", ciudad: String = "",
        zonaHoraria: String = "", ocupacion: String = "", idiomaPreferido: String = "",
        formaDeTrato: String = "", biografia: String = ""
    ) {
        self.nombrePreferido = nombrePreferido
        self.nombreCompleto = nombreCompleto
        self.pronombres = pronombres
        self.fechaNacimiento = fechaNacimiento
        self.pais = pais
        self.ciudad = ciudad
        self.zonaHoraria = zonaHoraria
        self.ocupacion = ocupacion
        self.idiomaPreferido = idiomaPreferido
        self.formaDeTrato = formaDeTrato
        self.biografia = biografia
    }
}

public struct ProfileData: Codable, Sendable, Equatable {
    public var identidad: ProfileIdentity
    public var gustos: [String]
    public var proyectos: [String]
    public var metas: [String]
    public var relaciones: [String]
    public var empresas: [String]
    public var habitos: [String]

    public init(
        identidad: ProfileIdentity = .init(), gustos: [String] = [], proyectos: [String] = [],
        metas: [String] = [], relaciones: [String] = [], empresas: [String] = [],
        habitos: [String] = []
    ) {
        self.identidad = identidad
        self.gustos = gustos
        self.proyectos = proyectos
        self.metas = metas
        self.relaciones = relaciones
        self.empresas = empresas
        self.habitos = habitos
    }
}

public struct LiveProfile: Codable, Sendable, Equatable {
    public var resumen: String
    public var datos: ProfileData
    public var version: Int
    public var updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case resumen, datos, version
        case updatedAt = "updated_at"
    }

    public init(resumen: String = "", datos: ProfileData = .init(), version: Int = 0, updatedAt: Date? = nil) {
        self.resumen = resumen
        self.datos = datos
        self.version = version
        self.updatedAt = updatedAt
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
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, channel
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }
}

/// Archivo ya subido y listo para adjuntar a un mensaje. El UUID `id` es el
/// unico dato que vuelve en `attachments`; nombre/MIME quedan para la UI.
public struct UploadedFile: Codable, Sendable, Equatable, Identifiable {
    public let id: String
    public let filename: String
    public let mime: String?
    public let sizeBytes: Int?
    public let status: String
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, filename, mime, status
        case sizeBytes = "size_bytes"
        case createdAt = "created_at"
    }
}

/// Política única del cliente para rechazar antes de crear el envoltorio
/// multipart. El backend sigue siendo la autoridad final, pero esta barrera
/// evita duplicar archivos demasiado grandes en disco y desperdiciar red.
public enum FileUploadPolicy {
    public static let maximumBytes = 25 * 1_024 * 1_024

    public enum ValidationError: Error, LocalizedError, Sendable, Equatable {
        case notARegularFile
        case tooLarge(actualBytes: Int, maximumBytes: Int)

        public var errorDescription: String? {
            switch self {
            case .notARegularFile:
                return "Selecciona un archivo válido, no una carpeta."
            case .tooLarge(_, let maximumBytes):
                return "El archivo supera el límite de \(maximumBytes / 1_024 / 1_024) MB."
            }
        }
    }

    @discardableResult
    public static func validate(_ url: URL) throws -> Int {
        let values = try url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey])
        guard values.isRegularFile == true else { throw ValidationError.notARegularFile }
        let size = values.fileSize ?? 0
        guard size <= maximumBytes else {
            throw ValidationError.tooLarge(actualBytes: size, maximumBytes: maximumBytes)
        }
        return size
    }
}

/// Referencia segura guardada dentro del contenido historico de un mensaje.
public struct ChatAttachment: Sendable, Equatable, Identifiable {
    public let fileId: String
    public let filename: String
    public let mime: String?

    public var id: String { fileId }

    public init(fileId: String, filename: String, mime: String? = nil) {
        self.fileId = fileId
        self.filename = filename
        self.mime = mime
    }
}

/// Mensaje persistido de `GET /v1/conversations/{id}`. `content` historico
/// puede ser texto plano o `{text, attachments}`; ambos se normalizan aqui.
public struct ConversationMessage: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let role: String
    public let text: String
    public let attachments: [ChatAttachment]
    public let toolCalls: [ChatEvent]
    public let tokensIn: Int
    public let tokensOut: Int
    public let createdAt: Date

    enum CodingKeys: String, CodingKey {
        case id, role, content
        case toolCalls = "tool_calls"
        case tokensIn = "tokens_in"
        case tokensOut = "tokens_out"
        case createdAt = "created_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        role = try container.decode(String.self, forKey: .role)
        let content = (try? container.decode(JSONValue.self, forKey: .content)) ?? .null
        switch content {
        case .string(let value):
            text = value
            attachments = []
        case .object(let object):
            if case .string(let value)? = object["text"] { text = value }
            else { text = "" }
            attachments = Self.decodeAttachments(object["attachments"])
        default:
            text = ""
            attachments = []
        }
        toolCalls = (try? container.decode([ChatEvent].self, forKey: .toolCalls)) ?? []
        tokensIn = (try? container.decode(Int.self, forKey: .tokensIn)) ?? 0
        tokensOut = (try? container.decode(Int.self, forKey: .tokensOut)) ?? 0
        createdAt = try container.decode(Date.self, forKey: .createdAt)
    }

    private static func decodeAttachments(_ raw: JSONValue?) -> [ChatAttachment] {
        guard case .array(let values)? = raw else { return [] }
        return values.compactMap { value in
            guard case .object(let item) = value,
                  case .string(let fileId)? = item["file_id"]
            else { return nil }
            let filename: String
            if case .string(let value)? = item["filename"] { filename = value }
            else { filename = "archivo" }
            let mime: String?
            if case .string(let value)? = item["mime"] { mime = value }
            else { mime = nil }
            return ChatAttachment(fileId: fileId, filename: filename, mime: mime)
        }
    }
}

public struct ConversationDetail: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String?
    public let channel: String
    public let createdAt: Date
    public let updatedAt: Date?
    public let messages: [ConversationMessage]
    public let pendingConfirmation: PendingConfirmationOut?

    enum CodingKeys: String, CodingKey {
        case id, title, channel, messages
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case pendingConfirmation = "pending_confirmation"
    }
}

/// Parte pública y recuperable de una aprobación peligrosa. Nunca incluye el
/// turno interno serializado ni secretos de ejecución.
public struct PendingConfirmationOut: Decodable, Sendable, Equatable {
    public let toolCallId: String
    public let name: String
    public let args: [String: JSONValue]

    enum CodingKeys: String, CodingKey {
        case toolCallId = "tool_call_id"
        case name, args
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

/// Un archivo creado por una herramienta durante el turno. La referencia
/// nunca contiene una URL publica: ``APIClient/descargarArtefacto(_:)`` usa
/// `fileId` contra el endpoint autenticado y limitado al tenant actual.
public struct ArtifactRef: Codable, Sendable, Equatable, Identifiable {
    public let fileId: String
    public let filename: String
    public let mime: String?

    public var id: String { fileId }

    enum CodingKeys: String, CodingKey {
        case fileId = "file_id"
        case filename, mime
    }

    public init(fileId: String, filename: String, mime: String? = nil) {
        self.fileId = fileId
        self.filename = filename
        self.mime = mime
    }
}

/// Bytes privados descargados para compartir/guardar con la hoja nativa.
public struct DownloadedArtifact: Sendable, Equatable {
    public let artifact: ArtifactRef
    public let data: Data

    public init(artifact: ArtifactRef, data: Data) {
        self.artifact = artifact
        self.data = data
    }
}

// MARK: - Presentacion rica del chat

/// Pantallas que el contrato compartido permite abrir desde una tarjeta.
/// Mantener este enum cerrado evita que el backend pueda convertir un string
/// arbitrario en una ruta interna del cliente.
public enum ChatScreen: String, Decodable, Sendable, Equatable {
    case assistant
    case create
    case remote
    case activity
    case settings
    case travel
    case orders
    case files
    case skills
}

/// Accion visible de un bloque rico. Los tipos futuros o payloads invalidos
/// se conservan como ``unsupported`` y no rompen el resto del evento SSE.
public enum ChatAction: Decodable, Sendable, Equatable {
    case openURL(id: String, label: String, url: URL)
    case openScreen(id: String, label: String, screen: ChatScreen)
    case prefillMessage(id: String, label: String, message: String)
    case unsupported(id: String, label: String?, action: String)

    private enum CodingKeys: String, CodingKey {
        case id, label, action, url, screen, message
    }

    public init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self) else {
            self = .unsupported(id: "invalid-action", label: nil, action: "invalid")
            return
        }
        let action = (try? container.decode(String.self, forKey: .action)) ?? "unknown"
        let id = (try? container.decode(String.self, forKey: .id)) ?? "\(action)-action"
        let label = try? container.decode(String.self, forKey: .label)

        switch action {
        case "open_url":
            guard let label,
                  let rawURL = try? container.decode(String.self, forKey: .url),
                  let url = Self.httpURLSegura(rawURL)
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .openURL(id: id, label: label, url: url)
        case "open_screen":
            guard let label,
                  let rawScreen = try? container.decode(String.self, forKey: .screen),
                  let screen = ChatScreen(rawValue: rawScreen)
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .openScreen(id: id, label: label, screen: screen)
        case "prefill_message", "send_message":
            // `send_message` fue el nombre previo del contrato. Se interpreta
            // deliberadamente como prefill: nunca dispara una orden por si solo.
            guard let label,
                  let message = try? container.decode(String.self, forKey: .message),
                  !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .prefillMessage(id: id, label: label, message: message)
        default:
            self = .unsupported(id: id, label: label, action: action)
        }
    }

    public var id: String {
        switch self {
        case .openURL(let id, _, _), .openScreen(let id, _, _),
             .prefillMessage(let id, _, _), .unsupported(let id, _, _):
            return id
        }
    }

    public var label: String? {
        switch self {
        case .openURL(_, let label, _), .openScreen(_, let label, _),
             .prefillMessage(_, let label, _):
            return label
        case .unsupported(_, let label, _):
            return label
        }
    }

    public var isSupported: Bool {
        if case .unsupported = self { return false }
        return true
    }

    /// Solo URLs HTTP(S), absolutas, con host y sin credenciales embebidas.
    /// La comprobacion se repite en el cliente aunque el backend ya valide.
    public static func httpURLSegura(_ rawValue: String) -> URL? {
        let clean = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard var components = URLComponents(string: clean),
              let scheme = components.scheme?.lowercased(),
              scheme == "http" || scheme == "https",
              let host = components.host,
              !host.isEmpty,
              components.user == nil,
              components.password == nil
        else { return nil }
        let normalizedHost = host.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: "."))
        guard normalizedHost != "localhost",
              !normalizedHost.hasSuffix(".localhost"),
              !normalizedHost.hasSuffix(".local"),
              !normalizedHost.hasSuffix(".internal"),
              !esDireccionLocalOReservada(normalizedHost)
        else { return nil }
        components.scheme = scheme
        return components.url
    }

    private static func esDireccionLocalOReservada(_ host: String) -> Bool {
        let ipv6 = host.trimmingCharacters(in: CharacterSet(charactersIn: "[]")).lowercased()
        if ipv6 == "::" || ipv6 == "::1" || ipv6.hasPrefix("fc")
            || ipv6.hasPrefix("fd") || ipv6.hasPrefix("fe80:") {
            return true
        }

        let octets = host.split(separator: ".", omittingEmptySubsequences: false).compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }) else { return false }
        let (first, second) = (octets[0], octets[1])
        return first == 0
            || first == 10
            || first == 127
            || (first == 100 && (64...127).contains(second))
            || (first == 169 && second == 254)
            || (first == 172 && (16...31).contains(second))
            || (first == 192 && second == 168)
            || first >= 224
    }
}

public enum ChatSourceMode: String, Decodable, Sendable, Equatable {
    case demo
    case live
    case unknown

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "unknown"
        self = Self(rawValue: raw) ?? .unknown
    }
}

public enum MediaKind: String, Decodable, Sendable, Equatable {
    case image
    case video
    case audio
}

public struct MediaBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let mediaKind: MediaKind
    public let artifact: ArtifactRef
    public let alt: String
    public let caption: String?

    enum CodingKeys: String, CodingKey {
        case artifact, alt, caption
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
        case mediaKind = "media_kind"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        mediaKind = try container.decode(MediaKind.self, forKey: .mediaKind)
        artifact = try container.decode(ArtifactRef.self, forKey: .artifact)
        alt = try container.decodeIfPresent(String.self, forKey: .alt) ?? ""
        caption = try container.decodeIfPresent(String.self, forKey: .caption)
    }
}

public struct LinkPreviewBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let url: String
    public let title: String
    public let description: String?
    public let siteName: String?
    public let observedAt: String?
    public let sourceMode: ChatSourceMode
    public let actions: [ChatAction]

    enum CodingKeys: String, CodingKey {
        case url, title, description, actions
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
        case siteName = "site_name"
        case observedAt = "observed_at"
        case sourceMode = "source_mode"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        url = try container.decode(String.self, forKey: .url)
        title = try container.decode(String.self, forKey: .title)
        description = try container.decodeIfPresent(String.self, forKey: .description)
        siteName = try container.decodeIfPresent(String.self, forKey: .siteName)
        observedAt = try container.decodeIfPresent(String.self, forKey: .observedAt)
        sourceMode = try container.decodeIfPresent(ChatSourceMode.self, forKey: .sourceMode) ?? .unknown
        actions = try container.decodeIfPresent([ChatAction].self, forKey: .actions) ?? []
    }
}

public struct FlightCardBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let offerId: String
    public let airline: String
    public let origin: String
    public let destination: String
    public let departure: String?
    public let arrival: String?
    public let stops: Int
    public let price: String
    public let currency: String
    public let sourceMode: ChatSourceMode
    public let provider: String?
    public let observedAt: String?
    public let expiresAt: String?
    public let taxes: String?
    public let cancellation: String?
    public let actions: [ChatAction]

    enum CodingKeys: String, CodingKey {
        case airline, origin, destination, departure, arrival, stops, price, currency
        case provider, taxes, cancellation, actions
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
        case offerId = "offer_id"
        case sourceMode = "source_mode"
        case observedAt = "observed_at"
        case expiresAt = "expires_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        offerId = try container.decode(String.self, forKey: .offerId)
        airline = try container.decode(String.self, forKey: .airline)
        origin = try container.decode(String.self, forKey: .origin)
        destination = try container.decode(String.self, forKey: .destination)
        departure = try container.decodeIfPresent(String.self, forKey: .departure)
        arrival = try container.decodeIfPresent(String.self, forKey: .arrival)
        stops = try container.decodeIfPresent(Int.self, forKey: .stops) ?? 0
        price = try container.decode(String.self, forKey: .price)
        currency = try container.decode(String.self, forKey: .currency)
        sourceMode = try container.decodeIfPresent(ChatSourceMode.self, forKey: .sourceMode) ?? .unknown
        provider = try container.decodeIfPresent(String.self, forKey: .provider)
        observedAt = try container.decodeIfPresent(String.self, forKey: .observedAt)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
        taxes = try container.decodeIfPresent(String.self, forKey: .taxes)
        cancellation = try container.decodeIfPresent(String.self, forKey: .cancellation)
        actions = try container.decodeIfPresent([ChatAction].self, forKey: .actions) ?? []
    }
}

public struct HotelCardBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let offerId: String
    public let name: String
    public let city: String
    public let checkin: String?
    public let checkout: String?
    public let rating: String?
    public let price: String
    public let currency: String
    public let sourceMode: ChatSourceMode
    public let provider: String?
    public let observedAt: String?
    public let expiresAt: String?
    public let taxes: String?
    public let cancellation: String?
    public let actions: [ChatAction]

    enum CodingKeys: String, CodingKey {
        case name, city, checkin, checkout, rating, price, currency
        case provider, taxes, cancellation, actions
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
        case offerId = "offer_id"
        case sourceMode = "source_mode"
        case observedAt = "observed_at"
        case expiresAt = "expires_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        offerId = try container.decode(String.self, forKey: .offerId)
        name = try container.decode(String.self, forKey: .name)
        city = try container.decode(String.self, forKey: .city)
        checkin = try container.decodeIfPresent(String.self, forKey: .checkin)
        checkout = try container.decodeIfPresent(String.self, forKey: .checkout)
        rating = try container.decodeIfPresent(String.self, forKey: .rating)
        price = try container.decode(String.self, forKey: .price)
        currency = try container.decode(String.self, forKey: .currency)
        sourceMode = try container.decodeIfPresent(ChatSourceMode.self, forKey: .sourceMode) ?? .unknown
        provider = try container.decodeIfPresent(String.self, forKey: .provider)
        observedAt = try container.decodeIfPresent(String.self, forKey: .observedAt)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
        taxes = try container.decodeIfPresent(String.self, forKey: .taxes)
        cancellation = try container.decodeIfPresent(String.self, forKey: .cancellation)
        actions = try container.decodeIfPresent([ChatAction].self, forKey: .actions) ?? []
    }
}

/// Bloque tipado y forward-compatible. Un bloque futuro o malformado se
/// vuelve ``unsupported`` y puede mostrar `fallback_text` sin tumbar el chat.
public enum ChatBlock: Decodable, Sendable, Equatable {
    case media(MediaBlock)
    case linkPreview(LinkPreviewBlock)
    case flight(FlightCardBlock)
    case hotel(HotelCardBlock)
    case unsupported(type: String, fallbackText: String?)

    private enum CodingKeys: String, CodingKey {
        case type
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
    }

    public init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self) else {
            self = .unsupported(type: "invalid", fallbackText: nil)
            return
        }
        let type = (try? container.decode(String.self, forKey: .type)) ?? "unknown"
        let version = (try? container.decode(Int.self, forKey: .schemaVersion)) ?? 1
        let fallback = try? container.decode(String.self, forKey: .fallbackText)
        guard version == 1 else {
            self = .unsupported(type: type, fallbackText: fallback)
            return
        }

        switch type {
        case "media":
            if let value = try? MediaBlock(from: decoder) { self = .media(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "link_preview":
            if let value = try? LinkPreviewBlock(from: decoder) { self = .linkPreview(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "flight":
            if let value = try? FlightCardBlock(from: decoder) { self = .flight(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "hotel":
            if let value = try? HotelCardBlock(from: decoder) { self = .hotel(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        default:
            self = .unsupported(type: type, fallbackText: fallback)
        }
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
    case toolStart(toolCallId: String? = nil, name: String, args: [String: JSONValue])
    case toolProgress(
        toolCallId: String? = nil,
        name: String,
        elapsedSeconds: Int,
        message: String
    )
    case toolEnd(
        toolCallId: String? = nil,
        name: String,
        resultPreview: String,
        artifacts: [ArtifactRef],
        blocksVersion: Int = 1,
        blocks: [ChatBlock] = [],
        missionId: String? = nil
    )
    case confirmationRequired(toolCallId: String, name: String, args: [String: JSONValue])
    case done(usage: Usage?)
    case error(message: String)
    case unknown(type: String)

    enum CodingKeys: String, CodingKey {
        case type, text, name, args, usage, message, artifacts, blocks
        case elapsedSeconds = "elapsed_seconds"
        case resultPreview = "result_preview"
        case toolCallId = "tool_call_id"
        case blocksVersion = "blocks_version"
        case missionId = "mission_id"
    }

    public init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self) else {
            self = .unknown(type: "invalid")
            return
        }
        let type = (try? container.decode(String.self, forKey: .type)) ?? "missing"
        switch type {
        case "text_delta":
            guard let text = try? container.decode(String.self, forKey: .text) else {
                self = .unknown(type: type)
                return
            }
            self = .textDelta(text: text)
        case "tool_start":
            guard let name = try? container.decode(String.self, forKey: .name) else {
                self = .unknown(type: type)
                return
            }
            self = .toolStart(
                toolCallId: try? container.decode(String.self, forKey: .toolCallId),
                name: name,
                args: (try? container.decode([String: JSONValue].self, forKey: .args)) ?? [:]
            )
        case "tool_progress":
            guard let name = try? container.decode(String.self, forKey: .name) else {
                self = .unknown(type: type)
                return
            }
            self = .toolProgress(
                toolCallId: try? container.decode(String.self, forKey: .toolCallId),
                name: name,
                elapsedSeconds: (try? container.decode(Int.self, forKey: .elapsedSeconds)) ?? 0,
                message: (try? container.decode(String.self, forKey: .message)) ?? "Trabajando"
            )
        case "tool_end":
            guard let name = try? container.decode(String.self, forKey: .name) else {
                self = .unknown(type: type)
                return
            }
            self = .toolEnd(
                toolCallId: try? container.decode(String.self, forKey: .toolCallId),
                name: name,
                resultPreview: (try? container.decode(String.self, forKey: .resultPreview)) ?? "",
                artifacts: (try? container.decode([ArtifactRef].self, forKey: .artifacts)) ?? [],
                blocksVersion: (try? container.decode(Int.self, forKey: .blocksVersion)) ?? 1,
                blocks: (try? container.decode([ChatBlock].self, forKey: .blocks)) ?? [],
                missionId: try? container.decode(String.self, forKey: .missionId)
            )
        case "confirmation_required":
            guard let toolCallId = try? container.decode(String.self, forKey: .toolCallId),
                  let name = try? container.decode(String.self, forKey: .name)
            else {
                self = .unknown(type: type)
                return
            }
            self = .confirmationRequired(
                toolCallId: toolCallId,
                name: name,
                args: (try? container.decode([String: JSONValue].self, forKey: .args)) ?? [:]
            )
        case "done":
            self = .done(usage: try? container.decode(Usage.self, forKey: .usage))
        case "error":
            guard let message = try? container.decode(String.self, forKey: .message) else {
                self = .unknown(type: type)
                return
            }
            self = .error(message: message)
        default:
            self = .unknown(type: type)
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
