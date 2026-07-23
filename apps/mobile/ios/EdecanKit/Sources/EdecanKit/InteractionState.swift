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
            try fileManager.setAttributes(
                [.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication],
                ofItemAtPath: fileURL.path
            )
#endif
        } catch {
            try? fileManager.removeItem(at: fileURL)
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
        static let draftPrefix = "\(ChatLocalStateStore.storagePrefix)draft."
    }

    private let defaults: UserDefaults

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

    public func saveDraft(_ text: String, conversationId: String?) {
        let key = draftKey(conversationId)
        if text.isEmpty { defaults.removeObject(forKey: key) }
        else { defaults.set(text, forKey: key) }
    }

    public func draft(conversationId: String?) -> String {
        defaults.string(forKey: draftKey(conversationId)) ?? ""
    }

    public func clearAll() {
        for key in defaults.dictionaryRepresentation().keys where key.hasPrefix(Self.storagePrefix) {
            defaults.removeObject(forKey: key)
        }
    }

    private func draftKey(_ conversationId: String?) -> String {
        "\(Key.draftPrefix)\(conversationId ?? "new")"
    }
}
