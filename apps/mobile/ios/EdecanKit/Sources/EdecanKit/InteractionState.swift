import Foundation

/// Identidad de un único mensaje lógico. Reintentar transporte conserva la
/// instancia; redactar/enviar otro mensaje crea una nueva.
public struct LogicalChatAttempt: Sendable, Equatable {
    public let idempotencyKey: UUID

    public init(idempotencyKey: UUID = UUID()) {
        self.idempotencyKey = idempotencyKey
    }

    public var headerValue: String {
        idempotencyKey.uuidString.lowercased()
    }
}

/// Estado mínimo de un turno que el servidor ya puede estar ejecutando aunque
/// iOS suspenda el socket SSE. Se guarda antes del primer POST y se elimina
/// únicamente después de rehidratar la fuente de verdad.
///
/// El prompt y los adjuntos nunca se escriben en este archivo: al volver de
/// una suspensión o un cierre de proceso, el cliente consulta el replay por
/// `idempotencyKey` sin volver a enviar el cuerpo.
public struct PendingChatAttempt: Codable, Sendable, Equatable {
    public static let currentSchemaVersion = 1

    public let schemaVersion: Int
    public let idempotencyKey: UUID
    public let conversationId: String
    public let localMessageId: String
    public let createdAt: Date

    public init(
        idempotencyKey: UUID,
        conversationId: String,
        localMessageId: String,
        createdAt: Date = Date()
    ) {
        schemaVersion = Self.currentSchemaVersion
        self.idempotencyKey = idempotencyKey
        self.conversationId = conversationId
        self.localMessageId = localMessageId
        self.createdAt = createdAt
    }

    /// El replay del backend dura 24 horas por defecto. El estado local nunca
    /// se considera recuperable por más tiempo que la fuente de verdad.
    public func isRecoverable(at date: Date = Date(), maximumAge: TimeInterval = 24 * 60 * 60) -> Bool {
        schemaVersion == Self.currentSchemaVersion
            && date.timeIntervalSince(createdAt) >= -60
            && date.timeIntervalSince(createdAt) <= maximumAge
            && !conversationId.isEmpty
            && !localMessageId.isEmpty
    }
}

/// Persistencia local de un único turno pendiente. El producto solo permite un
/// envío conversacional simultáneo, por lo que un archivo único evita carreras
/// y simplifica la limpieza al cambiar de cuenta/servidor.
///
/// En iOS se aplica `completeUntilFirstUserAuthentication`: los metadatos
/// quedan cifrados por Data Protection y siguen disponibles al volver a primer
/// plano después del primer desbloqueo. No se guarda el texto de transporte.
public struct PendingChatAttemptStore: Sendable {
    public enum StoreError: Error, LocalizedError, Sendable {
        case unavailable
        case unsupportedSchema

        public var errorDescription: String? {
            switch self {
            case .unavailable:
                return "No se pudo preparar la recuperación segura del mensaje."
            case .unsupportedSchema:
                return "El envío pendiente pertenece a una versión incompatible."
            }
        }
    }

    private let fileURL: URL

    public init(directoryURL: URL? = nil, fileManager: FileManager = .default) {
        let directory = directoryURL
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
                .appendingPathComponent("cc.edecan.mobile", isDirectory: true)
            ?? fileManager.temporaryDirectory.appendingPathComponent(
                "cc.edecan.mobile",
                isDirectory: true
            )
        fileURL = directory.appendingPathComponent("pending-chat-attempt.json", isDirectory: false)
    }

    public func load(fileManager: FileManager = .default) throws -> PendingChatAttempt? {
        guard fileManager.fileExists(atPath: fileURL.path) else { return nil }
        let data = try Data(contentsOf: fileURL)
        let pending = try JSONDecoder().decode(PendingChatAttempt.self, from: data)
        guard pending.schemaVersion == PendingChatAttempt.currentSchemaVersion else {
            throw StoreError.unsupportedSchema
        }
        return pending
    }

    public func save(
        _ pending: PendingChatAttempt,
        fileManager: FileManager = .default
    ) throws {
        let directory = fileURL.deletingLastPathComponent()
        do {
            try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
            let data = try JSONEncoder().encode(pending)
            try data.write(to: fileURL, options: .atomic)
#if os(iOS)
            // Data Protection is a hardening enhancement, not a prerequisite
            // for sending. Some simulator/device states reject this attribute
            // even after the atomic write succeeded. Never turn that benign
            // platform variation into a failed chat message.
            try? fileManager.setAttributes(
                [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                ofItemAtPath: fileURL.path
            )
#endif
        } catch {
            // Keep the last known-good recovery record. Removing it here can
            // erase a valid replay marker when a transient filesystem error
            // occurs during a replacement write.
            throw StoreError.unavailable
        }
    }

    public func clear(fileManager: FileManager = .default) {
        guard fileManager.fileExists(atPath: fileURL.path) else { return }
        try? fileManager.removeItem(at: fileURL)
    }
}

/// Guardia pequeña y determinista para una interacción push-to-talk.
/// Un permiso asíncrono solo puede iniciar audio si la misma presión continúa
/// activa; soltar, cancelar o comenzar otra presión invalida el token anterior.
public struct PushToTalkGate: Sendable, Equatable {
    public private(set) var generation: UInt64 = 0
    public private(set) var isPressed = false

    public init() {}

    @discardableResult
    public mutating func press() -> UInt64 {
        generation &+= 1
        isPressed = true
        return generation
    }

    public mutating func release() {
        isPressed = false
    }

    public mutating func cancel() {
        isPressed = false
        generation &+= 1
    }

    public func accepts(_ token: UInt64) -> Bool {
        isPressed && generation == token
    }

    public func isCurrent(_ token: UInt64) -> Bool {
        generation == token
    }
}

/// Único dueño de los borradores y del último hilo local. Todas sus claves
/// comparten prefijo para que un cierre/expiración pueda borrar el conjunto
/// completo sin tocar preferencias ajenas de la app.
public struct ChatLocalStateStore {
    public static let storagePrefix = "edecan.chat."

    private enum Key {
        static let currentConversation = "\(ChatLocalStateStore.storagePrefix)currentConversationId"
        static let principalConversation = "\(ChatLocalStateStore.storagePrefix)principalConversationId"
        static let draftPrefix = "\(ChatLocalStateStore.storagePrefix)draft."
        static let lastReadPrefix = "\(ChatLocalStateStore.storagePrefix)lastRead."
    }

    private let defaults: UserDefaults
    /// Borradores SOLO de la sesión en memoria: un texto que el dueño no
    /// mandó (p. ej. "Post de linkedin") NO debe perseguirlo cada vez que
    /// abre la app (bug reportado: reaparecía builds después). Los drafts
    /// se recuperan al cambiar de conversación dentro de la MISMA sesión;
    /// al cerrar la app mueren. La caja es una clase: los métodos del store
    /// no son `mutating` (el store se usa como `let`).
    private let draftsEnMemoria = BorradoresBox()

    public init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
    }

    public var currentConversationId: String? {
        get {
            guard let value = defaults.string(forKey: Key.currentConversation), !value.isEmpty else { return nil }
            return value
        }
        nonmutating set {
            if let newValue, !newValue.isEmpty {
                defaults.set(newValue, forKey: Key.currentConversation)
            } else {
                defaults.removeObject(forKey: Key.currentConversation)
            }
        }
    }

    /// Id de `GET /v1/conversations/main`. En frío el chat aterriza aquí
    /// (Frente 5) sin esperar ese round-trip si ya lo resolvimos antes.
    public var principalConversationId: String? {
        get {
            guard let value = defaults.string(forKey: Key.principalConversation), !value.isEmpty else { return nil }
            return value
        }
        nonmutating set {
            if let newValue, !newValue.isEmpty {
                defaults.set(newValue, forKey: Key.principalConversation)
            } else {
                defaults.removeObject(forKey: Key.principalConversation)
            }
        }
    }

    public func saveDraft(_ text: String, conversationId: String?) {
        let key = draftKey(conversationId)
        if text.isEmpty { draftsEnMemoria.dict.removeValue(forKey: key) }
        else { draftsEnMemoria.dict[key] = text }
    }

    public func draft(conversationId: String?) -> String {
        draftsEnMemoria.dict[draftKey(conversationId)] ?? ""
    }

    /// Marca de lectura local por conversación. Los avisos proactivos del
    /// compañero comparan `createdAt` del mensaje contra este valor.
    public func lastReadAt(conversationId: String) -> Date? {
        defaults.object(forKey: lastReadKey(conversationId)) as? Date
    }

    public func markRead(conversationId: String, at date: Date = Date()) {
        defaults.set(date, forKey: lastReadKey(conversationId))
    }

    public func clearAll() {
        draftsEnMemoria.dict.removeAll()
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.storagePrefix) {
            defaults.removeObject(forKey: key)
        }
    }

    private func draftKey(_ conversationId: String?) -> String {
        "\(Key.draftPrefix)\(conversationId ?? "new")"
    }

    /// Borra del disco los borradores persistidos por builds viejas (el
    /// "fantasma" que reaparecía). Se llama UNA vez por lanzamiento.
    public func purgarBorradoresPersistidos() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Key.draftPrefix) {
            defaults.removeObject(forKey: key)
        }
    }

    /// Caja por referencia de los borradores en memoria (ver `draftsEnMemoria`).
    private final class BorradoresBox {
        var dict: [String: String] = [:]
    }

    private func lastReadKey(_ conversationId: String) -> String {
        "\(Key.lastReadPrefix)\(conversationId)"
    }
}

/// Recorte del hilo para pintar al instante. El VPS sigue siendo la fuente
/// de verdad: esto solo evita 10 s de spinner Colombia↔Virginia. No guarda
/// confirmaciones pendientes ni tool_calls (pueden estar viejos o ser
/// peligrosos de rehidratar sin el GET).
public struct CachedChatMessage: Codable, Sendable, Equatable {
    public let id: String
    public let role: String
    public let text: String
    public let createdAt: Date?
    public let pinned: Bool
    public let bookmark: Bool

    public init(
        id: String,
        role: String,
        text: String,
        createdAt: Date? = nil,
        pinned: Bool = false,
        bookmark: Bool = false
    ) {
        self.id = id
        self.role = role
        self.text = String(text.prefix(8_000))
        self.createdAt = createdAt
        self.pinned = pinned
        self.bookmark = bookmark
    }
}

public struct CachedConversationSnapshot: Codable, Sendable, Equatable {
    public static let currentSchemaVersion = 1

    public let schemaVersion: Int
    public let conversationId: String
    public let title: String?
    public let isMain: Bool
    public let model: String?
    public let effort: String?
    public let savedAt: Date
    public let messages: [CachedChatMessage]

    public init(
        conversationId: String,
        title: String?,
        isMain: Bool,
        model: String?,
        effort: String?,
        savedAt: Date = Date(),
        messages: [CachedChatMessage]
    ) {
        schemaVersion = Self.currentSchemaVersion
        self.conversationId = conversationId
        self.title = title
        self.isMain = isMain
        self.model = model
        self.effort = effort
        self.savedAt = savedAt
        self.messages = Array(messages.suffix(50))
    }

    public func isUsable(at date: Date = Date(), maximumAge: TimeInterval = 30 * 24 * 60 * 60) -> Bool {
        schemaVersion == Self.currentSchemaVersion
            && !conversationId.isEmpty
            && date.timeIntervalSince(savedAt) >= -60
            && date.timeIntervalSince(savedAt) <= maximumAge
    }
}

/// Referencia a un adjunto persistido en el snapshot del bot (BOTS-21).
/// Se guarda SOLO la identidad (fileId/filename/mime); el contenido se
/// descarga al reconectar — nunca se cachea el binario en el snapshot.
public struct CachedBotAttachment: Codable, Sendable, Equatable, Identifiable {
    public let fileId: String
    public let filename: String?
    public let mime: String?

    public var id: String { fileId }

    public init(fileId: String, filename: String?, mime: String?) {
        self.fileId = fileId
        self.filename = filename
        self.mime = mime
    }
}

public struct CachedBotMessage: Codable, Sendable, Equatable {
    public let id: String
    public let esUsuario: Bool
    public let texto: String
    public let nombreRemitente: String?
    /// v2: referencias de adjuntos (sin contenido) para que el mensaje offline
    /// sea honesto sobre lo que falta descargar.
    public let adjuntos: [CachedBotAttachment]
    /// v2: `true` cuando el texto se recortó a 8_000 caracteres (entregable parcial).
    public let recortado: Bool

    public init(
        id: String,
        esUsuario: Bool,
        texto: String,
        nombreRemitente: String? = nil,
        adjuntos: [CachedBotAttachment] = [],
        recortado: Bool = false
    ) {
        self.id = id
        self.esUsuario = esUsuario
        self.recortado = recortado || texto.count > 8_000
        self.texto = String(texto.prefix(8_000))
        self.nombreRemitente = nombreRemitente
        self.adjuntos = adjuntos
    }

    /// Decodificación tolerante: los snapshots v1 no traen `adjuntos` ni
    /// `recortado` — se leen como vacío/false y siguen pintándose.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        esUsuario = try container.decode(Bool.self, forKey: .esUsuario)
        texto = (try? container.decode(String.self, forKey: .texto)) ?? ""
        nombreRemitente = try container.decodeIfPresent(String.self, forKey: .nombreRemitente)
        adjuntos = (try? container.decode([CachedBotAttachment].self, forKey: .adjuntos)) ?? []
        recortado = (try? container.decode(Bool.self, forKey: .recortado)) ?? false
    }

    private enum CodingKeys: String, CodingKey {
        case id, esUsuario, texto, nombreRemitente, adjuntos, recortado
    }
}

public struct CachedBotThread: Codable, Sendable, Equatable {
    public static let currentSchemaVersion = 2
    public static let minimumSchemaVersion = 1

    public let schemaVersion: Int
    public let workerId: String
    public let savedAt: Date
    public let messages: [CachedBotMessage]
    /// v2: epoch de conversación que manda el servidor (numérico, opcional).
    /// Un snapshot con epoch viejo no se repinta si ya conocemos uno más reciente.
    public let conversationEpoch: Int64?

    public init(
        workerId: String,
        savedAt: Date = Date(),
        messages: [CachedBotMessage],
        conversationEpoch: Int64? = nil
    ) {
        schemaVersion = Self.currentSchemaVersion
        self.workerId = workerId
        self.savedAt = savedAt
        self.messages = Array(messages.suffix(50))
        self.conversationEpoch = conversationEpoch
    }

    /// Decodificación tolerante: snapshots v1 (sin `conversationEpoch`) se leen
    /// igual; solo se exige `workerId`.
    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        schemaVersion = (try? container.decode(Int.self, forKey: .schemaVersion)) ?? Self.minimumSchemaVersion
        workerId = try container.decode(String.self, forKey: .workerId)
        savedAt = (try? container.decode(Date.self, forKey: .savedAt)) ?? Date()
        messages = (try? container.decode([CachedBotMessage].self, forKey: .messages)) ?? []
        conversationEpoch = try container.decodeIfPresent(Int64.self, forKey: .conversationEpoch)
    }

    public func isUsable(at date: Date = Date(), maximumAge: TimeInterval = 30 * 24 * 60 * 60) -> Bool {
        (Self.minimumSchemaVersion...Self.currentSchemaVersion).contains(schemaVersion)
            && !workerId.isEmpty
            && date.timeIntervalSince(savedAt) >= -60
            && date.timeIntervalSince(savedAt) <= maximumAge
    }

    private enum CodingKeys: String, CodingKey {
        case schemaVersion, workerId, savedAt, messages, conversationEpoch
    }
}

/// Snapshots de hilo en Application Support. Un archivo por conversación o
/// worker; `clearAll` al cambiar de cuenta/servidor.
public struct ConversationSnapshotStore: Sendable {
    private let directoryURL: URL

    public init(directoryURL: URL? = nil, fileManager: FileManager = .default) {
        self.directoryURL = directoryURL
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
                .appendingPathComponent("cc.edecan.mobile/chat-snapshots", isDirectory: true)
            ?? fileManager.temporaryDirectory.appendingPathComponent(
                "cc.edecan.mobile/chat-snapshots",
                isDirectory: true
            )
    }

    public func load(conversationId: String, fileManager: FileManager = .default) -> CachedConversationSnapshot? {
        guard let url = fileURL(for: conversationId),
              fileManager.fileExists(atPath: url.path),
              let data = try? Data(contentsOf: url),
              let snap = try? Self.decoder().decode(CachedConversationSnapshot.self, from: data),
              snap.conversationId == conversationId,
              snap.isUsable()
        else { return nil }
        return snap
    }

    public func save(_ snapshot: CachedConversationSnapshot, fileManager: FileManager = .default) {
        guard let url = fileURL(for: snapshot.conversationId) else { return }
        do {
            try fileManager.createDirectory(at: directoryURL, withIntermediateDirectories: true)
            let data = try Self.encoder().encode(snapshot)
            try data.write(to: url, options: .atomic)
            Self.aplicarProteccion(url, fileManager: fileManager)
        } catch {
            // Un fallo de disco no debe tumbar el chat: el GET del VPS sigue.
        }
    }

    public func remove(conversationId: String, fileManager: FileManager = .default) {
        guard let url = fileURL(for: conversationId) else { return }
        try? fileManager.removeItem(at: url)
    }

    public func clearAll(fileManager: FileManager = .default) {
        guard fileManager.fileExists(atPath: directoryURL.path) else { return }
        try? fileManager.removeItem(at: directoryURL)
    }

    private func fileURL(for conversationId: String) -> URL? {
        guard let nombre = Self.nombreSeguro(conversationId) else { return nil }
        return directoryURL.appendingPathComponent("\(nombre).json", isDirectory: false)
    }

    static func nombreSeguro(_ id: String) -> String? {
        let trimmed = id.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, trimmed.count <= 80,
              trimmed.allSatisfy({ $0.isHexDigit || $0 == "-" })
        else { return nil }
        return trimmed
    }

    private static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    private static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }

    fileprivate static func aplicarProteccion(_ url: URL, fileManager: FileManager) {
#if os(iOS)
        try? fileManager.setAttributes(
            [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
            ofItemAtPath: url.path
        )
#endif
    }
}

public struct BotChatSnapshotStore: Sendable {
    private let directoryURL: URL

    public init(directoryURL: URL? = nil, fileManager: FileManager = .default) {
        self.directoryURL = directoryURL
            ?? fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
                .appendingPathComponent("cc.edecan.mobile/bot-chat-snapshots", isDirectory: true)
            ?? fileManager.temporaryDirectory.appendingPathComponent(
                "cc.edecan.mobile/bot-chat-snapshots",
                isDirectory: true
            )
    }

    public func load(workerId: String, fileManager: FileManager = .default) -> CachedBotThread? {
        guard let url = fileURL(for: workerId),
              fileManager.fileExists(atPath: url.path),
              let data = try? Data(contentsOf: url),
              let snap = try? Self.decoder().decode(CachedBotThread.self, from: data),
              snap.workerId == workerId,
              snap.isUsable()
        else { return nil }
        return snap
    }

    public func save(_ snapshot: CachedBotThread, fileManager: FileManager = .default) {
        guard let url = fileURL(for: snapshot.workerId) else { return }
        do {
            try fileManager.createDirectory(at: directoryURL, withIntermediateDirectories: true)
            let data = try Self.encoder().encode(snapshot)
            try data.write(to: url, options: .atomic)
            ConversationSnapshotStore.aplicarProteccion(url, fileManager: fileManager)
        } catch {}
    }

    public func clearAll(fileManager: FileManager = .default) {
        guard fileManager.fileExists(atPath: directoryURL.path) else { return }
        try? fileManager.removeItem(at: directoryURL)
    }

    /// BOTS-09: invalida el snapshot de UN worker (tras `/clear` o DELETE
    /// exitoso en el servidor). Borrar el archivo evita que un cold open
    /// offline repinte el chat que el servidor ya vació/eliminó.
    public func remove(workerId: String, fileManager: FileManager = .default) {
        guard let url = fileURL(for: workerId) else { return }
        try? fileManager.removeItem(at: url)
    }

    private func fileURL(for workerId: String) -> URL? {
        guard let nombre = ConversationSnapshotStore.nombreSeguro(workerId) else { return nil }
        return directoryURL.appendingPathComponent("\(nombre).json", isDirectory: false)
    }

    private static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }

    private static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return decoder
    }
}
