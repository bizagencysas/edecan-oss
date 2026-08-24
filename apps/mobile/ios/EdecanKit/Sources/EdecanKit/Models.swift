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
    /// Conversación "principal" (frente 5, paridad REFERENCIA): ahí aterrizan
    /// los eventos automáticos que el dueño no pidió -- llamada recibida,
    /// automatización ejecutada, recordatorio disparado. A lo sumo una por
    /// tenant+usuario (`GET /v1/conversations/main`). `decodeIfPresent` con
    /// default `false` para no romper si algún backend viejo todavía no
    /// manda esta clave.
    public let isMain: Bool
    /// Modelo del selector fijado para ESTA conversación (`null` =
    /// automático, y ahí decide el backend). Es propiedad de la conversación
    /// y no de la credencial del tenant, así que la pastilla del composer se
    /// restaura al reabrir el chat en cualquier dispositivo.
    public let model: String?
    /// Nivel de Esfuerzo recordado. Se conserva aunque el modelo activo no lo
    /// soporte (cambiar de Copla a Oda debe recordar el nivel previo).
    public let effort: EsfuerzoChat?
    public let createdAt: Date
    public let updatedAt: Date?

    enum CodingKeys: String, CodingKey {
        case id, title, channel, model, effort
        case isMain = "is_main"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        channel = try container.decode(String.self, forKey: .channel)
        isMain = try container.decodeIfPresent(Bool.self, forKey: .isMain) ?? false
        // `decodeIfPresent`: un servidor anterior al selector no manda estas
        // claves y el historial debe seguir cargando igual. El Esfuerzo se
        // mapea por `rawValue` para que un nivel que el cliente todavía no
        // conozca no tumbe la lista entera de conversaciones.
        model = try container.decodeIfPresent(String.self, forKey: .model)
        let esfuerzoCrudo = try container.decodeIfPresent(String.self, forKey: .effort)
        effort = esfuerzoCrudo.flatMap(EsfuerzoChat.init(rawValue:))
        createdAt = try container.decode(Date.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
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
    public let pinned: Bool
    public let bookmark: Bool
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
            pinned = false
            bookmark = false
        case .object(let object):
            if case .string(let value)? = object["text"] { text = value }
            else { text = "" }
            attachments = Self.decodeAttachments(object["attachments"])
            if case .bool(let value)? = object["pinned"] { pinned = value }
            else { pinned = false }
            if case .bool(let value)? = object["bookmark"] { bookmark = value }
            else { bookmark = false }
        default:
            text = ""
            attachments = []
            pinned = false
            bookmark = false
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
    /// Igual que en ``Conversation``: `null` = automático. Al abrir el chat es
    /// lo que restaura la pastilla del composer.
    public let model: String?
    public let effort: EsfuerzoChat?
    public let createdAt: Date
    public let updatedAt: Date?
    public let messages: [ConversationMessage]
    public let pendingConfirmation: PendingConfirmationOut?

    enum CodingKeys: String, CodingKey {
        case id, title, channel, messages, model, effort
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case pendingConfirmation = "pending_confirmation"
    }

    /// Decodificador explícito solo por el Esfuerzo: mapearlo por `rawValue`
    /// evita que un nivel desconocido impida abrir una conversación entera.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        title = try container.decodeIfPresent(String.self, forKey: .title)
        channel = try container.decode(String.self, forKey: .channel)
        model = try container.decodeIfPresent(String.self, forKey: .model)
        let esfuerzoCrudo = try container.decodeIfPresent(String.self, forKey: .effort)
        effort = esfuerzoCrudo.flatMap(EsfuerzoChat.init(rawValue:))
        createdAt = try container.decode(Date.self, forKey: .createdAt)
        updatedAt = try container.decodeIfPresent(Date.self, forKey: .updatedAt)
        messages = try container.decode([ConversationMessage].self, forKey: .messages)
        pendingConfirmation = try container.decodeIfPresent(
            PendingConfirmationOut.self, forKey: .pendingConfirmation
        )
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
///
/// Allowlist discriminada de 8 acciones (paso 1 del MVP de SDUI,
/// `edecan_schemas.chat.ChatAction`): las 3 de siempre (`openURL`,
/// `openScreen`, `prefillMessage`) más las 5 nuevas de abajo. Un `action`
/// fuera de esta lista decodifica como ``unsupported`` -- el nodo/botón que
/// la trae simplemente no se pinta, no tumba nada más.
public enum ChatAction: Decodable, Sendable, Equatable {
    case openURL(id: String, label: String, url: URL)
    case openScreen(id: String, label: String, screen: ChatScreen)
    case prefillMessage(id: String, label: String, message: String)
    /// Copia `text` (literal, viaja completo en la propia acción) al
    /// portapapeles. Sin red, sin efectos remotos -- mismo contrato que
    /// `CopyTextAction` en `chat.py`.
    case copyText(id: String, label: String, text: String)
    /// Guarda en Fotos un artifact ya mostrado en la card. Esta acción por sí
    /// sola solo trae `fileId` (sin filename/mime): quien la ejecuta resuelve
    /// el `ArtifactRef` completo contra los artifacts que la MISMA card ya
    /// tiene en pantalla (ver `CardGenericaView`), igual que valida el
    /// backend contra `allowed_file_ids` en `rich_blocks_from_tool_data`.
    case saveArtifact(id: String, label: String, fileId: String)
    /// Manda `message` como si la persona lo hubiera escrito: turno normal,
    /// visible, nunca a ciegas -- mismo contrato que `QuestionOption.value`
    /// (que ya hace esto desde el modal de ``QuestionBlock``). NO es un
    /// alias de `prefillMessage`: a diferencia de esa acción, esta SÍ envía.
    /// Antes `send_message` colapsaba a `prefillMessage` (nombre legado sin
    /// autoenvío); `chat.py` (paso 1 del plan de SDUI) ya las separó como
    /// acciones propias y este cliente sigue esa autoridad -- ver
    /// desviaciones del paso 4 y el test
    /// `prefillMessageNuncaAutoenviaYSendMessageSiEnviaAPropósito`.
    case sendMessage(id: String, label: String, message: String)
    /// Privilegiada: dispara la publicación de un borrador tras confirmación
    /// explícita del usuario. `draftId` es TODO lo que trae esta acción --
    /// nunca destino/texto/imagen -- porque el backend revalida el borrador
    /// server-side contra el tenant al publicar (doble candado, ver
    /// `ApproveDraftAction` en `chat.py`).
    case approveDraft(id: String, label: String, draftId: String)
    /// Abre una conversación existente por id. Navegación pura: el servidor
    /// valida pertenencia al tenant al resolverla.
    case openConversation(id: String, label: String, conversationId: String)
    case unsupported(id: String, label: String?, action: String)

    private enum CodingKeys: String, CodingKey {
        case id, label, action, url, screen, message, text
        case fileId = "file_id"
        case draftId = "draft_id"
        case conversationId = "conversation_id"
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
        case "prefill_message":
            guard let label,
                  let message = try? container.decode(String.self, forKey: .message),
                  !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .prefillMessage(id: id, label: label, message: message)
        case "send_message":
            guard let label,
                  let message = try? container.decode(String.self, forKey: .message),
                  !message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .sendMessage(id: id, label: label, message: message)
        case "copy_text":
            guard let label,
                  let text = try? container.decode(String.self, forKey: .text),
                  !text.isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .copyText(id: id, label: label, text: text)
        case "save_artifact":
            guard let label,
                  let fileId = try? container.decode(String.self, forKey: .fileId),
                  !fileId.isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .saveArtifact(id: id, label: label, fileId: fileId)
        case "approve_draft":
            guard let label,
                  let draftId = try? container.decode(String.self, forKey: .draftId),
                  !draftId.isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .approveDraft(id: id, label: label, draftId: draftId)
        case "open_conversation":
            guard let label,
                  let conversationId = try? container.decode(String.self, forKey: .conversationId),
                  !conversationId.isEmpty
            else {
                self = .unsupported(id: id, label: label, action: action)
                return
            }
            self = .openConversation(id: id, label: label, conversationId: conversationId)
        default:
            self = .unsupported(id: id, label: label, action: action)
        }
    }

    public var id: String {
        switch self {
        case .openURL(let id, _, _), .openScreen(let id, _, _),
             .prefillMessage(let id, _, _), .copyText(let id, _, _),
             .saveArtifact(let id, _, _), .sendMessage(let id, _, _),
             .approveDraft(let id, _, _), .openConversation(let id, _, _),
             .unsupported(let id, _, _):
            return id
        }
    }

    public var label: String? {
        switch self {
        case .openURL(_, let label, _), .openScreen(_, let label, _),
             .prefillMessage(_, let label, _), .copyText(_, let label, _),
             .saveArtifact(_, let label, _), .sendMessage(_, let label, _),
             .approveDraft(_, let label, _), .openConversation(_, let label, _):
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
    public let address: String?
    public let imageURL: URL?
    public let sourceMode: ChatSourceMode
    public let provider: String?
    public let observedAt: String?
    public let expiresAt: String?
    public let taxes: String?
    public let cancellation: String?
    public let actions: [ChatAction]

    enum CodingKeys: String, CodingKey {
        case name, city, checkin, checkout, rating, price, currency, address
        case provider, taxes, cancellation, actions
        case imageURL = "image_url"
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
        let rawAddress = try container.decodeIfPresent(String.self, forKey: .address)?
            .trimmingCharacters(in: .whitespacesAndNewlines)
        address = (rawAddress?.isEmpty == false) ? rawAddress : nil
        let rawImage = try container.decodeIfPresent(String.self, forKey: .imageURL)
        imageURL = rawImage.flatMap(ChatAction.httpURLSegura)
        sourceMode = try container.decodeIfPresent(ChatSourceMode.self, forKey: .sourceMode) ?? .unknown
        provider = try container.decodeIfPresent(String.self, forKey: .provider)
        observedAt = try container.decodeIfPresent(String.self, forKey: .observedAt)
        expiresAt = try container.decodeIfPresent(String.self, forKey: .expiresAt)
        taxes = try container.decodeIfPresent(String.self, forKey: .taxes)
        cancellation = try container.decodeIfPresent(String.self, forKey: .cancellation)
        actions = try container.decodeIfPresent([ChatAction].self, forKey: .actions) ?? []
    }
}

public struct ChartSeriesPoint: Decodable, Sendable, Equatable {
    public let label: String
    public let value: Double
}

public struct ChartBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let title: String
    public let series: [ChartSeriesPoint]

    enum CodingKeys: String, CodingKey {
        case title, series
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
    }
}

public struct SourceCitation: Decodable, Sendable, Equatable, Identifiable {
    public let id: String
    public let title: String
    public let url: String
    public let source: String
    public let retrievedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, url, source
        case retrievedAt = "retrieved_at"
    }
}

public struct SourcesBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let citations: [SourceCitation]

    enum CodingKeys: String, CodingKey {
        case citations
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
    }
}

/// Borrador de post social (`crear_contenido_social`, `edecan_creative.social`)
/// mostrado como card de chat. El payload es exactamente el de
/// ``SocialContentDraft`` — el mismo contrato que ya decodifica
/// `POST /v1/content/social` (ver `ContentStudioModels.swift`) — envuelto con
/// `schema_version`/`fallback_text` como el resto de bloques ricos. Reusar el
/// tipo evita duplicar un segundo modelo para la misma forma de datos.
public struct SocialDraftBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let draft: SocialContentDraft

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        draft = try SocialContentDraft(from: decoder)
    }
}

/// Una opción del modal de ``QuestionBlock``.
///
/// `value` es el texto que se manda de vuelta como mensaje del usuario al
/// tocarla; si viene vacío se usa `label`. Están separados para poder mostrar
/// "Personal" y mandar "Publícalo en mi cuenta personal", que le deja al
/// modelo una instrucción sin ambigüedad.
public struct QuestionOption: Decodable, Sendable, Equatable, Identifiable {
    public let label: String
    public let description: String?
    public let value: String?

    /// Estable dentro de un mismo bloque: el backend descarta etiquetas
    /// repetidas antes de emitirlo (ver `PreguntarAlUsuarioTool`).
    public var id: String { label }

    /// Lo que se envía como mensaje al elegir esta opción.
    public var messageText: String {
        let raw = value?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        return raw.isEmpty ? label : raw
    }
}

/// Pregunta con 2-4 opciones que el chat muestra como modal.
///
/// Emitirla TERMINA el turno del agente: la respuesta del usuario viaja como
/// un mensaje normal en el turno siguiente, así que la app no tiene que
/// mantener ningún estado suspendido ni ninguna conexión abierta esperando.
public struct QuestionBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let fallbackText: String?
    public let question: String
    public let header: String?
    public let options: [QuestionOption]
    public let multiSelect: Bool
    public let allowFreeText: Bool

    enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case fallbackText = "fallback_text"
        case question
        case header
        case options
        case multiSelect = "multi_select"
        case allowFreeText = "allow_free_text"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        fallbackText = try container.decodeIfPresent(String.self, forKey: .fallbackText)
        question = try container.decode(String.self, forKey: .question)
        header = try container.decodeIfPresent(String.self, forKey: .header)
        options = try container.decode([QuestionOption].self, forKey: .options)
        multiSelect = try container.decodeIfPresent(Bool.self, forKey: .multiSelect) ?? false
        allowFreeText = try container.decodeIfPresent(Bool.self, forKey: .allowFreeText) ?? true
    }
}

// MARK: - Card genérica (MVP de Server-Driven UI)
//
// `GenericCardBlock` es el ÚNICO tipo de bloque nuevo de este MVP (paso 4 del
// plan de SDUI): en vez de hornear cada UI nueva en Swift, la card es un
// árbol de hasta 4 niveles y 40 nodos, compuesto con 6 primitivas que llevan
// estilo SEMÁNTICO (roles, nunca CSS/px/hex) -- el backend decide QUÉ se
// compone y con qué rol, esta vista decide CÓMO se ve cada rol. Espejo
// exacto de `edecan_schemas.chat` (paso 1, ya en el backend); las fixtures
// dorabas en `packages/schemas/tests/fixtures/chat_blocks/` son la fuente de
// verdad compartida -- ver `CardGenericaTests.swift`.
public let cardMaxProfundidad = 4
public let cardMaxNodos = 40

/// Eje de un ``StackNode``. Un valor futuro desconocido degrada a `vertical`
/// (nivel 2 de forward-compat: un enum semántico nuevo nunca descarta el
/// nodo, solo se queda con el default razonable) en vez de tumbar el nodo.
public enum EjeStack: String, Sendable, Equatable, Decodable {
    case vertical, horizontal

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "vertical"
        self = Self(rawValue: raw) ?? .vertical
    }
}

public enum EspaciadoStack: String, Sendable, Equatable, Decodable {
    case xs, s, m, l

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "m"
        self = Self(rawValue: raw) ?? .m
    }
}

public enum RellenoStack: String, Sendable, Equatable, Decodable {
    case ninguno, compacto, tarjeta

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "ninguno"
        self = Self(rawValue: raw) ?? .ninguno
    }
}

public enum FondoStack: String, Sendable, Equatable, Decodable {
    case ninguno, tarjeta
    case acentoSuave = "acento_suave"
    case advertenciaSuave = "advertencia_suave"
    case exitoSuave = "exito_suave"

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "ninguno"
        self = Self(rawValue: raw) ?? .ninguno
    }
}

public enum AlineacionStack: String, Sendable, Equatable, Decodable {
    case inicio, centro, fin

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "inicio"
        self = Self(rawValue: raw) ?? .inicio
    }
}

/// Contenedor: el VStack/HStack que hoy está horneado en Swift, como dato.
/// `hijos` referencia ``NodoCard`` -- recursivo a propósito.
public struct StackNode: Decodable, Sendable, Equatable {
    public let eje: EjeStack
    public let espaciado: EspaciadoStack
    public let relleno: RellenoStack
    public let fondo: FondoStack
    public let alineacion: AlineacionStack
    public var hijos: [NodoCard]

    private enum CodingKeys: String, CodingKey {
        case eje, espaciado, relleno, fondo, alineacion, hijos
    }

    public init(
        eje: EjeStack = .vertical,
        espaciado: EspaciadoStack = .m,
        relleno: RellenoStack = .ninguno,
        fondo: FondoStack = .ninguno,
        alineacion: AlineacionStack = .inicio,
        hijos: [NodoCard] = []
    ) {
        self.eje = eje
        self.espaciado = espaciado
        self.relleno = relleno
        self.fondo = fondo
        self.alineacion = alineacion
        self.hijos = hijos
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        eje = try container.decodeIfPresent(EjeStack.self, forKey: .eje) ?? .vertical
        espaciado = try container.decodeIfPresent(EspaciadoStack.self, forKey: .espaciado) ?? .m
        relleno = try container.decodeIfPresent(RellenoStack.self, forKey: .relleno) ?? .ninguno
        fondo = try container.decodeIfPresent(FondoStack.self, forKey: .fondo) ?? .ninguno
        alineacion = try container.decodeIfPresent(AlineacionStack.self, forKey: .alineacion) ?? .inicio
        hijos = try container.decodeIfPresent([NodoCard].self, forKey: .hijos) ?? []
    }
}

public enum RolTexto: String, Sendable, Equatable, Decodable {
    case titulo, subtitulo, cuerpo, pie, etiqueta

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "cuerpo"
        self = Self(rawValue: raw) ?? .cuerpo
    }
}

public enum ColorTexto: String, Sendable, Equatable, Decodable {
    case primario, secundario, acento, advertencia, exito

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "primario"
        self = Self(rawValue: raw) ?? .primario
    }
}

public enum EnfasisTexto: String, Sendable, Equatable, Decodable {
    case normal, fuerte

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "normal"
        self = Self(rawValue: raw) ?? .normal
    }
}

/// Resuelve la clase de bug "titular muy grande": el tamaño deja de ser una
/// decisión de Swift y pasa a ser un ROL que el backend elige de un enum
/// cerrado -- un restyle global sigue siendo decisión del tema nativo.
public struct TextoNode: Decodable, Sendable, Equatable {
    public let contenido: String
    public let rol: RolTexto
    public let color: ColorTexto
    public let enfasis: EnfasisTexto
    public let maxLineas: Int?

    private enum CodingKeys: String, CodingKey {
        case contenido, rol, color, enfasis
        case maxLineas = "max_lineas"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        contenido = try container.decode(String.self, forKey: .contenido)
        rol = try container.decodeIfPresent(RolTexto.self, forKey: .rol) ?? .cuerpo
        color = try container.decodeIfPresent(ColorTexto.self, forKey: .color) ?? .primario
        enfasis = try container.decodeIfPresent(EnfasisTexto.self, forKey: .enfasis) ?? .normal
        maxLineas = try container.decodeIfPresent(Int.self, forKey: .maxLineas)
    }
}

public enum AspectoImagen: String, Sendable, Equatable, Decodable {
    case cuadrada, panoramica, libre

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "libre"
        self = Self(rawValue: raw) ?? .libre
    }
}

public enum EsquinasImagen: String, Sendable, Equatable, Decodable {
    case ninguna, s, m

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "ninguna"
        self = Self(rawValue: raw) ?? .ninguna
    }
}

/// Imagen dentro de una card compuesta. `artifact` DEBE pertenecer a los
/// artifacts que la misma tool call devolvió -- misma regla que
/// ``MediaBlock``, validada en el backend (`rich_blocks_from_tool_data`), no
/// aquí.
public struct ImagenNode: Decodable, Sendable, Equatable {
    public let artifact: ArtifactRef
    public let alt: String
    public let aspecto: AspectoImagen
    public let esquinas: EsquinasImagen

    private enum CodingKeys: String, CodingKey { case artifact, alt, aspecto, esquinas }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        artifact = try container.decode(ArtifactRef.self, forKey: .artifact)
        alt = try container.decodeIfPresent(String.self, forKey: .alt) ?? ""
        aspecto = try container.decodeIfPresent(AspectoImagen.self, forKey: .aspecto) ?? .libre
        esquinas = try container.decodeIfPresent(EsquinasImagen.self, forKey: .esquinas) ?? .ninguna
    }
}

/// Video dentro de una card compuesta. Espejo de ``ImagenNode``: ``artifact``
/// apunta a los bytes privados del MP4 y el renderer (``CardGenericaView``) lo
/// reproduce con `AVPlayer` en vez de pintar una imagen estática. Misma regla
/// que ``ImagenNode``/``MediaBlock``: el `artifact` se valida en el backend.
public struct VideoNode: Decodable, Sendable, Equatable {
    public let artifact: ArtifactRef
    public let alt: String
    public let aspecto: AspectoImagen
    public let esquinas: EsquinasImagen

    private enum CodingKeys: String, CodingKey { case artifact, alt, aspecto, esquinas }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        artifact = try container.decode(ArtifactRef.self, forKey: .artifact)
        alt = try container.decodeIfPresent(String.self, forKey: .alt) ?? ""
        aspecto = try container.decodeIfPresent(AspectoImagen.self, forKey: .aspecto) ?? .libre
        esquinas = try container.decodeIfPresent(EsquinasImagen.self, forKey: .esquinas) ?? .ninguna
    }
}

public enum EstiloBoton: String, Sendable, Equatable, Decodable {
    case primario, secundario, discreto, destructivo

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "primario"
        self = Self(rawValue: raw) ?? .primario
    }
}

/// Convierte en dato lo que hoy está cableado en Swift (los botones de
/// `SocialDraftCardView` elegidos por `target.isPersonal`). `accion` es UNA
/// ``ChatAction`` del allowlist -- incluye `.unsupported` como caso
/// tolerante, así que una acción de una versión futura no tumba el botón ni
/// el nodo que lo contiene, solo lo deja sin pintar.
public struct BotonNode: Decodable, Sendable, Equatable {
    public let estilo: EstiloBoton
    public let accion: ChatAction

    private enum CodingKeys: String, CodingKey { case estilo, accion }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        estilo = try container.decodeIfPresent(EstiloBoton.self, forKey: .estilo) ?? .primario
        accion = try container.decode(ChatAction.self, forKey: .accion)
    }
}

public enum TonoBadge: String, Sendable, Equatable, Decodable {
    case neutro, acento, advertencia, exito

    public init(from decoder: Decoder) throws {
        let raw = (try? decoder.singleValueContainer().decode(String.self)) ?? "neutro"
        self = Self(rawValue: raw) ?? .neutro
    }
}

/// El badge demo/live verde/naranja/gris que hoy está fijo en Swift, como
/// dato: `tono` mapea a los colores de `FuenteBadge`.
public struct BadgeNode: Decodable, Sendable, Equatable {
    public let contenido: String
    public let tono: TonoBadge

    private enum CodingKeys: String, CodingKey { case contenido, tono }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        contenido = try container.decode(String.self, forKey: .contenido)
        tono = try container.decodeIfPresent(TonoBadge.self, forKey: .tono) ?? .neutro
    }
}

/// Una de las 7 primitivas, o ``desconocido`` como caso tolerante (nivel 1 de
/// forward-compat): un `nodo` que este cliente no reconoce todavía se
/// decodifica aquí en vez de invalidar el stack que lo contiene -- el
/// renderer (``CardGenericaView``) simplemente lo salta. `indirect` porque
/// `.stack` encierra un ``StackNode`` cuyos `hijos` son, de nuevo,
/// `NodoCard`.
public indirect enum NodoCard: Decodable, Sendable, Equatable {
    case stack(StackNode)
    case texto(TextoNode)
    case imagen(ImagenNode)
    case video(VideoNode)
    case boton(BotonNode)
    case badge(BadgeNode)
    case divisor
    case desconocido(nodo: String)

    private enum CodingKeys: String, CodingKey { case nodo }

    public init(from decoder: Decoder) throws {
        guard let container = try? decoder.container(keyedBy: CodingKeys.self),
              let tipoNodo = try? container.decode(String.self, forKey: .nodo)
        else {
            self = .desconocido(nodo: "invalido")
            return
        }
        switch tipoNodo {
        case "stack":
            if let value = try? StackNode(from: decoder) { self = .stack(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "texto":
            if let value = try? TextoNode(from: decoder) { self = .texto(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "imagen":
            if let value = try? ImagenNode(from: decoder) { self = .imagen(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "video":
            if let value = try? VideoNode(from: decoder) { self = .video(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "boton":
            if let value = try? BotonNode(from: decoder) { self = .boton(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "badge":
            if let value = try? BadgeNode(from: decoder) { self = .badge(value) }
            else { self = .desconocido(nodo: tipoNodo) }
        case "divisor":
            self = .divisor
        default:
            self = .desconocido(nodo: tipoNodo)
        }
    }
}

/// MVP de Server-Driven UI del chat: un árbol de hasta ``cardMaxProfundidad``
/// niveles y ``cardMaxNodos`` nodos. `fallbackText` es OBLIGATORIO (a
/// diferencia del resto de bloques, donde es opcional): es la red final de
/// un cliente que todavía no tiene este renderer (nivel 3 de forward-compat)
/// -- toda card emitida DEBE poder degradarse a una frase útil.
public struct GenericCardBlock: Decodable, Sendable, Equatable {
    public let schemaVersion: Int
    public let cardId: String
    public let raiz: StackNode
    public let fallbackText: String

    private enum CodingKeys: String, CodingKey {
        case schemaVersion = "schema_version"
        case cardId = "card_id"
        case raiz
        case fallbackText = "fallback_text"
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion) ?? 1
        cardId = try container.decodeIfPresent(String.self, forKey: .cardId) ?? ""
        fallbackText = try container.decode(String.self, forKey: .fallbackText)
        let raizDecodificada = try container.decode(StackNode.self, forKey: .raiz)
        var presupuesto = cardMaxNodos
        // El nodo raíz mismo cuenta contra el presupuesto de nodos.
        presupuesto -= 1
        var copia = raizDecodificada
        copia.hijos = Self.podados(copia.hijos, profundidad: 2, presupuesto: &presupuesto)
        raiz = copia
    }

    /// Defensa en profundidad del lado del cliente (además de la validación
    /// server-side en Pydantic, que ya rechaza cualquier card que exceda
    /// estos límites antes de emitirla): nunca confiar solo en el emisor.
    /// Poda SILENCIOSA y recursiva -- un exceso de nodos o de profundidad
    /// deja de pintarse a partir de ahí, nunca revienta la card entera.
    private static func podados(_ hijos: [NodoCard], profundidad: Int, presupuesto: inout Int) -> [NodoCard] {
        // Espejo de `GenericCardBlock.validate_limites` en `chat.py`: TODO nodo
        // (stack u hoja) por encima de `cardMaxProfundidad` se descarta, no
        // solo los stacks -- así que el corte es por nivel completo, antes de
        // mirar qué trae cada hijo.
        guard profundidad <= cardMaxProfundidad else { return [] }
        var resultado: [NodoCard] = []
        for hijo in hijos {
            guard presupuesto > 0 else { break }
            presupuesto -= 1
            switch hijo {
            case .stack(var contenedor):
                contenedor.hijos = podados(contenedor.hijos, profundidad: profundidad + 1, presupuesto: &presupuesto)
                resultado.append(.stack(contenedor))
            default:
                resultado.append(hijo)
            }
        }
        return resultado
    }
}

/// Bloque tipado y forward-compatible. Un bloque futuro o malformado se
/// vuelve ``unsupported`` y puede mostrar `fallback_text` sin tumbar el chat.
public enum ChatBlock: Decodable, Sendable, Equatable {
    case media(MediaBlock)
    case linkPreview(LinkPreviewBlock)
    case flight(FlightCardBlock)
    case hotel(HotelCardBlock)
    case socialDraft(SocialDraftBlock)
    case question(QuestionBlock)
    case card(GenericCardBlock)
    case gymCheckin(GymCheckinBlock)
    case chart(ChartBlock)
    case sources(SourcesBlock)
    case unsupported(type: String, fallbackText: String?)

    /// Versión más alta del contrato de bloques que este cliente sabe pintar.
    /// Antes el gate era `schemaVersion != 1`, que en la práctica se
    /// comportaba igual porque el backend actual solo emite `1`; queda
    /// escrito así para que subir esta constante sea una decisión consciente
    /// (nivel 3 de forward-compat del plan de SDUI), no un accidente.
    public static let versionMaximaSoportada = 1

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
        guard version <= Self.versionMaximaSoportada else {
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
        case "social_draft":
            if let value = try? SocialDraftBlock(from: decoder) { self = .socialDraft(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "question":
            if let value = try? QuestionBlock(from: decoder) { self = .question(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "card":
            if let value = try? GenericCardBlock(from: decoder) { self = .card(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "gym_checkin":
            if let value = try? GymCheckinBlock(from: decoder) { self = .gymCheckin(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "chart":
            if let value = try? ChartBlock(from: decoder) { self = .chart(value) }
            else { self = .unsupported(type: type, fallbackText: fallback) }
        case "sources":
            if let value = try? SourcesBlock(from: decoder) { self = .sources(value) }
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
